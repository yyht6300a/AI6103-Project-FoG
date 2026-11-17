#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""单题演示：把"一个 dev 问题 → 源实体 + 黄金三元组 + 3 跳子图"整条链跑出来。

对应《复现计划》4.5 的 Phase 1 验收标准：
**"能对一个 dev 问题取出源实体、黄金三元组与 3 跳子图"**。

用法::

    python scripts/demo_one_question.py                      # 默认 WebQSP test 第一条
    python scripts/demo_one_question.py --dataset cwq --index 5
    python scripts/demo_one_question.py --qid WebQTest-0
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fog.datasets.loader import DatasetSpec, load_split  # noqa: E402
from fog.kg.tsv_index import TripleIndex  # noqa: E402
from fog.naming import EntityLinker, MidNameResolver  # noqa: E402
from fog.pipeline import AlignConfig, resolve_example  # noqa: E402
from fog.subgraph import SubgraphConfig, build_subgraph  # noqa: E402
from fog.utils import project_root  # noqa: E402


def main(argv=None) -> int:
    root = project_root()
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="webqsp", choices=["webqsp", "cwq"])
    ap.add_argument("--split", default="test")
    ap.add_argument("--index", type=int, default=0, help="取第几条（0 起）")
    ap.add_argument("--qid", default=None)
    args = ap.parse_args(argv)

    cfg = json.loads((root / "configs" / f"{args.dataset}.json").read_text(encoding="utf-8-sig"))
    spec = DatasetSpec(cfg["dataset"], cfg["format"], {})
    raw = root / "data" / "raw" / "qa" / cfg["splits"][args.split]
    examples = load_split(spec, args.split, raw)
    if args.qid:
        examples = [e for e in examples if e.qid == args.qid] or examples[:1]
    ex = examples[args.index]

    kg = TripleIndex.load(root / "data" / "processed" / "kg" / "index_all")
    mid2name = MidNameResolver.load(root / "data" / "interim" / "mid2name.json")
    linker = EntityLinker(kg)

    print("=" * 78)
    print(f"问题 [{ex.qid}]  {ex.question}")
    print(f"答案      {ex.answers}")
    print("=" * 78)

    res = resolve_example(ex, kg, linker, mid2name, AlignConfig())
    print("\n① 源实体（决策点 1）")
    for s in res.sources:
        print(f"   name={s.name!r}  mid={s.mid}  kg_id={s.kg_id}  "
              f"method={s.method}  in_kg={s.in_kg}")

    print(f"\n② 黄金推理路径（决策点 3）  对齐方式={res.gold.match_method}")
    print(f"   标注关系链: {[s.relation for s in res.gold.steps]}")
    for i, tr in enumerate(res.gold.triples, 1):
        print(f"   [{i}] ({tr.h}, {tr.r}, {tr.t})")
    if res.gold.constraints:
        print(f"   约束三元组: {[t.as_list() for t in res.gold.constraints]}")

    sub = build_subgraph(kg, ex.qid, [res.sources[0].name], res.gold.all_triples,
                         ex.answers, SubgraphConfig.from_dict(cfg["subgraph"]))
    st = sub.stats
    print("\n③ 3 跳展开子图（决策点 7/8）")
    print(f"   节点 {st['n_nodes']:,}  边 {st['n_edges']:,}  截断={st['truncated']}")
    print(f"   各层节点数: {st['layer_node_counts']}")
    print(f"   黄金边 {st['n_gold_edges']}/{st['n_gold_triples']}   "
          f"召回={st['gold_recall_in_subgraph']}")
    print(f"   同层边 {st['n_lateral_edges']:,}   枢纽跳过 {st['hub_skipped']}   "
          f"节点预算撞顶 {st['node_cap_hit']:,}")
    print("\n   前 8 条边（注意 src->dst 是朝内定向：远端 -> 近端）:")
    for e in sub.edges[:8]:
        print(f"     ({sub.nodes[e.s].name[:38]:<38s} -{e.r[:38]:<38s}-> "
              f"{sub.nodes[e.t].name[:32]:<32s})  d={e.ds}->{e.dt}  "
              f"inward={e.src}->{e.dst}  gold={e.gold}")
    print("\n   ✅ Phase 1 验收：源实体 / 黄金三元组 / 3 跳子图 三者均已产出。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
