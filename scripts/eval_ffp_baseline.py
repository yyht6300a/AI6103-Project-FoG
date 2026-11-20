# -*- coding: utf-8 -*-
"""Compare zero-state (no FFP) vs FFP-state edge metrics on the same eval split.

Answers the paper's core question: does far-to-near feedback improve edge-level
gold recall (recall@tau_e / recall@tau_p) and AUC over the Initial Scorer?

The "no FFP" column evaluates the pretrained Initial Scorer with zero states.
The "with FFP" column loads the trained checkpoint (scorer + FFP) and evaluates
with the FFP state provider. For a frozen-scorer checkpoint the scorer stays at
the pretrained weights; for a joint checkpoint the fine-tuned scorer is loaded.

Usage:
    python scripts/eval_ffp_baseline.py --dataset webqsp \
        --checkpoint checkpoints/webqsp/epoch5.pt \
        --ffp-checkpoint checkpoints/ffp_webqsp/checkpoint_epoch4.pt \
        --limit-eval 300
"""

from __future__ import annotations

import argparse
import sys
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
from src.model.state_provider import FfpStateProvider, make_subgraph_loader  # noqa: E402
from src.model.training import evaluate, split_qa_records  # noqa: E402

KEYS = ("auc", "tau_e_recall", "tau_e_kept_frac", "tau_p_recall")


def build_examples(builder, records, limit=None):
    out = []
    for rec in records:
        if limit is not None and len(out) >= limit:
            break
        ex = builder.build(rec)
        if ex is not None:
            out.append(ex)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True, choices=["webqsp", "cwq"])
    ap.add_argument("--checkpoint", required=True, help="pretrained scorer (no-FFP baseline)")
    ap.add_argument("--ffp-checkpoint", required=True, help="trained FFP checkpoint")
    ap.add_argument("--cache", default="cache/embeddings_v2")
    ap.add_argument("--index-dir", default=str(DEFAULT_INDEX_DIR))
    ap.add_argument("--processed-root", default=str(DEFAULT_PROCESSED_ROOT))
    ap.add_argument("--limit-eval", type=int, default=None)
    ap.add_argument("--max-nodes", type=int, default=8000)
    ap.add_argument("--max-edges", type=int, default=10000)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = ap.parse_args()

    device = torch.device(args.device)
    kg = load_kg(args.index_dir)
    store = EmbeddingStore.load(args.cache)
    store.validate_index(kg)

    records = iter_qa_records(args.processed_root, args.dataset, "train")
    _, eval_records = split_qa_records(records, validation_records=None, seed=0)
    builder = TrainingRegionBuilder(kg, SamplerConfig(kappa=10, seed=0))
    eval_ex = build_examples(builder, eval_records, args.limit_eval)
    print(f"eval questions: {len(eval_ex)}")

    scorer = ForesightTripleScorer.load(args.checkpoint).to(device).eval()
    base = evaluate(scorer, eval_ex, store, device, state_provider=None)
    print("no-FFP   :", {k: round(base[k], 4) for k in KEYS if k in base})

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
    records_by_qid = {r["qid"]: r for r in eval_records}
    subgraph_loader = make_subgraph_loader(kg, records_by_qid, sub_cfg)
    provider = FfpStateProvider(scorer, ffp, store, kg, subgraph_loader, device)
    ffp_res = evaluate(scorer, eval_ex, store, device, state_provider=provider)
    print("with-FFP :", {k: round(ffp_res[k], 4) for k in KEYS if k in ffp_res})


if __name__ == "__main__":
    main()

