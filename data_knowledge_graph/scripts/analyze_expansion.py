#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""展开池的诊断与敏感性分析（写报告用的数据）。

输出三张表：

1. **规模分布**：3 跳候选池的节点数 / 边数分位数、截断率、耗时；
2. **黄金边召回**：黄金三元组有多少比例真的落在候选池里（这是 FSE 的上限，
   如果候选池里没有黄金边，评分器再好也救不回来）；
3. **敏感性**：``max_edges`` / ``node_degree_max`` / ``rel_degree_max`` 变化时召回率怎么变，
   用来给决策点 7 定阈值。

用法::

    python scripts/analyze_expansion.py --dataset webqsp --split test --limit 150
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fog.datasets.loader import DatasetSpec, load_split  # noqa: E402
from fog.kg.tsv_index import TripleIndex  # noqa: E402
from fog.naming import EntityLinker, MidNameResolver  # noqa: E402
from fog.pipeline import AlignConfig, resolve_example  # noqa: E402
from fog.subgraph import SubgraphConfig, build_subgraph  # noqa: E402
from fog.utils import Logger, project_root, relpath, write_json  # noqa: E402


def pct(values, q):
    if not values:
        return None
    s = sorted(values)
    k = min(len(s) - 1, max(0, int(round((q / 100) * (len(s) - 1)))))
    return s[k]


def main(argv=None) -> int:
    root = project_root()
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="webqsp", choices=["webqsp", "cwq"])
    ap.add_argument("--split", default="test")
    ap.add_argument("--limit", type=int, default=150)
    ap.add_argument("--kg-index", default="data/processed/kg/index_all")
    args = ap.parse_args(argv)

    log = Logger("analyze", logfile=root / "reports" / "logs" / "analyze_expansion.log")
    cfg = json.loads((root / "configs" / f"{args.dataset}.json").read_text(encoding="utf-8-sig"))
    raw = root / "data" / "raw" / "qa" / cfg["splits"][args.split]

    kg = TripleIndex.load(root / args.kg_index)
    log(f"KG: {kg}")
    mid2name = MidNameResolver.load(root / "data" / "interim" / "mid2name.json")
    linker = EntityLinker(kg)
    spec = DatasetSpec(name=cfg["dataset"], format=cfg["format"], files=cfg["splits"])
    examples = load_split(spec, args.split, raw)[: args.limit]
    log(f"{args.dataset}/{args.split}: {len(examples)} 条")

    base = SubgraphConfig(**{k: v for k, v in cfg["subgraph"].items()
                             if k in SubgraphConfig.__dataclass_fields__})
    align = AlignConfig(**{k: v for k, v in cfg["align"].items()
                           if k in AlignConfig.__dataclass_fields__})

    # ---- 1) 基准配置下的规模与召回
    rows = []
    t0 = time.time()
    for i, ex in enumerate(examples):
        res = resolve_example(ex, kg, linker, mid2name, align)
        src = res.sources[0] if res.sources else None
        if src is None or not res.gold.matched:
            continue
        t = time.perf_counter()
        sub = build_subgraph(kg, ex.qid, [src.name], res.gold.all_triples, ex.answers, base)
        dt = time.perf_counter() - t
        rows.append({
            "qid": ex.qid,
            "n_nodes": sub.stats["n_nodes"],
            "n_edges": sub.stats["n_edges"],
            "truncated": sub.stats["truncated"],
            "gold_recall": sub.stats["gold_recall_in_subgraph"],
            "gold_triples": len(res.gold.all_triples),
            "seconds": round(dt, 4),
        })
        if (i + 1) % 25 == 0:
            log(f"  已处理 {i + 1}/{len(examples)}")

    def summ(key):
        vals = [r[key] for r in rows if r[key] is not None]
        return {"mean": round(statistics.mean(vals), 2) if vals else None,
                "p50": pct(vals, 50), "p90": pct(vals, 90), "p99": pct(vals, 99),
                "max": max(vals) if vals else None}

    base_stats = {
        "n_questions": len(examples),
        "n_solved": len(rows),
        "seconds_total": round(time.time() - t0, 1),
        "n_nodes": summ("n_nodes"),
        "n_edges": summ("n_edges"),
        "seconds_per_question": summ("seconds"),
        "truncated_frac": round(sum(1 for r in rows if r["truncated"]) / max(len(rows), 1), 4),
        "gold_recall_mean": round(statistics.mean([r["gold_recall"] or 0 for r in rows]), 4)
        if rows else None,
        "gold_full_recall_frac": round(sum(1 for r in rows if (r["gold_recall"] or 0) >= 1.0)
                                       / max(len(rows), 1), 4),
        "config": base.to_dict(),
    }
    log(f"\n基准配置：")
    log(f"  节点 {base_stats['n_nodes']}")
    log(f"  边   {base_stats['n_edges']}")
    log(f"  单问耗时 {base_stats['seconds_per_question']['mean']}s")
    log(f"  截断率 {base_stats['truncated_frac']:.1%}  黄金边全召回率 {base_stats['gold_full_recall_frac']:.1%}")

    # ---- 2) 敏感性扫描
    sweep = []
    # 重新对齐 gold（省时间：缓存）；同时记录 path-only 的黄金三元组
    solved = []
    for ex in examples:
        res = resolve_example(ex, kg, linker, mid2name, align)
        src = res.sources[0] if res.sources else None
        if src is not None and res.gold.matched:
            solved.append((ex, src.name, res.gold.all_triples, list(res.gold.triples)))
    log(f"\n敏感性扫描：{len(solved)} 条已对齐问题")

    def run_variant(knob, value, vcfg):
        recalls, path_recalls, n_edges, secs = [], [], [], []
        for ex, src_name, gold, path in solved:
            t = time.perf_counter()
            sub = build_subgraph(kg, ex.qid, [src_name], gold, ex.answers, vcfg)
            secs.append(time.perf_counter() - t)
            n_edges.append(sub.stats["n_edges"])
            recalls.append(sub.stats["gold_recall_in_subgraph"] or 0.0)
            present = {(sub.nodes[e.s].name, e.r, sub.nodes[e.t].name) for e in sub.edges}
            path_recalls.append(
                (sum(1 for t2 in path if t2.key() in present) / len(path)) if path else 0.0)
        row = {
            "knob": knob, "value": value,
            "gold_recall_mean": round(statistics.mean(recalls), 4) if recalls else None,
            "path_recall_mean": round(statistics.mean(path_recalls), 4) if path_recalls else None,
            "gold_full_recall_frac": round(sum(1 for r in recalls if r >= 1.0) / max(len(recalls), 1), 4),
            "path_full_recall_frac": round(sum(1 for r in path_recalls if r >= 1.0) / max(len(path_recalls), 1), 4),
            "n_edges_mean": round(statistics.mean(n_edges), 1) if n_edges else None,
            "seconds_mean": round(statistics.mean(secs), 4) if secs else None,
        }
        sweep.append(row)
        log(f"  {knob}={value:<12} path_recall={row['path_recall_mean']:.4f} "
            f"full={row['path_full_recall_frac']:.1%} "
            f"gold_recall={row['gold_recall_mean']:.4f} "
            f"edges={row['n_edges_mean']:,.0f} t={row['seconds_mean']:.3f}s")
        return row

    for cap in (2000, 10000, 20000, 60000):
        run_variant("max_edges", cap, SubgraphConfig.from_dict(
            {**base.to_dict(), "max_edges": cap}))
    for nd in (200, 1000, 5000, 10 ** 9):
        run_variant("node_degree_max", nd, SubgraphConfig.from_dict(
            {**base.to_dict(), "node_degree_max": nd}))
    for rdmax in (1000, 10000, 100000, 200000, 10 ** 9):
        run_variant("rel_degree_max", rdmax, SubgraphConfig.from_dict(
            {**base.to_dict(), "rel_degree_max": rdmax}))
    # 二维网格：枢纽策略 × 边上限（决策点 7 的组合效应）
    for nd in (1000, 5000, 10 ** 9):
        for cap in (10000, 20000, 60000):
            if nd == base.node_degree_max and cap == base.max_edges:
                continue
            run_variant(f"grid(node_degree_max={nd},max_edges)", cap,
                        SubgraphConfig.from_dict({**base.to_dict(),
                                                  "node_degree_max": nd, "max_edges": cap}))

    report = {
        "dataset": args.dataset, "split": args.split, "kg_index": args.kg_index,
        "base": base_stats, "sweep": sweep,
        "per_question_sample": rows[:50],
    }
    out = root / "reports" / "stats" / f"expansion_{args.dataset}_{args.split}.json"
    write_json(out, report)
    log(f"\n已写入 {relpath(out)}")
    log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
