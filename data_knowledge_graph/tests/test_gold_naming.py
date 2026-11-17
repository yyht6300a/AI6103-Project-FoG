# -*- coding: utf-8 -*-
"""黄金路径对齐（matcher）与命名（naming）的单元测试。"""

from __future__ import annotations

import unittest

from fog.gold.matcher import align_gold, bfs_fallback, constraint_triples, walk_chain
from fog.gold.sexpr import parse_sexpr
from fog.kg.tsv_index import index_from_triples
from fog.naming import EntityLinker, MidNameResolver
from tests.fixtures import CWQ_SEXPR, TOY_TRIPLES_ALL


def _kg():
    return index_from_triples(TOY_TRIPLES_ALL)


class TestWalkChain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kg = _kg()

    def test_exact_two_hop_through_cvt(self):
        chain = [("people.person.sibling_s", "fwd"), ("people.sibling_relationship.sibling", "fwd")]
        res = walk_chain(self.kg, ["Justin Bieber"], chain, ["Jaxon Bieber"])
        self.assertTrue(res.ok)
        self.assertEqual(res.method, "exact")
        self.assertEqual([t.key() for t in res.triples], [
            ("Justin Bieber", "people.person.sibling_s", "m.0cvt1"),
            ("m.0cvt1", "people.sibling_relationship.sibling", "Jaxon Bieber"),
        ])

    def test_answer_validation_rejects_wrong_target(self):
        chain = [("people.person.sibling_s", "fwd"), ("people.sibling_relationship.sibling", "fwd")]
        res = walk_chain(self.kg, ["Justin Bieber"], chain, ["Nobody"])
        self.assertFalse(res.ok)

    def test_reverse_step_is_relaxed(self):
        """KG 里的方向与标注方向不一致时，必须能放宽方向但仍命中答案。

        KG 只有 (London, location.location.containedby, England)，
        所以从 England 出发到 London 是逆向；标注若写 fwd，应退化为 relaxed。
        """
        res = walk_chain(self.kg, ["England"],
                         [("location.location.containedby", "fwd")], ["London"])
        self.assertTrue(res.ok)
        self.assertEqual(res.method, "relaxed")
        self.assertEqual([t.key() for t in res.triples],
                         [("London", "location.location.containedby", "England")])

    def test_bwd_direction_is_exact(self):
        res = walk_chain(self.kg, ["England"],
                         [("location.location.containedby", "bwd")], ["London"])
        self.assertTrue(res.ok)
        self.assertEqual(res.method, "exact")

    def test_unknown_start(self):
        res = walk_chain(self.kg, ["Nobody Here"], [("a.b", "fwd")], ["x"])
        self.assertFalse(res.ok)
        self.assertIn("源实体不在 KG 中", res.warnings)

    def test_align_gold_primary_then_fallback(self):
        kg = self.kg
        gold = align_gold(
            kg, ["Justin Bieber"], ["Jaxon Bieber"], [],
            primary_chain=[("people.person.sibling_s", "fwd"),
                           ("people.sibling_relationship.sibling", "fwd")],
        )
        self.assertTrue(gold.matched)
        self.assertEqual(gold.match_method, "exact")
        self.assertEqual(len(gold.triples), 2)

    def test_align_gold_uses_alt_chain(self):
        kg = self.kg
        gold = align_gold(
            kg, ["Justin Bieber"], ["London"], [],
            primary_chain=[("wrong.relation", "fwd")],
            alt_chains=[[("people.person.place_of_birth", "fwd")]],
        )
        self.assertTrue(gold.matched)
        self.assertTrue(gold.match_method.startswith("alt1"))

    def test_align_gold_unmatched(self):
        kg = self.kg
        gold = align_gold(kg, ["Justin Bieber"], ["Nowhere"], [],
                          primary_chain=[("people.person.place_of_birth", "fwd")],
                          allow_shortest_fallback=False)
        self.assertFalse(gold.matched)
        self.assertEqual(gold.match_method, "none")


class TestBfsFallback(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kg = _kg()

    def test_relation_restricted(self):
        kg = self.kg
        res = bfs_fallback(kg, ["Justin Bieber"], ["England"], max_depth=3,
                           relation_allow={"people.person.place_of_birth",
                                           "location.location.containedby"})
        self.assertTrue(res.ok)
        self.assertEqual(res.method, "relaxed_order")
        self.assertEqual([t.key() for t in res.triples][-1],
                         ("London", "location.location.containedby", "England"))

    def test_shortest_any(self):
        kg = self.kg
        res = bfs_fallback(kg, ["Justin Bieber"], ["Pop music"], max_depth=3)
        self.assertTrue(res.ok)
        self.assertEqual(res.method, "shortest_any")


class TestConstraints(unittest.TestCase):
    def test_constraint_projection(self):
        kg = _kg()
        cons = [{"NodePredicate": "people.person.gender", "EntityName": "Male",
                 "SourceNodeIndex": 2, "Argument": "m.05zppz", "ArgumentType": "Entity"}]
        nodes = ["Justin Bieber", "m.0cvt1", "Jaxon Bieber"]
        tr = constraint_triples(kg, nodes, cons)
        self.assertEqual([t.key() for t in tr],
                         [("Jaxon Bieber", "people.person.gender", "Male")])


class TestSexpr(unittest.TestCase):
    def test_chain(self):
        chain = parse_sexpr(CWQ_SEXPR)
        self.assertIn("government.governmental_jurisdiction.governing_officials", chain.relations)
        self.assertIn("government.government_position_held.office_holder", chain.relations)
        self.assertIn("m.010vz", chain.entities)

    def test_empty(self):
        chain = parse_sexpr(None)
        self.assertEqual(chain.relations, [])


class TestNaming(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kg = _kg()

    def test_mid2name_fallback(self):
        r = MidNameResolver({"m.06w2sn5": "Justin Bieber"})
        self.assertEqual(r.to_name("m.06w2sn5"), "Justin Bieber")
        self.assertEqual(r.to_name("m.unknown"), "m.unknown")   # 决策点 2 的回退
        self.assertEqual(r.to_name(None), "")

    def test_mid2name_from_webqsp(self):
        questions = [{
            "Parses": [{
                "TopicEntityMid": "m.06w2sn5", "TopicEntityName": "Justin Bieber",
                "Answers": [{"AnswerArgument": "m.0gxnnwq", "EntityName": "Jaxon Bieber"}],
                "Constraints": [],
            }]
        }]
        r = MidNameResolver.from_webqsp(questions)
        self.assertEqual(r.to_name("m.0gxnnwq"), "Jaxon Bieber")

    def test_mid2name_from_cwq(self):
        recs = [{"answers": [{"answer_id": "m.01j5_1", "answer": "Muhammad Zia-ul-Haq"}]}]
        r = MidNameResolver.from_cwq(recs)
        self.assertEqual(r.to_name("m.01j5_1"), "Muhammad Zia-ul-Haq")

    def test_linker(self):
        linker = EntityLinker(self.kg)
        names = linker.link_names("who is the sibling of Justin Bieber")
        self.assertIn("Justin Bieber", names)

    def test_linker_longest_match_wins(self):
        linker = EntityLinker(self.kg)
        names = linker.link_names("is London containedby England or United Kingdom")
        self.assertIn("United Kingdom", names)   # 长提及优先，未被 "England" 切碎


if __name__ == "__main__":
    unittest.main()
