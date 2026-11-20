# -*- coding: utf-8 -*-
"""离线三元组索引与 3 跳子图展开的单元测试。"""

from __future__ import annotations

import unittest

from fog.kg.tsv_index import index_from_triples
from fog.schema import Triple
from fog.subgraph import SubgraphConfig, bfs_distances, build_subgraph
from tests.fixtures import HIGH_DEGREE_REL, TOY_TRIPLES_ALL


def _toy_index():
    return index_from_triples(TOY_TRIPLES_ALL)


class TestTripleIndex(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kg = _toy_index()

    def test_sizes(self):
        self.assertEqual(self.kg.n_triples, len({t.key() for t in TOY_TRIPLES_ALL}))
        self.assertEqual(self.kg.n_entities, len({x for t in TOY_TRIPLES_ALL for x in (t.h, t.t)}))

    def test_lookup_and_adjacency(self):
        kg = self.kg
        eid = kg.entity_id("Justin Bieber")
        self.assertIsNotNone(eid)
        outs = kg.out_edges(eid)
        rels = {kg.relations[int(kg.rel[e])] for e in outs}
        self.assertIn("people.person.sibling_s", rels)
        self.assertIn("music.artist.genre", rels)

        self.assertTrue(kg.has_triple("My World", "music.album.artist", "Justin Bieber"))
        outs_mw = kg.out_edges(kg.entity_id("My World"))
        self.assertTrue(any(kg.relations[int(kg.rel[e])] == "music.album.artist" for e in outs_mw))

    def test_degree_and_relation_degree(self):
        kg = self.kg
        rid = kg.relation_id(HIGH_DEGREE_REL)
        self.assertEqual(int(kg.rel_degree[rid]), 30)

    def test_resolve_case_insensitive(self):
        kg = self.kg
        self.assertIsNotNone(kg.resolve_entity("justin bieber"))
        self.assertEqual(kg.resolve_entity("justin bieber"), kg.entity_id("Justin Bieber"))

    def test_has_triple(self):
        kg = self.kg
        self.assertTrue(kg.has_triple("Justin Bieber", "people.person.sibling_s", "m.0cvt1"))
        self.assertFalse(kg.has_triple("Justin Bieber", "people.person.sibling_s", "London"))

    def test_roundtrip_save_load(self):
        from fog.kg.tsv_index import TripleIndex
        from fog.utils import project_root

        d = project_root() / "data" / "interim" / "_test_index"
        try:
            self.kg.save(d)
            kg2 = TripleIndex.load(d)
            self.assertEqual(kg2.n_triples, self.kg.n_triples)
            self.assertEqual(kg2.entities, self.kg.entities)
        finally:
            if d.exists():
                for f in d.iterdir():
                    f.unlink()
                d.rmdir()


class TestSubgraph(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kg = _toy_index()

    def test_bfs_layers(self):
        kg = self.kg
        dist, stats = bfs_distances(kg, [kg.entity_id("Justin Bieber")], SubgraphConfig(hops=3))
        self.assertEqual(dist[kg.entity_id("Justin Bieber")], 0)
        self.assertEqual(dist[kg.entity_id("m.0cvt1")], 1)
        self.assertEqual(dist[kg.entity_id("Jaxon Bieber")], 2)
        self.assertEqual(dist[kg.entity_id("London")], 1)
        self.assertEqual(dist[kg.entity_id("England")], 2)
        # 3 跳链上的节点在 L=3 时可达
        self.assertEqual(dist.get(kg.entity_id("United Kingdom")), 3)

    def test_relation_degree_filter(self):
        """决策点 7：通用关系的频次 / 黑名单过滤。"""
        kg = self.kg
        gold = [Triple("Justin Bieber", "people.person.sibling_s", "m.0cvt1")]
        cfg = SubgraphConfig(hops=2, rel_degree_max=20)
        sub = build_subgraph(kg, "q", ["Justin Bieber"], gold, ["Jaxon Bieber"], cfg)
        rels = {e.r for e in sub.edges}
        self.assertNotIn(HIGH_DEGREE_REL, rels)
        self.assertIn("people.person.sibling_s", rels)
        # 过滤策略本身
        self.assertFalse(cfg.relation_allowed(HIGH_DEGREE_REL, 30))
        self.assertTrue(cfg.relation_allowed("people.person.sibling_s", 1))
        self.assertFalse(cfg.relation_allowed("common.topic.notable_types", 5))
        self.assertFalse(cfg.relation_allowed("freebase.valuenotation.is_reviewed", 5))

    def test_relation_blocklist(self):
        kg = self.kg
        cfg = SubgraphConfig(hops=2, rel_degree_max=10_000, rel_blocklist=(HIGH_DEGREE_REL,))
        sub = build_subgraph(kg, "q", ["Justin Bieber"], [], [], cfg)
        self.assertNotIn(HIGH_DEGREE_REL, {e.r for e in sub.edges})

    def test_hub_node_not_expanded(self):
        """非源实体的枢纽节点不展开；但**源实体本身永远展开**（否则会得到空子图）。"""
        kg = self.kg
        cfg = SubgraphConfig(hops=3, node_degree_max=1)
        dist, stats = bfs_distances(kg, [kg.entity_id("Justin Bieber")], cfg)
        # 源实体即使度数超阈值也要展开 → 1 跳邻居必须在
        self.assertEqual(dist.get(kg.entity_id("m.0cvt1")), 1)
        self.assertEqual(dist.get(kg.entity_id("London")), 1)
        # m.0cvt1 度数 > 1，不作为展开起点 → 2 跳的 Jaxon Bieber 进不来
        self.assertNotIn(kg.entity_id("Jaxon Bieber"), dist)
        self.assertGreater(stats["hub_skipped"], 0)

    def test_seed_hub_still_expands(self):
        """源实体是枢纽（度数远超阈值）时仍然展开出 1 跳邻居。"""
        kg = self.kg
        cfg = SubgraphConfig(hops=2, node_degree_max=0)
        sub = build_subgraph(kg, "q", ["Justin Bieber"], [], [], cfg)
        self.assertGreater(len(sub.edges), 0)
        self.assertGreater(sub.stats["n_nodes"], 1)

    def test_inward_orientation(self):
        """朝内定向：src 必须比 dst 离源更远（或同层）。"""
        kg = self.kg
        sub = build_subgraph(kg, "q", ["Justin Bieber"], [], ["Jaxon Bieber"],
                             SubgraphConfig(hops=3))
        self.assertGreater(len(sub.edges), 0)
        for e in sub.edges:
            if e.ds != e.dt:
                self.assertGreater(sub.nodes[e.src].dist, sub.nodes[e.dst].dist,
                                   f"边 {sub.nodes[e.s].name}->{sub.nodes[e.t].name} 定向错误")
                self.assertEqual(sub.nodes[e.src].dist, max(e.ds, e.dt))
                self.assertEqual(sub.nodes[e.dst].dist, min(e.ds, e.dt))

    def test_gold_flag_and_recall(self):
        kg = self.kg
        gold = [
            Triple("Justin Bieber", "people.person.sibling_s", "m.0cvt1"),
            Triple("m.0cvt1", "people.sibling_relationship.sibling", "Jaxon Bieber"),
        ]
        sub = build_subgraph(kg, "q", ["Justin Bieber"], gold, ["Jaxon Bieber"],
                             SubgraphConfig(hops=3))
        self.assertEqual(sub.stats["n_gold_edges"], 2)
        self.assertEqual(sub.stats["gold_recall_in_subgraph"], 1.0)
        # 每一条黄金边都必须是 gold=1
        gold_names = {t.key() for t in gold}
        for e in sub.edges:
            self.assertEqual(bool(e.gold), sub.triple_names(e) in gold_names)

    def test_missing_seed(self):
        kg = self.kg
        sub = build_subgraph(kg, "q", ["Not In Graph"], [], [], SubgraphConfig())
        self.assertEqual(len(sub.nodes), 0)
        self.assertEqual(sub.stats["empty_reason"], "no_seed_in_kg")

    def test_determinism(self):
        kg = self.kg
        a = build_subgraph(kg, "q", ["Justin Bieber"], [], [], SubgraphConfig(hops=2)).to_dict()
        b = build_subgraph(kg, "q", ["Justin Bieber"], [], [], SubgraphConfig(hops=2)).to_dict()
        self.assertEqual(a, b)

    def test_edge_cap(self):
        kg = self.kg
        sub = build_subgraph(kg, "q", ["Justin Bieber"], [], [], SubgraphConfig(hops=3, max_edges=3))
        self.assertLessEqual(len(sub.edges), 3)
        self.assertTrue(sub.stats["truncated"])


if __name__ == "__main__":
    unittest.main()

