#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""边上限 / 枢纽阈值的一次性复核（写进报告用）。

和 ``analyze_expansion.py`` 的区别：这里直接在**完整 split** 上抽样，并且可以并行跑，
用来复核最终默认值（20k 边上限是不是够）。
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
from fog.utils import Logger, project_root, write_json  # noqa: E402


def main(argv=None) -> int:
    root = project_root()
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="webqsp")
    ap.add_argument("--split", default="test")
    ap.add_argument("--n", type=int, default=250, help="等距抽样题数")
    args = ap.parse_args(argv)

    log = Logger("capcheck", logfile=root / "reports" / "logs" / "capcheck.log")
    cfg = json.loads((root / "configs" / f"{args.dataset}.json").read_text(encoding="utf-8-sig"))
    kg = TripleIndex.load(root / "data" / "processed" / "kg" / "index_all")
    mid2name = MidNameResolver.load(root / "data" / "interim" / "mid2name.json")
    linker = EntityLinker(kg)
    spec = DatasetSpec(cfg["dataset"], cfg["format"], {})
    exs = load_split(spec, args.split, root / "data" / "raw" / "qa" / cfg["splits"][args.split])
    step = max(1, len(exs) // args.n)
    sample = exs[::step][: args.n]
    log(f"{args.dataset}/{args.split}: 全量 {len(exs)}，等距抽样 {len(sample)}")

    align = AlignConfig(**{k: v for k, v in cfg["align"].items()
                           if k in AlignConfig.__dataclass_fields__})
    base = SubgraphConfig.from_dict(cfg["subgraph"])
    solved = []
    for ex in sample:
        r = resolve_example(ex, kg, linker, mid2name, align)
        if r.sources and r.gold.matched:
            solved.append((ex, r.sources[0].name, r.gold.all_triples, list(r.gold.triples)))
    log(f"可用样本 {len(solved)}")

    rows = []
    for knob, values in (("max_edges", [10000, 20000, 40000, 80000]),
                         ("node_degree_max", [2000, 5000, 10000])):
        for v in values:
            vcfg = SubgraphConfig.from_dict({**base.to_dict(), knob: v})
            recalls, sizes, secs = [], [], []
            for ex, name, gold, _path in solved:
                t = time.perf_counter()
                sub = build_subgraph(kg, ex.qid, [name], gold, ex.answers, vcfg)
                secs.append(time.perf_counter() - t)
                sizes.append(sub.stats["n_edges"])
                recalls.append(sub.stats["gold_recall_in_subgraph"] or 0.0)
            rows.append({
                "knob": knob, "value": v,
                "gold_recall_mean": round(statistics.mean(recalls), 4),
                "full_recall_frac": round(sum(1 for x in recalls if x >= 1.0) / len(recalls), 4),
                "n_edges_mean": round(statistics.mean(sizes), 1),
                "seconds_mean": round(statistics.mean(secs), 4),
            })
            log(f"  {knob}={v:<7} recall={rows[-1]['gold_recall_mean']:.4f} "
                f"full={rows[-1]['full_recall_frac']:.1%} edges={rows[-1]['n_edges_mean']:,.0f} "
                f"t={rows[-1]['seconds_mean']:.3f}s")

    out = root / "reports" / "stats" / f"capcheck_{args.dataset}_{args.split}.json"
    write_json(out, {"dataset": args.dataset, "split": args.split,
                     "n_sample": len(sample), "n_solved": len(solved), "rows": rows})
    log(f"已写入 {out}")
    log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
