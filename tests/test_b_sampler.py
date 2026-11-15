import unittest

import numpy as np

from src.model.sampler import SamplerConfig, TrainingRegionBuilder
from b_fixtures import ADJ, ADJ_S, AWARD, NOTABLE, SIB, SIB_S, bieber_record, tiny_kg, usa_record


def triples_of(kg, ex, label):
    return {(kg.entities[h], kg.relations[r], kg.entities[t])
            for h, r, t, y in zip(ex.head_ids, ex.rel_ids, ex.tail_ids, ex.labels) if y == label}


class SamplerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kg = tiny_kg()

    def test_positives_negatives_and_kappa(self):
        b = TrainingRegionBuilder(self.kg, SamplerConfig(kappa=10))
        ex = b.build(bieber_record(self.kg))
        pos = triples_of(self.kg, ex, 1)
        neg = triples_of(self.kg, ex, 0)
        self.assertEqual(pos, {("Justin Bieber", SIB_S, "m.cvt1"), ("m.cvt1", SIB, "Jaxon Bieber")})
        # deg_pos: Justin 1, m.cvt1 2, Jaxon 1 -> only Justin has non-positive neighbours
        self.assertEqual(len(neg), 10)
        self.assertTrue(pos.isdisjoint(neg))
        self.assertTrue(all(h == "Justin Bieber" for h, _, _ in neg))
        self.assertFalse(any(r == NOTABLE for _, r, _ in neg))   # generic-relation filter

    def test_kappa_scales_with_deg_pos_and_caps_at_neighbourhood(self):
        b = TrainingRegionBuilder(self.kg, SamplerConfig(kappa=100))
        ex = b.build(bieber_record(self.kg))
        # Justin's allowed neighbours: 2 sibling + genre + birthplace + 30 awards = 34, minus 1 positive
        self.assertEqual(ex.n_neg, 33)

    def test_deterministic_per_epoch(self):
        b = TrainingRegionBuilder(self.kg)
        a1 = b.build(bieber_record(self.kg), epoch=0)
        a2 = b.build(bieber_record(self.kg), epoch=0)
        a3 = b.build(bieber_record(self.kg), epoch=1)
        self.assertTrue(np.array_equal(a1.edge_ids, a2.edge_ids))
        self.assertFalse(np.array_equal(a1.edge_ids, a3.edge_ids))

    def test_multi_answer_paths_become_positive(self):
        b = TrainingRegionBuilder(self.kg)
        ex = b.build(usa_record(self.kg))
        pos = triples_of(self.kg, ex, 1)
        usa = "United States of America"
        self.assertIn((usa, ADJ_S, "m.adj2"), pos)           # path to Mexico, the second answer
        self.assertIn(("m.adj2", ADJ, "Mexico"), pos)
        self.assertNotIn((usa, ADJ_S, "m.adj3"), pos)        # Cuba is not an answer
        self.assertNotIn(("Canada", NOTABLE, "Country"), pos)  # filtered relation dropped
        self.assertEqual(b.stats["gold_triple_filtered_relation"], 1)
        self.assertNotIn((usa, ADJ_S, "m.adj2"), triples_of(self.kg, ex, 0))

    def test_without_augmentation_other_answer_can_be_negative(self):
        b = TrainingRegionBuilder(self.kg, SamplerConfig(augment_answer_paths=False, kappa=100))
        ex = b.build(usa_record(self.kg))
        self.assertIn(("United States of America", ADJ_S, "m.adj2"), triples_of(self.kg, ex, 0))

    def test_queries_are_independent_of_gold(self):
        b = TrainingRegionBuilder(self.kg)
        rec = bieber_record(self.kg)
        expected = [rec["question"]]
        self.assertEqual(b.build(rec).sub_questions, expected)
        self.assertEqual(b.sub_questions_for(rec), expected)
        rec["gold"] = {"matched": False, "triples": []}
        self.assertEqual(b.sub_questions_for(rec), expected)
        self.assertIsNone(b.build(rec))
        self.assertEqual(b.sub_questions_for(rec, ["independent query"]),
                         expected + ["independent query"])

    def test_explicit_independent_queries(self):
        b = TrainingRegionBuilder(self.kg)
        rec = bieber_record(self.kg)
        self.assertEqual(b.build(rec, sub_questions=["Who is the person?"]).sub_questions,
                         [rec["question"], "Who is the person?"])

    def test_unused_award_relation_is_sampled(self):
        b = TrainingRegionBuilder(self.kg, SamplerConfig(kappa=100))
        neg = triples_of(self.kg, b.build(bieber_record(self.kg)), 0)
        self.assertEqual(sum(r == AWARD for _, r, _ in neg), 30)


if __name__ == "__main__":
    unittest.main()
