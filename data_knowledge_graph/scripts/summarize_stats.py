#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把各 split 的统计汇总成 Markdown 表格（写报告 / 交接时直接用）。

用法::

    python scripts/summarize_stats.py                # 打印到终端
    python scripts/summarize_stats.py --out reports/A_统计汇总.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fog.utils import project_root, read_json, write_json  # noqa: E402


def fmt(v, nd=2, pct=False):
    if v is None:
        return "—"
    if pct:
        return f"{v * 100:.1f}%"
    if isinstance(v, float):
        return f"{v:,.{nd}f}"
    return f"{v:,}"


def main(argv=None) -> int:
    root = project_root()
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    rows = []
    for ds in ("webqsp", "cwq"):
        d = root / "data" / "processed" / ds
        if not d.exists():
            continue
        for f in sorted(d.glob("stats_*.json")):
            st = read_json(f)
            split = st["split"]
            c = st["stats"]["counters"]
            m = st["stats"]["means"]
            n = st["stats"]["n"]
            sub = st.get("subgraph") or {}
            subn = sub.get("n", 0)
            subm = sub.get("means", {}) if subn else {}
            rows.append({
                "dataset": ds, "split": split, "n": n,
                "gold_matched": int(c.get("gold_matched", {}).get("True", 0)),
                "gold_matched_pct": int(c.get("gold_matched", {}).get("True", 0)) / max(n, 1),
                "src_in_kg_pct": int(c.get("source_in_kg", {}).get("True", 0)) / max(n, 1),
                "answers_in_kg": m.get("answers_in_kg_frac"),
                "gold_triples": m.get("gold_n_triples"),
                "match_methods": c.get("gold_match_method", {}),
                "source_methods": c.get("source_method", {}),
                "sub_n": subn,
                "sub_nodes": subm.get("n_nodes"),
                "sub_edges": subm.get("n_edges"),
                "sub_gold_recall": subm.get("gold_recall_in_subgraph"),
                "sub_truncated": int(sub.get("counters", {}).get("truncated", {}).get("True", 0)),
                "sub_hub_skipped": subm.get("hub_skipped"),
                "qa_bytes": st.get("qa_bytes"),
                "subgraph_sampled": st.get("subgraph_sampled"),
            })

    lines = []
    lines.append("# 各 split 统计汇总（自动生成）\n")
    lines.append("> 由 `python scripts/summarize_stats.py` 生成，数据源 `data/processed/*/stats_*.json`。\n")
    lines.append("## 1. 对齐与覆盖\n")
    lines.append("| 数据集 | split | 题数 | 黄金对齐 | 对齐率 | 源实体在 KG | 平均答案数 | 平均黄金三元组 |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        lines.append(f"| {r['dataset']} | {r['split']} | {r['n']:,} | {r['gold_matched']:,} | "
                     f"{fmt(r['gold_matched_pct'], pct=True)} | {fmt(r['src_in_kg_pct'], pct=True)} | "
                     f"{fmt(r['answers_in_kg'], pct=True)} | {fmt(r['gold_triples'])} |")

    lines.append("\n## 2. 展开子图（候选池）规模\n")
    lines.append("| 数据集 | split | 统计题数 | 平均节点 | 平均边 | 黄金边召回 | 截断题数 | 枢纽跳过 |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        if not r["sub_n"]:
            lines.append(f"| {r['dataset']} | {r['split']} | 0 | — | — | — | — | — |")
            continue
        lines.append(f"| {r['dataset']} | {r['split']} | {r['sub_n']:,} | {fmt(r['sub_nodes'], 0)} | "
                     f"{fmt(r['sub_edges'], 0)} | {fmt(r['sub_gold_recall'], pct=True)} | "
                     f"{r['sub_truncated']:,} | {fmt(r['sub_hub_skipped'], 0)} |")

    lines.append("\n## 3. 对齐方式与源实体来源\n")
    for r in rows:
        lines.append(f"\n**{r['dataset']}/{r['split']}**（n={r['n']:,}）")
        lines.append(f"- 对齐方式：`{r['match_methods']}`")
        lines.append(f"- 源实体来源：`{r['source_methods']}`")
        if r["subgraph_sampled"] is not None:
            lines.append(f"- 子图统计采样：前 {r['subgraph_sampled']:,} 条")

    text = "\n".join(lines) + "\n"
    if args.out:
        outp = root / args.out if not Path(args.out).is_absolute() else Path(args.out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(text, encoding="utf-8")
        print(f"已写入 {outp}")
    else:
        print(text)
    write_json(root / "reports" / "stats" / "summary_table.json", rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
