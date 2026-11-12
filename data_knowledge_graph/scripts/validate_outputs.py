#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""产物自检：字段完整性、引用一致性、子图不变量。

跑完 ``prepare_dataset.py`` 之后必须过这一关，任何一项 FAIL 都说明产物不能交给下游。

检查项
------
A. **文件完整性**：每个 split 的 qa / gold / stats 文件存在且非空
B. **JSONL 字段契约**：必需键齐全、类型正确、长度一致
C. **源实体**：`name` 非空；`in_kg=True` 时 `kg_id` 必须能在索引里查到同名实体
D. **黄金三元组**：`matched=True` 时必须非空；且每条都能在 KG 里找到
E. **子图不变量**：`s/t` 下标越界、`src/dst` 朝内定向（远→近）、`gold` 标记数与统计一致
F. **统计自洽**：`stats_<split>.json` 里的 n 与 JSONL 行数一致

用法::

    python scripts/validate_outputs.py
    python scripts/validate_outputs.py --dataset webqsp --max-questions 500
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fog.kg.tsv_index import TripleIndex  # noqa: E402
from fog.utils import project_root, read_json, relpath, write_json  # noqa: E402

REQUIRED_KEYS = {"qid": str, "dataset": str, "split": str, "question": str,
                 "answers": list, "answer_mids": list, "source_entities": list}
GOLD_KEYS = {"steps": list, "triples": list, "constraints": list, "matched": bool,
             "match_method": str, "n_path_hops": int}


class Checker:
    def __init__(self) -> None:
        self.fails: list[str] = []
        self.warns: list[str] = []
        self.n_checks = 0

    def check(self, cond: bool, msg: str, warn: bool = False) -> bool:
        self.n_checks += 1
        if not cond:
            (self.warns if warn else self.fails).append(msg)
        return cond

    def summary(self) -> dict:
        return {"checks": self.n_checks, "failures": len(self.fails),
                "warnings": len(self.warns), "fail_detail": self.fails[:40],
                "warn_detail": self.warns[:40]}


def iter_jsonl_gz(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def validate_dataset(root: Path, ds: str, kg: TripleIndex, ck: Checker,
                     max_questions: int | None, log) -> dict:
    out_dir = root / "data" / "processed" / ds
    if not out_dir.exists():
        ck.check(False, f"{ds}: 目录不存在 {relpath(out_dir)}")
        return {}

    info = {}
    for qa_file in sorted(out_dir.glob("qa_*.jsonl.gz")):
        split = qa_file.name[len("qa_"):-len(".jsonl.gz")]
        gold_file = out_dir / f"gold_{split}.jsonl.gz"
        stats_file = out_dir / f"stats_{split}.json"
        ck.check(gold_file.exists(), f"{ds}/{split}: 缺 gold 文件")
        ck.check(stats_file.exists(), f"{ds}/{split}: 缺 stats 文件")

        n = 0
        n_matched = 0
        n_src_ok = 0
        n_bad_gold = 0
        for rec in iter_jsonl_gz(qa_file):
            n += 1
            if max_questions and n > max_questions:
                break
            for k, typ in REQUIRED_KEYS.items():
                ck.check(k in rec and isinstance(rec[k], typ),
                         f"{ds}/{split} qid={rec.get('qid')}: 字段 {k} 缺失或类型错误")
            gold = rec.get("gold")
            if gold is None:
                continue
            for k, typ in GOLD_KEYS.items():
                ck.check(k in gold and isinstance(gold[k], typ),
                         f"{ds}/{split} qid={rec.get('qid')}: gold.{k} 缺失或类型错误")
            if gold.get("matched"):
                n_matched += 1
                if not ck.check(len(gold["triples"]) > 0,
                                f"{ds}/{split} qid={rec.get('qid')}: matched 但无黄金三元组"):
                    continue
                for tr in gold["triples"]:
                    if not ck.check(isinstance(tr, list) and len(tr) == 3,
                                    f"{ds}/{split} qid={rec.get('qid')}: 黄金三元组格式错误"):
                        continue
                    if not kg.has_triple(tr[0], tr[1], tr[2]):
                        n_bad_gold += 1
                        ck.check(False, f"{ds}/{split} qid={rec.get('qid')}: 黄金三元组不在 KG 中: {tr}",
                                 warn=True)
            for se in rec.get("source_entities") or []:
                if se.get("in_kg"):
                    n_src_ok += 1
                    eid = kg.entity_id(se["name"])
                    ck.check(eid is not None and eid == se.get("kg_id"),
                             f"{ds}/{split} qid={rec.get('qid')}: 源实体 kg_id 不一致 {se}")

        # 子图不变量（只抽样）
        sub_dir = out_dir / "subgraphs" / split
        n_sub = 0
        if sub_dir.exists():
            for f in sorted(sub_dir.glob("*.json.gz"))[:50]:
                with gzip.open(f, "rt", encoding="utf-8") as fh:
                    sub = json.load(fh)
                n_sub += 1
                nn = len(sub["nodes"])
                bad_idx = [e for e in sub["edges"]
                           if not (0 <= e["s"] < nn and 0 <= e["t"] < nn
                                   and 0 <= e["src"] < nn and 0 <= e["dst"] < nn)]
                ck.check(not bad_idx, f"{f.name}: 边下标越界 {len(bad_idx)} 条")
                bad_dir = [e for e in sub["edges"]
                           if e["ds"] != e["dt"] and
                           sub["nodes"][e["src"]]["dist"] <= sub["nodes"][e["dst"]]["dist"]]
                ck.check(not bad_dir, f"{f.name}: 朝内定向错误 {len(bad_dir)} 条")
                gold_edges = sum(int(e["g"]) for e in sub["edges"])
                ck.check(gold_edges == sub["stats"]["n_gold_edges"],
                         f"{f.name}: gold 标记数 {gold_edges} != stats {sub['stats']['n_gold_edges']}")
                ds_ok = all(sub["nodes"][e["s"]]["dist"] == e["ds"] and
                            sub["nodes"][e["t"]]["dist"] == e["dt"] for e in sub["edges"])
                ck.check(ds_ok, f"{f.name}: 边的 ds/dt 与节点 dist 不一致")
                src_nodes = [i for i, x in enumerate(sub["nodes"]) if x["is_source"]]
                ck.check(all(sub["nodes"][i]["dist"] == 0 for i in src_nodes),
                         f"{f.name}: is_source 节点 dist != 0")
                ck.check(len(sub["edges"]) > 0, f"{f.name}: 空边集")

        n_lines = sum(1 for _ in iter_jsonl_gz(qa_file))
        if stats_file.exists():
            st = read_json(stats_file)
            ck.check(st.get("n_questions") == n_lines,
                     f"{ds}/{split}: stats.n_questions={st.get('n_questions')} != 行数 {n_lines}")
            if not max_questions:
                gm = st.get("stats", {}).get("counters", {}).get("gold_matched", {})
                ck.check(int(gm.get("True") or 0) == n_matched,
                         f"{ds}/{split}: stats 里的 gold_matched True={gm.get('True')} != 实际 {n_matched}")

        info[split] = {"n": n_lines, "n_matched": n_matched, "n_sub_check": n_sub,
                       "bad_gold_triples": n_bad_gold}
        log(f"  {ds}/{split:<22s} n={n_lines:>6,}  matched={n_matched:>6,}  "
            f"样本中子图={n_sub}  黄金三元组越界={n_bad_gold}")
    return info


def main(argv=None) -> int:
    root = project_root()
    ap = argparse.ArgumentParser(description="产物自检")
    ap.add_argument("--dataset", choices=["webqsp", "cwq", "all"], default="all")
    ap.add_argument("--max-questions", type=int, default=None, help="每个 split 只查前 N 条")
    args = ap.parse_args(argv)

    ck = Checker()
    print("加载 KG 索引 ...")
    kg = TripleIndex.load(root / "data" / "processed" / "kg" / "index_all")

    ds_list = ["webqsp", "cwq"] if args.dataset == "all" else [args.dataset]
    info = {}
    for ds in ds_list:
        print(f"\n== 检查 {ds} ==")
        info[ds] = validate_dataset(root, ds, kg, ck, args.max_questions, print)

    # 其他关键文件
    ck.check((root / "data/interim/mid2name.json").exists(), "缺 data/interim/mid2name.json")
    for f in ("head.npy", "rel.npy", "tail.npy", "entities.tsv", "meta.json"):
        ck.check((root / "data/processed/kg/index_all" / f).exists(), f"KG 索引缺 {f}")

    rep = {"datasets": info, **ck.summary()}
    write_json(root / "reports" / "stats" / "validation.json", rep)
    print("\n" + "=" * 60)
    print(f"检查 {ck.n_checks} 项：{len(ck.fails)} 项失败，{len(ck.warns)} 项警告")
    if ck.warns:
        for w in ck.warns[:10]:
            print("  [warn]", w)
    if ck.fails:
        for f in ck.fails[:20]:
            print("  [FAIL]", f)
        print("\n自检未通过")
        return 1
    print("自检通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
