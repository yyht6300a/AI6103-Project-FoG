# -*- coding: utf-8 -*-
"""端到端冒烟测试：把 loader → 源实体 → 黄金路径 → 子图 整条链跑通。

两个层次：

* **合成图测试**：不依赖任何下载数据，任何机器上都能跑；
* **真实数据冒烟**：如果 `data/processed/kg/index_all` 存在就顺手跑 6 条真问题
  （这类测试专门用于拦住"单元测试全绿但流水线全挂"的 bug——早期 `_chain_candidates`
  里的 `all(...)` 空集合判定就属于这一类）。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from fog.datasets.loader import DatasetSpec, load_split
from fog.kg.tsv_index import index_from_triples
from fog.naming import EntityLinker, MidNameResolver
from fog.pipeline import AlignConfig, resolve_example
from fog.schema import GoldPath, GoldStep, QAExample, Triple
from fog.subgraph import SubgraphConfig, build_subgraph
from fog.utils import project_root
from tests.fixtures import TOY_TRIPLES_ALL

ROOT = project_root()
KG_DIR = ROOT / "data" / "processed" / "kg" / "index_all"


def _toy_example() -> QAExample:
    return QAExample(
        qid="toy-1", dataset="webqsp", split="test",
        question="who is justin bieber's brother",
        answers=["Jaxon Bieber"], answer_mids=[],
        source_entities=[],
        gold=GoldPath(steps=[GoldStep(relation="people.person.sibling_s"),
                             GoldStep(relation="people.sibling_relationship.sibling")]),
        extra={"inferential_chain": ["people.person.sibling_s",
                                     "people.sibling_relationship.sibling"]},
    )


class TestSyntheticPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kg = index_from_triples(TOY_TRIPLES_ALL)
        cls.linker = EntityLinker(cls.kg)
        cls.mid2name = MidNameResolver({"m.0cvt1": "sibling relationship"})

    def test_resolve_finds_source_by_linking(self):
        """源实体为空时应该靠提及链接找到 Justin Bieber，并对齐黄金路径。"""
        ex = _toy_example()
        res = resolve_example(ex, self.kg, self.linker, self.mid2name, AlignConfig())
        self.assertTrue(res.gold.matched)
        self.assertEqual(res.gold.match_method, "exact")
        self.assertEqual(res.sources[0].name, "Justin Bieber")
        self.assertEqual([t.key() for t in res.gold.triples], [
            ("Justin Bieber", "people.person.sibling_s", "m.0cvt1"),
            ("m.0cvt1", "people.sibling_relationship.sibling", "Jaxon Bieber"),
        ])

    def test_subgraph_contains_gold(self):
        ex = _toy_example()
        res = resolve_example(ex, self.kg, self.linker, self.mid2name, AlignConfig())
        sub = build_subgraph(self.kg, ex.qid, [res.sources[0].name],
                             res.gold.all_triples, ex.answers, SubgraphConfig(hops=3))
        self.assertGreater(len(sub.edges), 0)
        present = {(sub.nodes[e.s].name, e.r, sub.nodes[e.t].name) for e in sub.edges}
        for tr in res.gold.all_triples:
            self.assertIn(tr.key(), present)

    def test_unmatched_is_marked(self):
        ex = _toy_example()
        ex.answers = ["Nobody At All"]
        res = resolve_example(ex, self.kg, self.linker, self.mid2name, AlignConfig())
        self.assertFalse(res.gold.matched)
        self.assertEqual(res.gold.match_method, "none")
        # 即使没对上，也要给出一个源实体候选，便于统计
        self.assertTrue(res.sources)


@unittest.skipUnless(KG_DIR.exists(), "未构建 KG 索引，跳过真实数据冒烟")
class TestRealDataSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fog.kg.tsv_index import TripleIndex

        cls.kg = TripleIndex.load(KG_DIR)
        cls.linker = EntityLinker(cls.kg)
        mid_path = ROOT / "data" / "interim" / "mid2name.json"
        cls.mid2name = (MidNameResolver.load(mid_path) if mid_path.exists()
                        else MidNameResolver())

    def _run(self, dataset: str, split: str, fname: str, n: int = 6):
        path = ROOT / "data" / "raw" / "qa" / fname
        if not path.exists():
            self.skipTest(f"缺少 {fname}")
        spec = DatasetSpec(dataset, dataset, {})
        examples = load_split(spec, split, path)[:n]
        n_matched = 0
        for ex in examples:
            res = resolve_example(ex, self.kg, self.linker, self.mid2name, AlignConfig())
            self.assertTrue(res.sources, f"{ex.qid}: 没有给出任何源实体候选")
            src = res.sources[0]
            self.assertTrue(src.name, f"{ex.qid}: 源实体名字为空")
            if res.gold.matched:
                n_matched += 1
                for tr in res.gold.triples:
                    self.assertTrue(self.kg.has_triple(tr.h, tr.r, tr.t),
                                    f"{ex.qid}: 黄金三元组不在 KG 中 {tr}")
                sub = build_subgraph(self.kg, ex.qid, [src.name], res.gold.all_triples,
                                     ex.answers, SubgraphConfig(hops=3))
                self.assertGreaterEqual(sub.stats["gold_recall_in_subgraph"] or 0, 0.0)
                for e in sub.edges:
                    if e.ds != e.dt:
                        self.assertGreater(sub.nodes[e.src].dist, sub.nodes[e.dst].dist)
        return n_matched, len(examples)

    def test_webqsp_smoke(self):
        matched, n = self._run("webqsp", "test", "WebQSP.test.json")
        self.assertGreater(matched, 0, "WebQSP 前 6 条一条都没对齐，说明流水线坏了")
        self.assertGreaterEqual(matched / n, 0.5)

    def test_cwq_smoke(self):
        """CWQ 没有官方源实体，最容易整体失败的地方，必须单独盯住。"""
        matched, n = self._run("cwq", "test", "ComplexWebQuestions_dev.json")
        self.assertGreater(matched, 0, "CWQ 前 6 条一条都没对齐，说明源实体链接或链解析坏了")


if __name__ == "__main__":
    unittest.main()
