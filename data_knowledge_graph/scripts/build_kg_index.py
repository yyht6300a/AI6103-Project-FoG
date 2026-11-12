#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""构建离线知识图谱索引（成员 A 交付物 2：Freebase 子图抽取 → 可 BFS 的索引）。

输入是 ``data/raw/kg/*.tsv``（head \\t relation \\t tail，名字空间）。输出是一个纯 numpy
的 CSR 邻接索引，供 3 跳展开、黄金路径对齐、负采样共用。

用法::

    # 默认：把 WebQSP + CWQ 两个子图并成一张图（覆盖更全，推荐）
    python scripts/build_kg_index.py

    # 只建 CWQ 子图
    python scripts/build_kg_index.py --dataset cwq

    # 自定义输入
    python scripts/build_kg_index.py --inputs data/raw/kg/cwq_subgraph.tsv --out data/processed/kg/cwq_index
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fog.kg.tsv_index import TripleIndex  # noqa: E402
from fog.utils import Logger, human_bytes, human_int, project_root, relpath, write_json  # noqa: E402

DEFAULT_INPUTS = {
    "all": ["data/raw/kg/webqsp_subgraph.tsv", "data/raw/kg/cwq_subgraph.tsv"],
    "webqsp": ["data/raw/kg/webqsp_subgraph.tsv"],
    "cwq": ["data/raw/kg/cwq_subgraph.tsv"],
}
DEFAULT_OUT = {
    "all": "data/processed/kg/index_all",
    "webqsp": "data/processed/kg/index_webqsp",
    "cwq": "data/processed/kg/index_cwq",
}


def main(argv: list[str] | None = None) -> int:
    root = project_root()
    ap = argparse.ArgumentParser(description="构建 Freebase 子图的离线三元组索引")
    ap.add_argument("--dataset", choices=sorted(DEFAULT_INPUTS), default="all")
    ap.add_argument("--inputs", nargs="*", default=None, help="TSV 路径（覆盖 --dataset）")
    ap.add_argument("--out", default=None, help="索引输出目录")
    ap.add_argument("--no-sha", action="store_true", help="跳过源文件 sha256（更快）")
    args = ap.parse_args(argv)

    inputs = [root / p for p in (args.inputs or DEFAULT_INPUTS[args.dataset])]
    out = root / (args.out or DEFAULT_OUT[args.dataset])
    missing = [p for p in inputs if not p.exists()]
    if missing:
        print("缺少输入文件：")
        for p in missing:
            print("  ", p)
        print("\n先运行：python scripts/download_data.py --only kg")
        return 2

    log = Logger("build_kg", logfile=root / "reports" / "logs" / "build_kg_index.log")
    log(f"索引构建开始：{len(inputs)} 个输入 -> {relpath(out)}")
    for p in inputs:
        log(f"  input: {relpath(p)} ({human_bytes(p.stat().st_size)})")

    t0 = time.time()
    idx, stats = TripleIndex.build(inputs, log=log, compute_sha=not args.no_sha)
    idx.save(out, meta={"builder": "scripts/build_kg_index.py", "dataset": args.dataset})

    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    summary = idx.summary()
    report = {
        "dataset": args.dataset,
        "inputs": [relpath(p) for p in inputs],
        "output": relpath(out),
        "index_bytes": size,
        "build": stats.to_dict(),
        "summary": summary,
        "seconds_total": round(time.time() - t0, 2),
    }
    write_json(root / "reports" / "stats" / f"kg_index_{args.dataset}.json", report)

    log("")
    log(f"三元组      : {human_int(idx.n_triples)}")
    log(f"实体        : {human_int(idx.n_entities)}")
    log(f"关系        : {human_int(idx.n_relations)}")
    log(f"最大出度/入度: {human_int(stats.max_degree_out)} / {human_int(stats.max_degree_in)}")
    log(f"索引体积    : {human_bytes(size)}")
    log(f"索引目录    : {relpath(out)}")
    if summary.get("top_relations"):
        log("高频关系 Top5：")
        for r in summary["top_relations"][:5]:
            log(f"  {r['relation']:<60s} {r['triples']:>10,} ({r['share']:.2%})")
    for thr in (100, 1000, 10000):
        log(f"度数 > {thr:>5} 的关系数: {summary[f'relations_degree_gt_{thr}']:>5}  "
            f"覆盖三元组 {summary[f'triples_in_relations_degree_gt_{thr}']:,}")
    log(f"统计已写入 reports/stats/kg_index_{args.dataset}.json")
    log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
