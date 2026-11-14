import unittest

from experiments.e2e import end_to_end_metrics, f1_score, hit_at_k, token_f1


class TestEndToEndMetrics(unittest.TestCase):
    def test_hit_at_k(self):
        self.assertEqual(hit_at_k(["Jaxon Bieber"], ["Jaxon Bieber"], 1), 1.0)
        self.assertEqual(hit_at_k(["someone else"], ["Jaxon Bieber"], 1), 0.0)
        self.assertEqual(
            hit_at_k(["someone else", "Jaxon Bieber"], ["Jaxon Bieber"], 2), 1.0
        )

    def test_token_f1(self):
        self.assertAlmostEqual(token_f1("Jaxon Bieber", ["Jaxon Bieber"]), 1.0)
        self.assertGreater(token_f1("Jaxon", ["Jaxon Bieber"]), 0.0)
        self.assertEqual(token_f1("", ["Jaxon Bieber"]), 0.0)

    def test_f1_score(self):
        self.assertEqual(f1_score([], ["Jaxon Bieber"]), 0.0)
        self.assertAlmostEqual(f1_score(["Jaxon Bieber"], ["Jaxon Bieber"]), 1.0)

    def test_end_to_end_metrics(self):
        m = end_to_end_metrics(["Jaxon Bieber"], ["Jaxon Bieber"])
        self.assertEqual(m["hit"], 1.0)
        self.assertEqual(m["hit@1"], 1.0)
        self.assertAlmostEqual(m["f1"], 1.0)


if __name__ == "__main__":
    unittest.main()
