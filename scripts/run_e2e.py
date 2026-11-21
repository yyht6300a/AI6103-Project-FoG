# -*- coding: utf-8 -*-
"""Minimal end-to-end driver: A -> B -> C -> D -> E, one retrieval round.

Wires member A's subgraph, member B's scorer, member C's FSE + FFP, member D's
memory management + LLM, and member E's Hit / Hit@1 / F1 metrics into a single
reasoning round per question.

This is the smallest runnable end-to-end path. Multi-round re-retrieval and
question decomposition are the next extension; the single round already
exercises every interface (A->B->C->D->E) and produces a first Hit number.

Requirements before running:
    * LLM settings in config/config-dev.yml (model_name / base_url / timeout /
      max_retries) and a local .env with LLM_API_KEY.
    * Member A's KG index + processed QA files, member B's embedding cache, and
      the pretrained scorer plus the FFP checkpoint.

Usage:
    python scripts/run_e2e.py --dataset webqsp \
        --checkpoint checkpoints/webqsp/epoch5.pt \
        --ffp-checkpoint checkpoints/ffp_webqsp/checkpoint_epoch4.pt \
        --limit 50
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

from experiments.e2e import end_to_end_metrics  # noqa: E402
from src.agent.llm_client import LLMClient  # noqa: E402
from src.agent.llm_config import load_llm_config  # noqa: E402
from src.agent.memory_management import manage_memory  # noqa: E402
from src.agent.memory_state import QuestionMemory  # noqa: E402
from src.model.d_adapter import fse_ffp_to_potential  # noqa: E402
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
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--max-nodes", type=int, default=8000)
    ap.add_argument("--max-edges", type=int, default=10000)
    ap.add_argument("--tau-p", type=float, default=0.7)
    ap.add_argument("--tau-lo", type=float, default=0.02)
    ap.add_argument("--tau-hi", type=float, default=0.10)
    ap.add_argument("--prompt-variant", default="evidence_decision")
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
    print(f"e2e questions: {len(records)}")

    config = load_llm_config()
    print(f"llm: {config.model_name} @ {config.base_url}")
    client = LLMClient(config)

    hit_sum = hit1_sum = f1_sum = n = errors = 0
    t0 = time.time()
    try:
        for i, rec in enumerate(records):
            try:
                sub = build_subgraph_from_record(kg, rec, sub_cfg)
                if sub is None or not getattr(sub, "edges", None):
                    continue
                out = run_fse_ffp(scorer, ffp, store, kg, sub, rec["question"],
                                  tau_lo=args.tau_lo, tau_hi=args.tau_hi, device=device)
                triples, scores = fse_ffp_to_potential(out, sub)
                state = QuestionMemory(rec["question"])
                result = manage_memory(state, triples, scores, client,
                                       potential_threshold=args.tau_p,
                                       prompt_variant=args.prompt_variant)
                predicted = list(result.response.predict_answer)
                m = end_to_end_metrics(predicted, rec["answers"])
                hit_sum += m["hit"]; hit1_sum += m["hit@1"]; f1_sum += m["f1"]; n += 1
            except Exception as exc:  # noqa: BLE001 - one bad question must not abort the run
                errors += 1
                print(f"[{i}] {rec['qid']}: {type(exc).__name__}", flush=True)
            if (i + 1) % 10 == 0:
                print(f"[{i+1}/{len(records)}] hit={hit_sum/max(n,1):.3f} "
                      f"hit@1={hit1_sum/max(n,1):.3f} f1={f1_sum/max(n,1):.3f}", flush=True)
    finally:
        client.close()

    print(f"\n=== E2E results (n={n}, errors={errors}) ===")
    print(f"Hit   = {hit_sum/max(n,1):.4f}")
    print(f"Hit@1 = {hit1_sum/max(n,1):.4f}")
    print(f"F1    = {f1_sum/max(n,1):.4f}")
    print(f"time {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
