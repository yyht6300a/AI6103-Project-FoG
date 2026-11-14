import unittest

import torch

from src.model.training import TrainingConfig, sample_negatives, train_scorer


class _FakeKG:
    """Minimal kg implementing the interface required by sample_negatives."""

    def __init__(self):
        # entity 0: gold path (0 --r1--> 1 --r2--> 2), plus noise edges.
        self._edges = {
            10: ("e0", "r1", "e1"),
            11: ("e1", "r2", "e2"),
            20: ("e0", "noise_low", "e3"),
            21: ("e0", "noise_high", "e4"),
            22: ("e3", "noise_low", "e5"),
        }
        self._out = {0: [10, 20, 21], 1: [11], 3: [22]}
        self._in = {1: [10], 2: [11], 3: [20], 4: [21], 5: [22]}
        self._rel_id = {"r1": 0, "r2": 1, "noise_low": 2, "noise_high": 3}
        self._rel_deg_arr = [1, 1, 10, 100_000]

    def resolve_entity(self, name):
        return {"e0": 0, "e1": 1, "e2": 2, "e3": 3, "e4": 4, "e5": 5}.get(name)

    def out_edges(self, eid):
        return self._out.get(eid, [])

    def in_edges(self, eid):
        return self._in.get(eid, [])

    def edge_triple(self, eid):
        return self._edges[eid]

    def relation_id(self, relation):
        return self._rel_id[relation]

    @property
    def rel_degree(self):
        return self._rel_deg_arr


class DummyScorer(torch.nn.Module):
    def __init__(self, dim):
        super().__init__()
        self.head = torch.nn.Linear(dim, 1)

    def forward(self, x):
        return self.head(x)


class TestTraining(unittest.TestCase):
    def test_sample_negatives_excludes_positives_and_high_degree(self):
        kg = _FakeKG()
        gold = [("e0", "r1", "e1"), ("e1", "r2", "e2")]
        negs = sample_negatives(kg, gold, kappa=10, rel_degree_max=1000, seed=0)
        neg_set = set(negs)
        self.assertNotIn(("e0", "r1", "e1"), neg_set)
        self.assertNotIn(("e0", "noise_high", "e4"), neg_set)
        self.assertIn(("e0", "noise_low", "e3"), neg_set)

    def test_train_scorer_updates_weights_and_saves(self):
        model = DummyScorer(8)
        before = model.head.weight.clone()
        batches = [([torch.randn(16, 8)], (torch.rand(16, 1) > 0.5).float()) for _ in range(3)]
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            path = td + "/ckpt.pt"
            history = train_scorer(model, batches, TrainingConfig(epochs=2, lr=1e-3), save_path=path)
        self.assertEqual(len(history), 2)
        self.assertFalse(torch.equal(before, model.head.weight))


if __name__ == "__main__":
    unittest.main()
