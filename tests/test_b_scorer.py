import tempfile
import unittest
from pathlib import Path

import torch

from src.model.mock_scorer import CosineScorer
from src.model.scorer import (TAU_P, ForesightTripleScorer, ScorerConfig, TripleBatch,
                              logit_threshold, pack_sub_questions, sub_question_set)

D = 16


def make_batch(n=6, b=2, seed=0):
    g = torch.Generator().manual_seed(seed)
    q_index = torch.tensor([i % b for i in range(n)])
    return TripleBatch(
        head_emb=torch.randn(n, D, generator=g), rel_emb=torch.randn(n, D, generator=g),
        tail_emb=torch.randn(n, D, generator=g),
        head_is_mid=torch.tensor([i == 1 for i in range(n)]),
        tail_is_mid=torch.tensor([i == 2 for i in range(n)]),
        q_index=q_index, q_ori=torch.randn(b, D, generator=g))


def make_subq(b=2, seed=1):
    g = torch.Generator().manual_seed(seed)
    return pack_sub_questions([torch.randn(i + 1, D, generator=g) for i in range(b)])


def small_scorer():
    torch.manual_seed(0)
    return ForesightTripleScorer(ScorerConfig(emb_dim=D, num_heads=2)).eval()


class ScorerTest(unittest.TestCase):
    def test_shapes_and_two_step_equals_forward(self):
        model, batch = small_scorer(), make_batch()
        q_sub, mask = make_subq()
        z = model.encode(batch)
        self.assertEqual(z.shape, (6, D))
        logits = model.score(z, None, None, q_sub, mask, batch.q_index)
        self.assertEqual(logits.shape, (6,))
        self.assertTrue(torch.allclose(logits, model(batch, q_sub, mask)))
        p = model.prob(z, None, None, q_sub, mask, batch.q_index)
        self.assertTrue(torch.allclose(p, torch.sigmoid(logits)))

    def test_h_none_means_zero(self):
        model, batch = small_scorer(), make_batch()
        q_sub, mask = make_subq()
        z = model.encode(batch)
        zeros = torch.zeros_like(z)
        self.assertTrue(torch.allclose(model.score(z, None, None, q_sub, mask, batch.q_index),
                                       model.score(z, zeros, zeros, q_sub, mask, batch.q_index)))
        h = torch.randn_like(z)
        self.assertFalse(torch.allclose(model.score(z, h, h, q_sub, mask, batch.q_index),
                                        model.score(z, None, None, q_sub, mask, batch.q_index)))

    def test_unnamed_nodes_use_the_cvt_vector(self):
        model, batch = small_scorer(), make_batch()
        q_sub, mask = make_subq()
        before = model(batch, q_sub, mask)
        batch.head_emb[1] = 123.0   # row 1 has an unnamed head
        batch.tail_emb[2] = -50.0   # row 2 has an unnamed tail
        self.assertTrue(torch.allclose(before, model(batch, q_sub, mask)))
        batch.head_emb[0] = 7.0     # named head: output must change
        self.assertFalse(torch.allclose(before[0], model(batch, q_sub, mask)[0]))

    def test_padding_and_other_questions_do_not_leak(self):
        model, batch = small_scorer(), make_batch()
        q_sub, mask = make_subq()
        before = model(batch, q_sub, mask)
        q_sub2 = q_sub.clone()
        q_sub2[0, 1:] = 99.0        # padded slots of question 0
        self.assertTrue(torch.allclose(before, model(batch, q_sub2, mask)))
        q_sub3 = q_sub.clone()
        q_sub3[1] = torch.randn_like(q_sub3[1])   # question 1 only
        after = model(batch, q_sub3, mask)
        own0 = batch.q_index == 0
        self.assertTrue(torch.allclose(before[own0], after[own0]))
        self.assertFalse(torch.allclose(before[~own0], after[~own0]))

    def test_all_masked_question_gives_finite_scores(self):
        model, batch = small_scorer(), make_batch()
        q_sub, mask = make_subq()
        mask[0] = False
        self.assertTrue(torch.isfinite(model(batch, q_sub, mask)).all())

    def test_gradients_reach_role_bias_and_cvt(self):
        model, batch = small_scorer(), make_batch()
        model.train()
        q_sub, mask = make_subq()
        model(batch, q_sub, mask).sum().backward()
        self.assertGreater(model.role_bias.grad.abs().sum().item(), 0)
        self.assertGreater(model.cvt_emb.grad.abs().sum().item(), 0)

    def test_save_and_load(self):
        model, batch = small_scorer(), make_batch()
        q_sub, mask = make_subq()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "s.pt"
            model.save(path, extra={"epoch": 1})
            loaded = ForesightTripleScorer.load(path).eval()
        self.assertTrue(torch.allclose(model(batch, q_sub, mask), loaded(batch, q_sub, mask)))

    def test_paper_size_model(self):
        with torch.device("meta"):
            model = ForesightTripleScorer()
        self.assertEqual(model.d, 2560)
        d = 2560
        # self block 12d^2 + cross block 12d^2 + F_attn 3d^2 + MLP_s 4d^2 (+ biases, norms, p, cvt)
        self.assertAlmostEqual(model.num_parameters() / (31 * d * d), 1.0, delta=0.01)

    def test_helpers(self):
        self.assertEqual(sub_question_set("q", ["a", "q", "a", "b"]), ["q", "a", "b"])
        self.assertAlmostEqual(logit_threshold(TAU_P), 0.8473, places=3)
        with self.assertRaises(ValueError):
            ScorerConfig(emb_dim=10, num_heads=3)


class MockScorerTest(unittest.TestCase):
    def test_same_interface(self):
        model, batch = CosineScorer(emb_dim=D), make_batch()
        q_sub, mask = make_subq()
        z = model.encode(batch)
        self.assertEqual(z.shape, (6, D))
        p = model.prob(z, None, None, q_sub, mask, batch.q_index)
        self.assertEqual(p.shape, (6,))
        self.assertTrue(((p > 0) & (p < 1)).all())
        self.assertTrue(torch.allclose(model(batch, q_sub, mask), torch.logit(p), atol=1e-4))


if __name__ == "__main__":
    unittest.main()
