# -*- coding: utf-8 -*-
"""Single-question smoke test: A subgraph -> B scorer -> C scoring/pruning.

Verifies the real chain without training FFP:
    TripleIndex + EmbeddingStore + ForesightTripleScorer (WebQSP checkpoint)
        -> build_subgraph -> subgraph_to_batch -> scorer.encode/prob -> fse_expand

Reports the score gap between gold and noise edges and the gold recall kept by
the expansion threshold tau_e.
"""

from __future__ import annotations

import gzip
import json
import os
import sys
import time

import torch

ROOT = r"D:\Repo\03-Project"
sys.path.insert(0, ROOT)
torch.set_num_threads(max(1, os.cpu_count() or 1))

from src.model.kg_bridge import load_kg  # noqa: E402
from src.model.embedding import EmbeddingStore  # noqa: E402
from src.model.scorer import ForesightTripleScorer, pack_sub_questions, sub_question_set  # noqa: E402
from src.model.fse import fse_expand, subgraph_to_batch  # noqa: E402
from src.model.state_provider import build_subgraph_from_record  # noqa: E402
from src.model.kg_bridge import ensure_fog_importable  # noqa: E402


def load_records(path: str) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def main() -> None:
    t0 = time.time()
    kg = load_kg()
    print(f"[ok] KG index: {kg.n_triples:,} triples / {kg.n_entities:,} entities / "
          f"{kg.n_relations:,} relations ({time.time()-t0:.1f}s)")

    store = EmbeddingStore.load(ROOT + r"\cache\embeddings_v2")
    store.validate_index(kg)
    print(f"[ok] embedding cache: dim={store.dim}, entities={store.entity_emb.shape[0]:,}, "
          f"relations={store.relation_emb.shape[0]:,}; matches KG index")

    scorer = ForesightTripleScorer.load(ROOT + r"\checkpoints\webqsp\epoch5.pt")
    scorer.eval()
    print(f"[ok] scorer loaded: {scorer.num_parameters():,} trainable params "
          f"({time.time()-t0:.1f}s)")

    ensure_fog_importable()
    from fog.subgraph import SubgraphConfig

    # Smaller subgraph keeps the smoke test fast and light; gold path is near the source.
    cfg = SubgraphConfig(hops=3, node_degree_max=20000, rel_degree_max=200_000,
                         max_nodes=10000, max_edges=8000)

    records = load_records(ROOT + r"\data_knowledge_graph\data\processed\webqsp\qa_test.jsonl.gz")
    record = next(r for r in records if store.has_query(r["question"]))
    print(f"\n[question] {record['question']!r}  (qid={record['qid']})")

    sub = build_subgraph_from_record(kg, record, cfg)
    n_gold = int(sub.stats.get("n_gold_edges", 0))
    print(f"[subgraph] nodes={len(sub.nodes)}, edges={len(sub.edges)}, "
          f"gold_edges={n_gold}, gold_recall_in_pool={sub.stats.get('gold_recall_in_subgraph')}")

    q_ori = store.encode_questions([record["question"]])
    batch = subgraph_to_batch(sub, store, kg, q_ori)
    z = scorer.encode(batch)
    q_sub, q_mask = pack_sub_questions(
        [store.encode_questions(sub_question_set(record["question"], []))])
    q_index = torch.zeros(len(sub.edges), dtype=torch.long)
    prob = scorer.prob(z, None, None, q_sub, q_mask, q_index)

    gold = torch.tensor([bool(e.gold) for e in sub.edges], dtype=torch.bool)
    pg, pn = prob[gold], prob[~gold]
    print(f"\n[score] gold  n={n_gold:5d}  mean={pg.mean():.4f}  median={pg.median():.4f}")
    print(f"[score] noise n={int(pn.numel()):5d}  mean={pn.mean():.4f}  median={pn.median():.4f}")
    p75 = torch.quantile(pn, 0.75)
    print(f"[score] noise p75={p75:.4f}; gold above noise p75: {(pg > p75).float().mean():.3f}")

    print()
    for tau in (0.1, 0.3, 0.5, 0.7):
        keep = fse_expand(sub, prob, tau_e=tau)
        kept_gold = int((keep & gold).sum())
        print(f"[fse tau={tau:.1f}] kept {int(keep.sum()):5d}/{len(sub.edges)} edges; "
              f"gold recall {kept_gold}/{n_gold}={kept_gold/max(n_gold,1):.3f}")
    print(f"\n[ok] smoke test finished ({time.time()-t0:.1f}s)")


if __name__ == "__main__":
    main()
