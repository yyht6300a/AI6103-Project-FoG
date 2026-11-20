#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成成员 A 的数据产物（交付物 1/3/4）。

流程::

    原始标注 ──▶ mid2name 字典 ──┐
                                 ├─▶ 源实体 + 黄金三元组 + 子图统计 ──▶ jsonl.gz
    原始子图 ──▶ KG 索引 ────────┘

产物（``data/processed/<dataset>/``）：

* ``qa_<split>.jsonl.gz``    每条问题一行：问题、答案、源实体、黄金路径、子图统计
* ``gold_<split>.jsonl.gz``  只要黄金三元组（训练正样本），体积很小
* ``subgraphs/<split>/``     可选：前 N 条问题的完整子图（供 C/D 直接调试）
* ``stats_<split>.json``     该 split 的统计（覆盖率、平均规模、截断率）

用法::

    python scripts/prepare_dataset.py --dataset webqsp
    python scripts/prepare_dataset.py --dataset cwq --limit 200         # 快速冒烟
    python scripts/prepare_dataset.py --dataset webqsp --materialize-subgraphs 500
    python scripts/prepare_dataset.py --dataset all                     # 两个数据集
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fog.datasets.loader import DatasetSpec  # noqa: E402
from fog.kg.tsv_index import TripleIndex  # noqa: E402
from fog.naming import EntityLinker, MidNameResolver  # noqa: E402
from fog.pipeline import AlignConfig, PrepareConfig, prepare_split  # noqa: E402
from fog.subgraph import SubgraphConfig  # noqa: E402
from fog.utils import Logger, human_bytes, project_root, read_json, relpath, write_json  # noqa: E402


def _load_cfg(root: Path, name: str) -> dict:
    path = root / "configs" / f"{name}.json"
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


def build_mid2name(root: Path, log) -> MidNameResolver:
    """从**全部**标注里收集 mid -> 英文名（决策点 2 的映射表）。"""
    resolver = MidNameResolver()
    qa_dir = root / "data" / "raw" / "qa"
    for fname in ("WebQSP.train.json", "WebQSP.test.json"):
        p = qa_dir / fname
        if p.exists():
            data = read_json(p)
            qs = data["Questions"] if isinstance(data, dict) else data
            MidNameResolver.from_webqsp(qs, base=resolver)
    for fname in ("ComplexWebQuestions_train.json", "ComplexWebQuestions_dev.json"):
        p = qa_dir / fname
        if p.exists():
            data = read_json(p)
            recs = data["Questions"] if isinstance(data, dict) else data
            MidNameResolver.from_cwq(recs, base=resolver)
    out = root / "data" / "interim" / "mid2name.json"
    resolver.save(out)
    log(f"mid2name: {len(resolver):,} 条 -> {relpath(out)} ({human_bytes(out.stat().st_size)})")
    return resolver


def main(argv: list[str] | None = None) -> int:
    root = project_root()
    # 调试用：FOG_WATCHDOG=60 时每 60 秒把调用栈 dump 到 reports/logs/watchdog.log，
    # 用来定位"卡住但 CPU 不高"的问题（曾用它发现 stdout 管道阻塞）。
    if os.environ.get("FOG_WATCHDOG"):
        import faulthandler

        (root / "reports" / "logs").mkdir(parents=True, exist_ok=True)
        _wd = open(root / "reports" / "logs" / "watchdog.log", "a", encoding="utf-8")
        faulthandler.dump_traceback_later(int(os.environ["FOG_WATCHDOG"]), repeat=True, file=_wd)

    ap = argparse.ArgumentParser(description="生成 FoG 复现的数据产物（成员 A）")
    ap.add_argument("--dataset", choices=["webqsp", "cwq", "all"], default="all")
    ap.add_argument("--kg-index", default=None, help="覆盖配置里的 KG 索引目录")
    ap.add_argument("--limit", type=int, default=None, help="每个 split 只处理前 N 条")
    ap.add_argument("--materialize-subgraphs", type=int, default=None,
                    help="为前 N 条问题物化完整子图 JSON（0 表示不物化）")
    ap.add_argument("--splits", nargs="*", default=None, help="只处理这些 split")
    ap.add_argument("--no-official-test", action="store_true", help="跳过 CWQ 官方无答案 test")
    ap.add_argument("--allow-shortest-fallback", action="store_true",
                    help="允许放弃关系约束的最短路兜底（默认关闭，避免假阳性）")
    ap.add_argument("--reuse-mid2name", action="store_true",
                    help="复用已有 data/interim/mid2name.json（并行跑多个数据集时用）")
    args = ap.parse_args(argv)

    log = Logger("prepare", logfile=root / "reports" / "logs" / f"prepare_dataset_{args.dataset}.log")
    t0 = time.time()

    mid_path = root / "data" / "interim" / "mid2name.json"
    if args.reuse_mid2name and mid_path.exists():
        mid2name = MidNameResolver.load(mid_path)
        log(f"== 复用 mid -> name 映射（{len(mid2name):,} 条）==")
    else:
        log("== 构建 mid -> name 映射 ==")
        mid2name = build_mid2name(root, log)

    names = ["webqsp", "cwq"] if args.dataset == "all" else [args.dataset]
    reports = []
    for name in names:
        cfgd = _load_cfg(root, name)
        kg_dir = root / (args.kg_index or cfgd["kg_index"])
        if not (kg_dir / "meta.json").exists():
            log(f"!! 找不到 KG 索引 {relpath(kg_dir)}，先运行 scripts/build_kg_index.py")
            return 2
        log(f"\n== [{name}] 载入 KG 索引 {relpath(kg_dir)} ==")
        kg = TripleIndex.load(kg_dir)
        log(f"  {kg}")

        linker = EntityLinker(kg) if cfgd["format"] == "cwq" else EntityLinker(kg)

        cfg = PrepareConfig(
            dataset=cfgd["dataset"],
            splits=dict(cfgd["splits"]),
            kg_index=relpath(kg_dir),
            out_dir=relpath(root / cfgd["out_dir"]),
            format=cfgd["format"],
            limit=args.limit,
            subgraph=SubgraphConfig(**{k: v for k, v in cfgd["subgraph"].items()
                                       if k in SubgraphConfig.__dataclass_fields__}),
            align=AlignConfig(
                **{k: v for k, v in cfgd["align"].items()
                   if k in AlignConfig.__dataclass_fields__ and k != "allow_shortest_fallback"},
                allow_shortest_fallback=args.allow_shortest_fallback,
            ),
            materialize_subgraphs=(args.materialize_subgraphs
                                   if args.materialize_subgraphs is not None
                                   else cfgd.get("materialize_subgraphs", 0)),
            official_test_split=(cfgd.get("official_test_split") or {}).get("name"),
            subgraph_sample=dict(cfgd.get("subgraph_sample") or {}),
        )

        spec = DatasetSpec(name=cfgd["dataset"], format=cfgd["format"], files=dict(cfgd["splits"]))
        splits = args.splits or list(cfgd["splits"])
        for split in splits:
            fname = cfgd["splits"][split]
            raw = root / "data" / "raw" / "qa" / fname
            if not raw.exists():
                log(f"  !! 缺少 {relpath(raw)}，跳过（先跑 download_data.py）")
                continue
            reports.append(prepare_split(spec, split, raw, kg, linker, mid2name, cfg, log=log))

        # CWQ 官方 test（无答案）单独处理，只做导出
        ot = cfgd.get("official_test_split")
        if ot and not args.no_official_test and not args.splits:
            raw = root / "data" / "raw" / "qa" / ot["file"]
            if raw.exists():
                reports.append(prepare_split(spec, ot["name"], raw, kg, linker, mid2name,
                                             cfg, log=log, with_answers=False))

    summary = {
        "generated_by": "scripts/prepare_dataset.py",
        "limit": args.limit,
        "seconds": round(time.time() - t0, 2),
        "reports": reports,
    }
    out = root / "reports" / "stats" / f"prepare_{args.dataset}.json"
    write_json(out, summary)
    log(f"\n=== 完成，用时 {time.time() - t0:.1f}s ===")
    for r in reports:
        st = r["stats"]
        gm = st["counters"].get("gold_matched", {})
        n = max(st["n"], 1)
        log(f"  {r['dataset']}/{r['split']:<20s} n={st['n']:>6,}  "
            f"gold matched={int(gm.get('True', 0)) / n:6.1%}  "
            f"src in KG={int(st['counters'].get('source_in_kg', {}).get('True', 0)) / n:6.1%}")
    log(f"统计汇总 -> {relpath(out)}")
    log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
