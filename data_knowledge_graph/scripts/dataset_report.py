#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""数据核查：只读原始标注，产出 ``reports/A_数据核查.md`` 所需的全部数字。

不依赖 KG 索引，几秒即可跑完；用来在动手写代码前先把"数据长什么样"钉死。

用法::

    python scripts/dataset_report.py
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fog.datasets.loader import DatasetSpec, load_split  # noqa: E402
from fog.utils import project_root, relpath, write_json  # noqa: E402

FILES = {
    "webqsp/train": ("webqsp", "train", "WebQSP.train.json"),
    "webqsp/test": ("webqsp", "test", "WebQSP.test.json"),
    "cwq/train": ("cwq", "train", "ComplexWebQuestions_train.json"),
    "cwq/dev(test)": ("cwq", "test", "ComplexWebQuestions_dev.json"),
    "cwq/official_test": ("cwq", "official_test", "ComplexWebQuestions_test.json"),
}


def describe(examples, name: str) -> dict:
    n = len(examples)
    empty_q = sum(1 for e in examples if not e.question.strip())
    empty_a = sum(1 for e in examples if not e.answers)
    n_answers = [len(e.answers) for e in examples]
    missing_parse = sum(1 for e in examples
                        if not (e.extra.get("inferential_chain") or e.extra.get("sparql_pattern")))
    warnings = Counter()
    for e in examples:
        for w in e.extra.get("sparql_warnings") or []:
            warnings[w.split(":")[0][:40]] += 1
    n_triples = [len((e.extra.get("sparql_pattern") or {}).get("triples") or []) for e in examples]
    return {
        "split": name,
        "n": n,
        "empty_question": empty_q,
        "no_answer": empty_a,
        "answer_count_mean": round(statistics.mean(n_answers), 3) if n else None,
        "answer_count_hist": dict(sorted(Counter(n_answers).items())),
        "multi_answer_frac": round(sum(1 for x in n_answers if x > 1) / n, 4) if n else None,
        "no_sparql_parse": missing_parse,
        "sparql_triples_mean": round(statistics.mean(n_triples), 3) if n else None,
        "sparql_warnings": dict(warnings.most_common(5)),
    }


def main() -> int:
    root = project_root()
    out = {}
    for key, (fmt, split, fname) in FILES.items():
        path = root / "data" / "raw" / "qa" / fname
        if not path.exists():
            print(f"[skip] {fname} 不存在")
            continue
        spec = DatasetSpec(name=fmt, format=fmt, files={})
        examples = load_split(spec, split, path)
        info = describe(examples, key)
        info["file"] = fname
        info["bytes"] = path.stat().st_size
        out[key] = info
        print(f"{key:<20s} n={info['n']:>6,} 无答案={info['no_answer']:>3} "
              f"平均答案数={info['answer_count_mean']} SPARQL解析失败={info['no_sparql_parse']:>3}")

    # WebQSP 解析质量分布
    wq = load_split(DatasetSpec("webqsp", "webqsp", {}), "train",
                    root / "data" / "raw" / "qa" / "WebQSP.train.json")
    out["webqsp_parse_quality"] = dict(Counter(
        e.extra.get("parse_quality") for e in wq).most_common())

    # CWQ compositionality 分布
    cq = load_split(DatasetSpec("cwq", "cwq", {}), "train",
                    root / "data" / "raw" / "qa" / "ComplexWebQuestions_train.json")
    out["cwq_compositionality"] = dict(Counter(
        e.extra.get("compositionality_type") for e in cq).most_common())

    # KG 文件行数
    for f in ("webqsp_subgraph.tsv", "cwq_subgraph.tsv"):
        p = root / "data" / "raw" / "kg" / f
        if p.exists():
            n = sum(1 for _ in open(p, "rb"))
            out.setdefault("kg_files", {})[f] = {"lines": n, "bytes": p.stat().st_size}

    write_json(root / "reports" / "stats" / "dataset_overview.json", out)
    print(f"\n已写入 {relpath(root / 'reports' / 'stats' / 'dataset_overview.json')}")
    print(json.dumps(out.get("webqsp_parse_quality"), ensure_ascii=False))
    print(json.dumps(out.get("cwq_compositionality"), ensure_ascii=False))
    print(json.dumps(out.get("kg_files"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
