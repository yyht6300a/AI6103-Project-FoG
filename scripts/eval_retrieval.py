# -*- coding: utf-8 -*-
"""Run full FSE -> FFP inference on test questions and report retrieval metrics.

Metrics (paper Sec 4.5):
    R_evid   gold-edge recall kept by the expansion threshold tau_e
    P_prune  fraction of noise edges pruned by tau_e
    H_path   fraction of questions whose complete gold path survives

Usage:
    python scripts/eval_retrieval.py --dataset webqsp \
        --checkpoint checkpoints/webqsp/epoch5.pt \
        --ffp-checkpoint checkpoints/ffp_webqsp/checkpoint_epoch4.pt \
        --limit 200
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.model.embedding import EmbeddingStore  # noqa: E402
from src.model.ffp import ForesightFeedbackPropagation  # noqa: E402
from src.model.infer import run_fse_ffp  # noqa: E402
from src.model.kg_bridge import (  # noqa: E402
    DEFAULT_INDEX_DIR,
    DEFAULT_PROCESSED_ROOT,
    ensure_fog_importable,
    load_kg,
)
from src.model.scorer import ForesightTripleScorer  # noqa: E402
from src.model.state_provider import build_subgraph_from_record  # noqa: E402


def load_test_records(processed_root, dataset, split="test"):
    path = Path(processed_root) / dataset / f"qa_{split}.jsonl.gz"
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True, choices=["webqsp", "cwq"])
    ap.add_argument("--checkpoint", required=True, help="pretrained scorer")
    ap.add_argument("--ffp-checkpoint", required=True, help="trained FFP checkpoint")
    ap.add_argument("--cache", default="cache/embeddings_v2")
    ap.add_argument("--index-dir", default=str(DEFAULT_INDEX_DIR))
    ap.add_argument("--processed-root", default=str(DEFAULT_PROCESSED_ROOT))
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--max-nodes", type=int, default=8000)
    ap.add_argument("--max-edges", type=int, default=10000)
    ap.add_argument("--tau-e", type=float, default=0.1)
    ap.add_argument("--tau-lo", type=float, default=None, help="depth-scheduled threshold at layer 1")
    ap.add_argument("--tau-hi", type=float, default=None, help="depth-scheduled threshold at layer L")
    ap.add_argument("--beta", type=float, default=None, help="degree-normalization z-score (mu + beta*sigma)")
    ap.add_argument("--fixed-tau", action="store_true",
                    help="use fixed tau_e instead of the default depth schedule")
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = ap.parse_args()

    device = torch.device(args.device)
    kg = load_kg(args.index_dir)
    store = EmbeddingStore.load(args.cache)
    store.validate_index(kg)

    scorer = ForesightTripleScorer.load(args.checkpoint).to(device).eval()
    ckpt = torch.load(args.ffp_checkpoint, map_location=device, weights_only=False)
    if "scorer_state" in ckpt:
        scorer.load_state_dict(ckpt["scorer_state"])
    ffp_cfg = ckpt.get("ffp_config") or {}
    ffp = ForesightFeedbackPropagation(
        ffp_cfg.get("dim", scorer.d), num_layers=ffp_cfg.get("num_layers", 2),
    ).to(device).eval()
    ffp.load_state_dict(ckpt["ffp_state"])
    scorer.eval()

    ensure_fog_importable()
    from fog.subgraph import SubgraphConfig
    sub_cfg = SubgraphConfig(hops=3, node_degree_max=20000, rel_degree_max=200_000,
                             max_nodes=args.max_nodes, max_edges=args.max_edges)

    records = [r for r in load_test_records(args.processed_root, args.dataset)
               if (r.get("gold") or {}).get("matched") and r.get("source_entities")]
    records = records[: args.limit]
    if args.fixed_tau:
        th_desc = f"fixed tau_e={args.tau_e}"
    elif args.beta is not None:
        th_desc = f"degree beta={args.beta}"
    else:
        lo = 0.02 if args.tau_lo is None else args.tau_lo
        hi = 0.10 if args.tau_hi is None else args.tau_hi
        th_desc = f"depth tau_lo={lo}, tau_hi={hi}"
    print(f"test questions: {len(records)} ({th_desc})")

    evid, prune, path = [], [], []
    t0 = time.time()
    for r in records:
        sub = build_subgraph_from_record(kg, r, sub_cfg)
        if sub is None or not getattr(sub, "edges", None):
            continue
        kwargs = dict(tau_e=args.tau_e, device=device)
        if args.beta is not None:
            kwargs["beta"] = args.beta
        elif args.fixed_tau:
            kwargs["tau_lo"] = None
            kwargs["tau_hi"] = None
        elif args.tau_lo is not None:
            kwargs["tau_lo"] = args.tau_lo
            kwargs["tau_hi"] = args.tau_hi
        # else: use run_fse_ffp's default depth schedule (0.02 / 0.10)
        out = run_fse_ffp(scorer, ffp, store, kg, sub, r["question"], **kwargs)
        keep = out.keep
        gold = [bool(e.gold) for e in sub.edges]
        n_gold = sum(gold)
        if n_gold == 0:
            continue
        kept_gold = sum(k and g for k, g in zip(keep, gold))
        evid.append(kept_gold / n_gold)
        path.append(1.0 if kept_gold == n_gold else 0.0)
        n_noise = len(gold) - n_gold
        if n_noise:
            kept_noise = sum(k and not g for k, g in zip(keep, gold))
            prune.append(1.0 - kept_noise / n_noise)

    n = max(len(evid), 1)
    print(f"R_evid ={sum(evid)/n:.3f}")
    print(f"P_prune={sum(prune)/max(len(prune),1):.3f}")
    print(f"H_path ={sum(path)/n:.3f}")
    print(f"questions evaluated: {n} ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
