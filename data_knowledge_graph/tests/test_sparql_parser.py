# -*- coding: utf-8 -*-
"""SPARQL 图模式解析的单元测试（对应决策点 3）。"""

from __future__ import annotations

import unittest

from fog.gold.sparql import candidate_chains, parse_sparql
from tests.fixtures import CWQ_SPARQL, WEBQSP_SPARQL


class TestSparqlParser(unittest.TestCase):
    def test_webqsp_pattern(self):
        pat = parse_sparql(WEBQSP_SPARQL)
        self.assertEqual(pat.select_vars, ["x"])
        # FILTER 里的三元组不能进模式
        self.assertEqual(len(pat.triples), 3)
        rels = [t.r for t in pat.triples]
        self.assertEqual(rels, [
            "people.person.sibling_s",
            "people.sibling_relationship.sibling",
            "people.person.gender",
        ])
        self.assertEqual(pat.constants, ["m.06w2sn5", "m.05zppz"])

    def test_webqsp_chain(self):
        pat = parse_sparql(WEBQSP_SPARQL)
        chain = pat.chain_from("m.06w2sn5")
        self.assertEqual(chain, [
            ("people.person.sibling_s", "fwd"),
            ("people.sibling_relationship.sibling", "fwd"),
        ])

    def test_constraint_constant_also_reaches_answer(self):
        """约束用的常量也能连到答案变量——所以链的选择必须靠答案校验，不能只看连通性。"""
        pat = parse_sparql(WEBQSP_SPARQL)
        chain = pat.chain_from("m.05zppz")
        self.assertEqual(chain, [("people.person.gender", "bwd")])

    def test_cwq_pattern_skips_filters_and_exists(self):
        pat = parse_sparql(CWQ_SPARQL)
        self.assertEqual(pat.select_vars, ["x"])
        rels = [t.r for t in pat.triples]
        self.assertIn("location.country.administrative_divisions", rels)
        self.assertIn("government.government_position_held.office_holder", rels)
        # FILTER(NOT EXISTS {...}) 里的三元组必须被剔除
        self.assertNotIn("government.government_position_held.from", rels)
        self.assertEqual(len(pat.triples), 4)

    def test_cwq_chain_direction(self):
        """?c administrative_divisions m.010vz  => 从 m.010vz 出发是逆向。"""
        pat = parse_sparql(CWQ_SPARQL)
        chain = pat.chain_from("m.010vz")
        self.assertIsNotNone(chain)
        self.assertEqual(chain[0], ("location.country.administrative_divisions", "bwd"))
        self.assertEqual(len(chain), 3)

    def test_candidate_chains(self):
        pat = parse_sparql(CWQ_SPARQL)
        chains = candidate_chains(pat)
        self.assertIn("m.010vz", chains)
        self.assertIn("m.060c4", chains)
        # 主链 3 跳，约束常量 2 跳
        self.assertEqual(len(chains["m.010vz"]), 3)
        self.assertEqual(len(chains["m.060c4"]), 2)

    def test_empty_and_garbage(self):
        self.assertEqual(parse_sparql("").triples, [])
        pat = parse_sparql("SELECT ?x WHERE { ?x ?p ?o }")
        self.assertEqual(pat.select_vars, ["x"])
        self.assertEqual(len(pat.triples), 1)


if __name__ == "__main__":
    unittest.main()
