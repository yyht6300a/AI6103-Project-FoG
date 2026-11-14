import gzip
import json
import tempfile
import unittest
from pathlib import Path

from experiments.adapter import load_questions, load_subgraph_candidates
from experiments.metrics import evidence_recall
from experiments.policy import FixedThresholdPolicy


def _write(path: Path, obj: dict) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(obj, f)


class TestAdapter(unittest.TestCase):
    def test_load_questions_gold_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            qa = root / "webqsp" / "qa_test.jsonl.gz"
            qa.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(qa, "wt", encoding="utf-8") as f:
                f.write(json.dumps({
                    "qid": "Q0",
                    "question": "q?",
                    "answers": ["A"],
                    "answer_mids": ["m.A"],
                    "gold": {"triples": [["X", "r", "A"]], "matched": True},
                }) + "\n")
            qs = load_questions("webqsp", "test", root)
            self.assertEqual(len(qs), 1)
            self.assertEqual(qs[0].gold_triples, {("X", "r", "A")})
            self.assertEqual(qs[0].gold_paths, [[("X", "r", "A")]])
            self.assertEqual(qs[0].answers, ["A"])

    def test_run_expansion_decision_keeps_gold(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sub_root = root / "webqsp" / "subgraphs"
            (sub_root / "test").mkdir(parents=True)
            qa = root / "webqsp" / "qa_test.jsonl.gz"
            qa.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(qa, "wt", encoding="utf-8") as f:
                f.write(json.dumps({
                    "qid": "Q0",
                    "question": "q?",
                    "answers": ["A"],
                    "answer_mids": ["m.A"],
                    "gold": {"triples": [["X", "r", "A"]], "matched": True},
                }) + "\n")
            _write(sub_root / "test" / "Q0.json.gz", {
                "nodes": [
                    {"name": "X"}, {"name": "A"}, {"name": "noise"},
                ],
                "edges": [
                    {"s": 0, "r": "r", "t": 1, "ds": 0, "dt": 1, "g": 1},
                    {"s": 0, "r": "r2", "t": 2, "ds": 0, "dt": 1, "g": 0},
                ],
            })
            qs = load_questions(
                "webqsp", "test", root, subgraph_root=sub_root,
                score_fn=lambda t: 0.9 if t == ("X", "r", "A") else 0.05,
            )
            all_c, kept = qs[0].run_expansion_decision(FixedThresholdPolicy(tau_e=0.1, tau_p=0.7))
            self.assertEqual(len(all_c), 2)
            self.assertEqual(len(kept), 1)
            self.assertEqual(kept[0].triple, ("X", "r", "A"))
            self.assertAlmostEqual(evidence_recall(kept, qs[0].gold_triples), 1.0)


if __name__ == "__main__":
    unittest.main()
