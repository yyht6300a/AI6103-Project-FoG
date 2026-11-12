#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一键跑完成员 A 的全部流水线（下载 → 建索引 → 数据生成 → 自检）。

等价于依次执行::

    python scripts/download_data.py
    python scripts/build_kg_index.py
    python scripts/prepare_dataset.py --dataset all
    python scripts/validate_outputs.py

用法::

    python scripts/run_all.py                 # 全流程
    python scripts/run_all.py --skip-download # 数据已就位
    python scripts/run_all.py --limit 300     # 快速验证（每 split 300 条）
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str], label: str) -> int:
    print(f"\n{'=' * 70}\n== {label}\n== {' '.join(cmd)}\n{'=' * 70}", flush=True)
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=str(ROOT))
    print(f"-- {label} 结束，退出码 {proc.returncode}，用时 {time.time() - t0:.1f}s", flush=True)
    return proc.returncode


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-download", action="store_true")
    ap.add_argument("--skip-index", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="每个 split 只处理前 N 条（调试）")
    ap.add_argument("--materialize-subgraphs", type=int, default=100)
    args = ap.parse_args(argv)

    py = sys.executable
    steps: list[tuple[str, list[str]]] = []
    if not args.skip_download:
        steps.append(("① 下载原始数据", [py, "scripts/download_data.py"]))
    if not args.skip_index:
        steps.append(("② 构建 KG 索引", [py, "scripts/build_kg_index.py"]))
    prep = [py, "scripts/prepare_dataset.py", "--dataset", "all",
            "--materialize-subgraphs", str(args.materialize_subgraphs)]
    if args.limit:
        prep += ["--limit", str(args.limit)]
    steps.append(("③ 生成数据产物", prep))
    steps.append(("④ 产物自检", [py, "scripts/validate_outputs.py"]))
    steps.append(("⑤ 统计汇总", [py, "scripts/summarize_stats.py",
                                  "--out", "reports/A_统计汇总.md"]))

    for label, cmd in steps:
        rc = run(cmd, label)
        if rc != 0:
            print(f"\n!! 步骤失败：{label}（退出码 {rc}），流水线中止")
            return rc
    print("\n全部完成。产物在 data/processed/，报告在 reports/。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
