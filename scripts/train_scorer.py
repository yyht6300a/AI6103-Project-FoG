#!/usr/bin/env python
"""CLI to train the foresight scorer on member A's gold data (member E).

End-to-end wiring: load positives, sample negatives against the Freebase
subgraph index, then run the BCE / AdamW loop. The scorer/embedding batch
construction is member B's responsibility (``src/model/scorer.py``).

Smoke test without the KG index or a real scorer::

    python scripts/train_scorer.py --smoke
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "data_knowledge_graph"))

import torch  # noqa: E402

from src.model.training import TrainingConfig, sample_negatives, train_scorer  # noqa: E402


class _TripleKGAdapter:
    """Expose member A's TripleIndex as plain 3-tuple edge triples."""

    def __init__(self, kg):
        self._kg = kg

    def resolve_entity(self, name):
        return self._kg.resolve_entity(name)

    def out_edges(self, eid):
        return self._kg.out_edges(eid)

    def in_edges(self, eid):
        return self._kg.in_edges(eid)

    def edge_triple(self, eid):
        return tuple(self._kg.edge_triple(eid))

    def relation_id(self, relation):
        return self._kg.relation_id(relation)

    @property
    def rel_degree(self):
        return self._kg.rel_degree


def load_gold_positives(data_root: Path, dataset: str, split: str) -> list[tuple]:
    """Load gold-path triples, skipping unaligned examples without positives."""
    path = data_root / dataset / f"gold_{split}.jsonl.gz"
    if not path.exists():
        raise FileNotFoundError(f"Missing gold artifact: {path}")
    positives: list[tuple] = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            if not rec.get("matched", True):
                continue
            positives.extend(tuple(t) for t in rec.get("triples", []))
    return positives


class DummyScorer(torch.nn.Module):
    """Small scorer used only by the smoke test."""

    def __init__(self, dim: int):
        super().__init__()
        self.head = torch.nn.Linear(dim, 1)

    def forward(self, x):
        return self.head(x)


def _smoke_batches(n_batches: int = 4, batch_size: int = 8, dim: int = 16):
    batches = []
    for _ in range(n_batches):
        x = torch.randn(batch_size, dim)
        y = (torch.rand(batch_size, 1) > 0.5).float()
        batches.append(([x], y))
    return batches


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="webqsp", choices=["webqsp", "cwq"])
    ap.add_argument("--split", default="train")
    ap.add_argument("--kappa", type=int, default=10)
    ap.add_argument("--rel-degree-max", type=int, default=200_000)
    ap.add_argument("--epochs", type=int, default=5)
    ap.add_argument("--lr", type=float, default=1e-5)
    ap.add_argument("--save", default=None)
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args(argv)

    if args.smoke:
        model = DummyScorer(16)
        history = train_scorer(
            model,
            _smoke_batches(),
            TrainingConfig(epochs=args.epochs, lr=args.lr),
            save_path=args.save,
        )
        print("smoke loss per epoch:", [round(x, 4) for x in history])
        return 0

    data_root = REPO / "data_knowledge_graph" / "data" / "processed"
    from fog.kg.tsv_index import TripleIndex  # noqa: E402

    kg = TripleIndex.load(data_root / "kg" / "index_all")
    positives = load_gold_positives(data_root, args.dataset, args.split)
    negatives = sample_negatives(
        _TripleKGAdapter(kg),
        positives,
        kappa=args.kappa,
        rel_degree_max=args.rel_degree_max,
    )
    print(f"positives={len(positives)}  negatives={len(negatives)}")
    print("Scorer / embedding batch construction is member B's responsibility.")
    print("Implement scorer.forward(triples, question, sub_questions), then add")
    print("the embedding + batch step here and call train_scorer(...).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
