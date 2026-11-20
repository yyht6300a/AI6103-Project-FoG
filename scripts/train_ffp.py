# -*- coding: utf-8 -*-
"""Train FFP (far-to-near feedback) on one dataset, reusing member B's training loop.

Loads the pretrained Initial Scorer, adds the FFP module, and trains with real FFP
states via the state_provider so BCE is computed on FFP-refined scores.

Memory notes:
    --freeze-scorer (default): only MLP_m is trainable; scorer runs forward-only.
    --max-edges 8000: keeps the full-subgraph encode inside an 8 GB laptop GPU.
    Full joint training (--no-freeze-scorer) with 40k-edge subgraphs is likely to OOM.

Checkpoint / resume:
    Every epoch writes checkpoint_epoch<k>.pt with scorer + FFP + optimizer + epoch.
    Resume with --resume checkpoints/ffp_webqsp/checkpoint_epoch3.pt to continue
    from epoch 4, restoring scorer, FFP, and optimizer state.

Usage (from repository root):
    python scripts/train_ffp.py --dataset webqsp --checkpoint checkpoints/webqsp/epoch5.pt \
        --out-dir checkpoints/ffp_webqsp --limit-train 200 --max-edges 8000
    python scripts/train_ffp.py ... --resume checkpoints/ffp_webqsp/checkpoint_epoch3.pt
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from dataclasses import asdict
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.model.embedding import EmbeddingStore  # noqa: E402
from src.model.ffp import ForesightFeedbackPropagation  # noqa: E402
from src.model.kg_bridge import (  # noqa: E402
    DEFAULT_INDEX_DIR,
    DEFAULT_PROCESSED_ROOT,
    ensure_fog_importable,
    load_kg,
)
from src.model.sampler import SamplerConfig, TrainingRegionBuilder, iter_qa_records  # noqa: E402
from src.model.scorer import ForesightTripleScorer  # noqa: E402
from src.model.state_provider import make_subgraph_loader, train_with_ffp  # noqa: E402
from src.model.training import TrainConfig, split_qa_records  # noqa: E402


def report_compute() -> str:
    lines = [f"python={platform.python_version()} torch={torch.__version__}"]
    if torch.cuda.is_available():
        p = torch.cuda.get_device_properties(0)
        lines.append(f"gpu={p.name} vram={p.total_memory/1024**3:.1f}GB")
    else:
        lines.append("gpu=none")
    lines.append(f"cpu_threads={torch.get_num_threads()}")
    return " | ".join(lines)


def build_examples(builder, records, limit=None):
    out = []
    for rec in records:
        if limit is not None and len(out) >= limit:
            break
        ex = builder.build(rec)
        if ex is not None:
            out.append(ex)
    return out


def ffp_config(scorer: ForesightTripleScorer, args) -> dict:
    return {"dim": scorer.d, "num_layers": args.ffp_layers}


def save_checkpoint(path, scorer, ffp, optimizer, epoch, train_cfg, args) -> None:
    """Full training checkpoint: scorer + FFP + optimizer + epoch (resumable)."""
    payload = {
        "epoch": epoch,
        "ffp_config": ffp_config(scorer, args),
        "ffp_state": ffp.state_dict(),
        "optimizer_state": optimizer.state_dict(),
        "train_config": asdict(train_cfg),
        "freeze_scorer": args.freeze_scorer,
        "dataset": args.dataset,
    }
    if not args.freeze_scorer:
        payload["scorer_config"] = asdict(scorer.config)
        payload["scorer_state"] = scorer.state_dict()
    torch.save(payload, path)


def save_final(path, scorer, ffp, args) -> None:
    """Inference-ready weights: scorer + FFP, no optimizer state."""
    payload = {
        "ffp_config": ffp_config(scorer, args),
        "ffp_state": ffp.state_dict(),
        "freeze_scorer": args.freeze_scorer,
        "dataset": args.dataset,
    }
    if not args.freeze_scorer:
        payload["scorer_config"] = asdict(scorer.config)
        payload["scorer_state"] = scorer.state_dict()
    torch.save(payload, path)


def load_checkpoint(path, device) -> dict:
    return torch.load(path, map_location=device, weights_only=False)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True, choices=["webqsp", "cwq"])
    ap.add_argument("--checkpoint", required=True, help="pretrained scorer, e.g. checkpoints/webqsp/epoch5.pt")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--cache", default="cache/embeddings_v2")
    ap.add_argument("--index-dir", default=str(DEFAULT_INDEX_DIR))
    ap.add_argument("--processed-root", default=str(DEFAULT_PROCESSED_ROOT))
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--limit-train", type=int, default=None)
    ap.add_argument("--limit-eval", type=int, default=None)
    ap.add_argument("--epochs", type=int, default=5)
    ap.add_argument("--lr", type=float, default=1e-5)
    ap.add_argument("--batch-questions", type=int, default=8)
    ap.add_argument("--max-triples-per-forward", type=int, default=64)
    ap.add_argument("--ffp-layers", type=int, default=2)
    ap.add_argument("--node-degree-max", type=int, default=20000, help="A's calibrated value")
    ap.add_argument("--max-nodes", type=int, default=10000)
    ap.add_argument("--max-edges", type=int, default=8000, help="subgraph edge cap (memory knob)")
    ap.add_argument("--freeze-scorer", action="store_true", default=True, help="freeze scorer, train MLP_m only")
    ap.add_argument("--no-freeze-scorer", action="store_false", dest="freeze_scorer")
    ap.add_argument("--no-amp", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--resume", type=str, default=None,
                    help="resume from a checkpoint_epoch<k>.pt; continue from epoch k+1")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    print(f"[compute] {report_compute()}")

    device = torch.device(args.device)
    kg = load_kg(args.index_dir)
    store = EmbeddingStore.load(args.cache)
    store.validate_index(kg)

    scorer = ForesightTripleScorer.load(args.checkpoint).to(device)
    if args.freeze_scorer:
        scorer.requires_grad_(False)
        scorer.eval()
        print("[mode] freeze scorer; train FFP MLP_m only")
    else:
        print("[mode] joint train scorer + FFP (needs more VRAM)")

    ffp = ForesightFeedbackPropagation(scorer.d, num_layers=args.ffp_layers).to(device)
    start_epoch, optimizer_state = 1, None
    if args.resume:
        ckpt = load_checkpoint(args.resume, device)
        if "scorer_state" in ckpt:
            scorer.load_state_dict(ckpt["scorer_state"])
        ffp_cfg = ckpt.get("ffp_config") or {}
        if ffp_cfg.get("num_layers", args.ffp_layers) != args.ffp_layers:
            ffp = ForesightFeedbackPropagation(ffp_cfg["dim"], num_layers=ffp_cfg["num_layers"]).to(device)
        ffp.load_state_dict(ckpt["ffp_state"])
        start_epoch = int(ckpt["epoch"]) + 1
        optimizer_state = ckpt.get("optimizer_state")
        print(f"[resume] from {args.resume}: trained epochs 1..{ckpt['epoch']}, "
              f"continue at epoch {start_epoch}")
        if start_epoch > args.epochs:
            raise SystemExit(f"resume epoch {ckpt['epoch']} >= --epochs {args.epochs}; nothing left to train")
    print(f"[ffp] num_layers={args.ffp_layers}, trainable={sum(p.numel() for p in ffp.parameters()):,}")

    records = iter_qa_records(args.processed_root, args.dataset, "train")
    train_records, eval_records = split_qa_records(records, validation_records=None, seed=args.seed)
    builder = TrainingRegionBuilder(kg, SamplerConfig(kappa=10, seed=args.seed))
    train_ex = build_examples(builder, train_records, args.limit_train)
    eval_ex = build_examples(builder, eval_records, args.limit_eval)
    if not eval_ex:
        raise SystemExit("validation contains no usable examples")
    print(f"[data] train={len(train_ex)} questions, eval={len(eval_ex)} ({time.time()-t0:.0f}s)")

    missing = {t for ex in train_ex + eval_ex for t in [ex.question, *ex.sub_questions]
               if not store.has_query(t)}
    if missing:
        raise SystemExit(f"{len(missing):,} queries missing from cache; rerun "
                         f"build_embedding_cache.py --parts queries --datasets {args.dataset}")

    ensure_fog_importable()
    from fog.subgraph import SubgraphConfig
    sub_cfg = SubgraphConfig(hops=3, node_degree_max=args.node_degree_max, rel_degree_max=200_000,
                             max_nodes=args.max_nodes, max_edges=args.max_edges)
    records_by_qid = {r["qid"]: r for r in train_records + eval_records}
    subgraph_loader = make_subgraph_loader(kg, records_by_qid, sub_cfg)

    train_cfg = TrainConfig(epochs=args.epochs, lr=args.lr,
                            batch_questions=args.batch_questions,
                            max_triples_per_forward=args.max_triples_per_forward,
                            amp=not args.no_amp, seed=args.seed)

    def on_epoch_end(epoch, metrics, optimizer):
        save_checkpoint(out_dir / f"checkpoint_epoch{epoch}.pt", scorer, ffp, optimizer,
                        epoch, train_cfg, args)
        with open(out_dir / "metrics.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(metrics) + "\n")

    (out_dir / "config.json").write_text(json.dumps({
        "dataset": args.dataset, "checkpoint": args.checkpoint,
        "freeze_scorer": args.freeze_scorer, "ffp_layers": args.ffp_layers,
        "node_degree_max": args.node_degree_max, "max_nodes": args.max_nodes,
        "max_edges": args.max_edges, "resume": args.resume,
        "start_epoch": start_epoch, "compute": report_compute(), "seed": args.seed}, indent=2))

    train_with_ffp(scorer, ffp, train_ex, store, kg, subgraph_loader, device,
                   config=train_cfg, eval_examples=eval_ex, on_epoch_end=on_epoch_end,
                   start_epoch=start_epoch, optimizer_state=optimizer_state)
    save_final(out_dir / "checkpoint_final.pt", scorer, ffp, args)
    print(f"done in {time.time()-t0:.0f}s -> {out_dir}")


if __name__ == "__main__":
    main()
