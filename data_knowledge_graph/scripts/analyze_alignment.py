#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""黄金路径对齐的失败归因（写报告 + 给成员 E 估上界用）。

对每条问题给出一个**唯一的失败原因**，重要性递减：

1. ``no_answers``       数据集没有可用答案（CWQ 官方 test）
2. ``answers_not_in_kg`` 答案实体在子图里一个都找不到 → 黄金路径无从校验
3. ``no_chain``         SPARQL 解析不出任何关系链
4. ``source_not_in_kg``  源实体（含链接回退）不在 KG 中
5. ``chain_no_answer``   关系链在 KG 上走不到任何答案（多为关系方向/中间实体名字缺失）
6. ``ok``                对齐成功

用法::

    python scripts/analyze_alignment.py --dataset webqsp --split test --limit 300
    python scripts/analyze_alignment.py --dataset cwq --split test --limit 300
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fog.datasets.loader import DatasetSpec, chains_for_example, load_split  # noqa: E402
from fog.kg.tsv_index import TripleIndex  # noqa: E402
from fog.naming import EntityLinker, MidNameResolver  # noqa: E402
from fog.pipeline import AlignConfig, resolve_example  # noqa: E402
from fog.utils import Logger, project_root, relpath, write_json  # noqa: E402


def main(argv=None) -> int:
    root = project_root()
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="webqsp", choices=["webqsp", "cwq"])
    ap.add_argument("--split", default="test")
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--kg-index", default="data/processed/kg/index_all")
    args = ap.parse_args(argv)

    log = Logger("align", logfile=root / "reports" / "logs" / "analyze_alignment.log")
    cfg = json.loads((root / "configs" / f"{args.dataset}.json").read_text(encoding="utf-8-sig"))
    raw = root / "data" / "raw" / "qa" / cfg["splits"][args.split]

    kg = TripleIndex.load(root / args.kg_index)
    mid2name = MidNameResolver.load(root / "data" / "interim" / "mid2name.json")
    linker = EntityLinker(kg)
    spec = DatasetSpec(name=cfg["dataset"], format=cfg["format"], files=cfg["splits"])
    examples = load_split(spec, args.split, raw)
    if args.limit:
        examples = examples[: args.limit]
    align = AlignConfig(**{k: v for k, v in cfg["align"].items()
                           if k in AlignConfig.__dataclass_fields__})
    log(f"{args.dataset}/{args.split}: {len(examples)} 条  mid2name={len(mid2name):,}")

    reasons: Counter = Counter()
    methods: Counter = Counter()
    src_methods: Counter = Counter()
    examples_by_reason: dict[str, list] = {}
    n_src_in_kg = 0
    n_ans_in_kg_frac = []
    n_chain_hops = []

    for ex in examples:
        if not ex.answers:
            reason = "no_answers"
        else:
            ans_hit = sum(1 for a in ex.answers if kg.resolve_entity(a) is not None)
            n_ans_in_kg_frac.append(ans_hit / len(ex.answers))
            chains = chains_for_example(ex)
            infer = ex.extra.get("inferential_chain") or []
            if not chains and not infer:
                reason = "no_chain"
            else:
                res = resolve_example(ex, kg, linker, mid2name, align)
                src = res.sources[0] if res.sources else None
                if ans_hit == 0:
                    reason = "answers_not_in_kg"
                elif src is None or not src.in_kg:
                    reason = "source_not_in_kg"
                elif res.gold.matched:
                    reason = "ok"
                else:
                    reason = "chain_no_answer"
                if src is not None:
                    n_src_in_kg += int(src.in_kg)
                    src_methods[src.method] += 1
                methods[res.gold.match_method] += 1
                if res.gold.matched:
                    n_chain_hops.append(res.gold.n_path_hops)
        reasons[reason] += 1
        if reason != "ok":
            examples_by_reason.setdefault(reason, []).append({
                "qid": ex.qid, "question": ex.question[:120], "answers": ex.answers[:3],
            })

    n = len(examples)
    report = {
        "dataset": args.dataset, "split": args.split, "n": n,
        "reasons": {k: {"n": v, "pct": round(v / n, 4)} for k, v in reasons.most_common()},
        "match_methods": dict(methods.most_common()),
        "source_methods": dict(src_methods.most_common()),
        "source_in_kg_rate": round(n_src_in_kg / n, 4),
        "answers_in_kg_mean": round(statistics.mean(n_ans_in_kg_frac), 4) if n_ans_in_kg_frac else None,
        "chain_hops_mean": round(statistics.mean(n_chain_hops), 3) if n_chain_hops else None,
        "chain_hops_hist": dict(sorted(Counter(n_chain_hops).items())) if n_chain_hops else {},
        "examples_by_reason": {k: v[:5] for k, v in examples_by_reason.items()},
    }
    log("\n失败归因：")
    for k, v in reasons.most_common():
        log(f"  {k:<22s} {v:>5,}  {v / n:6.1%}")
    log(f"\n源实体方法：{dict(src_methods.most_common())}")
    log(f"源实体在 KG 比例：{report['source_in_kg_rate']:.1%}")
    log(f"答案在 KG 平均比例：{report['answers_in_kg_mean']:.1%}")
    log(f"对齐方式：{dict(methods.most_common())}")
    log(f"黄金链跳数：{report['chain_hops_hist']}")

    out = root / "reports" / "stats" / f"alignment_{args.dataset}_{args.split}.json"
    write_json(out, report)
    log(f"已写入 {relpath(out)}")
    log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
