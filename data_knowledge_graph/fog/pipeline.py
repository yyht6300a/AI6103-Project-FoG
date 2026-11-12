# -*- coding: utf-8 -*-
"""成员 A 的流水线：加载 → 源实体定位 → 黄金路径对齐 → 3 跳子图 → 落盘 + 统计。

一条问题的处理顺序（每个问题的产物完全相同，可复现）：

1. **源实体定位**（决策点 1）
   * WebQSP 有官方话题实体，直接用；
   * CWQ 没有，先用问题文本对 KG 词表做提及链接，再用黄金链做可行性校验；
   * 都不行时回退到 mid 字符串（决策点 2）并计入统计。
2. **黄金路径对齐**（决策点 3）
   把标注的关系链投影到 KG 名字空间，只接受"终点命中答案"的路径。
3. **3 跳展开**（决策点 7/8）
   关系度数过滤 + 分层 BFS + 朝内定向 + 黄金边标注。
4. **落盘**
   ``qa_<split>.jsonl.gz``（元数据 + 黄金三元组 + 子图统计）与
   ``gold_<split>.jsonl.gz``（纯黄金三元组），子图本体按需物化。
"""

from __future__ import annotations

import gzip
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .datasets.loader import DatasetSpec, chains_for_example, load_split
from .gold.matcher import bfs_fallback, constraint_triples, walk_chain
from .gold.sparql import chain_from_inferential_chain
from .kg.tsv_index import TripleIndex
from .naming import EntityLinker, MidNameResolver, fallback_source_entity, make_source_entity
from .schema import (
    GoldPath,
    QAExample,
    SOURCE_FROM_ANNOTATION,
    SOURCE_FROM_LINKING,
    Subgraph,
)
from .subgraph import SubgraphConfig, build_subgraph
from .utils import JsonlWriter, Progress, human_int, human_bytes, relpath, write_json


@dataclass
class AlignConfig:
    """源实体与黄金路径对齐的参数。"""

    max_chain_hops: int = 4           # 关系链最长跳数（超过视作解析异常）
    max_mentions: int = 8             # 提及链接最多考虑几个候选
    allow_shortest_fallback: bool = False   # 是否允许"放弃关系约束"的最短路兜底
    use_mid2name_check: bool = True   # 用 mid2name 交叉校验常量与提及是否一致
    max_branch: int = 40
    max_paths: int = 64

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class PrepareConfig:
    dataset: str
    splits: Dict[str, str]                 # split -> 原始文件名（相对 data/raw/qa）
    kg_index: str                          # KG 索引目录
    out_dir: str                           # 产物目录 data/processed/<dataset>
    format: str = "webqsp"                 # webqsp | cwq
    limit: Optional[int] = None            # 仅处理前 N 条（调试）
    subgraph: SubgraphConfig = field(default_factory=SubgraphConfig)
    align: AlignConfig = field(default_factory=AlignConfig)
    materialize_subgraphs: int = 0         # >0 时物化前 N 条问题的子图
    official_test_split: Optional[str] = None   # CWQ 官方 test（无答案），单独处理
    subgraph_sample: Dict[str, int] = field(default_factory=dict)
    """按 split 限制"计算子图统计"的问题条数（空 = 全部）。
    训练集动辄 3 万条，全量子图既慢又没必要（下游主要看 dev/test 的覆盖率），
    因此在 ``configs/*.json`` 里对 train 做采样，并在统计里记录 ``subgraph_sampled``。"""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dataset": self.dataset,
            "splits": dict(self.splits),
            "kg_index": self.kg_index,
            "out_dir": self.out_dir,
            "format": self.format,
            "limit": self.limit,
            "subgraph": self.subgraph.to_dict(),
            "align": self.align.to_dict(),
            "materialize_subgraphs": self.materialize_subgraphs,
            "official_test_split": self.official_test_split,
        }


# --------------------------------------------------------------------------- 对齐
@dataclass
class Resolved:
    sources: List[Any]
    gold: GoldPath
    chain_source: Optional[str] = None     # 用哪条链对齐成功（'inferential' / mid）

    @property
    def matched(self) -> bool:
        return self.gold.matched


def _chain_candidates(example: QAExample, mid2name: MidNameResolver,
                      cfg: AlignConfig) -> List[Tuple[str, List[Tuple[str, str]]]]:
    """返回 ``[(来源标签, 关系链), ...]``，主链在前。"""
    out: List[Tuple[str, List[Tuple[str, str]]]] = []
    if example.dataset == "webqsp":
        rels = example.extra.get("inferential_chain") or []
        if rels:
            out.append(("inferential", chain_from_inferential_chain(rels)))
    chains = chains_for_example(example)
    # 常量链按长度升序（短链更可能是主链）
    for mid, chain in sorted(chains.items(), key=lambda kv: (len(kv[1]), kv[0])):
        if not chain or len(chain) > cfg.max_chain_hops:
            continue
        if out and any(list(chain) == list(c) for _t, c in out):
            continue
        out.append((mid, chain))
    return out


def resolve_example(
    example: QAExample,
    kg: TripleIndex,
    linker: Optional[EntityLinker],
    mid2name: MidNameResolver,
    cfg: AlignConfig,
) -> Resolved:
    """定位源实体并对齐黄金路径。"""
    starts: List[Tuple[str, Optional[str], str]] = []
    for s in example.source_entities:
        if s.name:
            starts.append((s.name, s.mid, SOURCE_FROM_ANNOTATION))
    mentions = linker.link(example.question, cfg.max_mentions) if linker else []
    for m in mentions:
        if any(m.name == st[0] for st in starts):
            continue
        starts.append((m.name, None, SOURCE_FROM_LINKING))

    chains = _chain_candidates(example, mid2name, cfg)

    # 候选对按置信度排序
    pairs: List[Tuple[int, str, Optional[str], str, str, List[Tuple[str, str]]]] = []
    for name, mid, method in starts:
        for label, chain in chains:
            conf = 1
            if method == SOURCE_FROM_ANNOTATION and label == "inferential":
                conf = 5
            elif method == SOURCE_FROM_ANNOTATION:
                conf = 4
            if cfg.use_mid2name_check and label not in ("inferential",):
                known = mid2name.name_of(label)
                if known is not None:
                    if known.casefold() == name.casefold():
                        conf = max(conf, 6)
                    else:
                        conf = 0    # 名字对不上，直接排掉
            if conf == 0:
                continue
            pairs.append((conf, name, mid, method, label, chain))

    pairs.sort(key=lambda p: (-p[0], len(p[5])))

    gold = GoldPath(steps=list(example.gold.steps) if example.gold else [])
    best_source: Optional[Any] = None
    for conf, name, mid, method, label, chain in pairs:
        res = walk_chain(kg, [name], chain, example.answers, example.answer_mids,
                         max_branch=cfg.max_branch, max_paths=cfg.max_paths)
        if res.ok:
            gold.triples = res.triples
            gold.matched = True
            gold.match_method = res.method
            gold.n_path_hops = len(res.triples)
            best_source = make_source_entity(name, kg, mid=mid, method=method,
                                             mention=name if method == SOURCE_FROM_LINKING else None)
            resolved = Resolved(sources=[best_source], gold=gold, chain_source=label)
            _attach_constraints(example, kg, mid2name, resolved, res.nodes)
            return resolved

    # 全部失败：保留一个源实体候选 + 关系集受限兜底（逐个起点 × 前两条链）
    if starts:
        name, mid, method = starts[0]
        src = make_source_entity(name, kg, mid=mid, method=method)
    else:
        src = fallback_source_entity(example.extra.get("topic_mid"), mid2name, kg)
    gold.matched = False
    gold.match_method = "none"
    gold.errors.append("关系链未在 KG 中对齐到答案")

    for name, mid, method in starts[: cfg.max_mentions]:
        for _label, chain in chains[:2]:
            rel_set = {r for r, _d in chain}
            if not rel_set:
                continue
            res = bfs_fallback(kg, [name], example.answers, example.answer_mids,
                               max_depth=min(cfg.max_chain_hops, 4), relation_allow=rel_set)
            if res.ok:
                gold.triples = res.triples
                gold.matched = True
                gold.match_method = res.method
                gold.n_path_hops = len(res.triples)
                src = make_source_entity(name, kg, mid=mid, method=method)
                return Resolved(sources=[src], gold=gold, chain_source=None)
    if cfg.allow_shortest_fallback:
        res = bfs_fallback(kg, [src.name], example.answers, example.answer_mids,
                           max_depth=3, relation_allow=None)
        if res.ok:
            gold.triples = res.triples
            gold.matched = True
            gold.match_method = res.method
            gold.n_path_hops = len(res.triples)
    return Resolved(sources=[src], gold=gold, chain_source=None)


def _attach_constraints(example: QAExample, kg: TripleIndex, mid2name: MidNameResolver,
                        resolved: Resolved, path_nodes: Sequence[str]) -> None:
    """把 WebQSP 的 Constraints 投影成名字空间三元组。"""
    constraints = example.extra.get("constraints") or []
    if not constraints:
        return
    extra = constraint_triples(kg, list(path_nodes), constraints, mid2name.as_dict())
    if extra:
        resolved.gold.constraints = extra


# --------------------------------------------------------------------------- 统计
class StatAccumulator:
    """流式统计（mean 用 running sum，避免存全量）。"""

    def __init__(self) -> None:
        self.n = 0
        self.counters: Dict[str, Dict[str, int]] = {}
        self.sums: Dict[str, float] = {}
        self.hist: Dict[str, Dict[str, int]] = {}

    def count(self, key: str, value: Any) -> None:
        d = self.counters.setdefault(key, {})
        d[str(value)] = d.get(str(value), 0) + 1

    def add(self, key: str, value: float) -> None:
        self.sums[key] = self.sums.get(key, 0.0) + float(value)

    def bump(self, key: str, bucket: int) -> None:
        d = self.hist.setdefault(key, {})
        d[str(bucket)] = d.get(str(bucket), 0) + 1

    def result(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"n": self.n}
        out["counters"] = {k: dict(sorted(v.items(), key=lambda kv: -kv[1]))
                           for k, v in self.counters.items()}
        out["means"] = {k: round(v / self.n, 4) for k, v in self.sums.items() if self.n}
        out["histograms"] = {k: dict(sorted(v.items(), key=lambda kv: int(kv[0])))
                             for k, v in self.hist.items()}
        return out


# --------------------------------------------------------------------------- 主流程
def prepare_split(
    spec: DatasetSpec,
    split: str,
    raw_path: str | Path,
    kg: TripleIndex,
    linker: Optional[EntityLinker],
    mid2name: MidNameResolver,
    cfg: PrepareConfig,
    log=print,
    with_answers: bool = True,
) -> Dict[str, Any]:
    """处理一个 split，写出 ``qa_<split>.jsonl.gz`` / ``gold_<split>.jsonl.gz``。"""
    out_dir = Path(cfg.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    log(f"\n[{cfg.dataset}/{split}] 读取 {Path(raw_path).name}")
    examples = load_split(spec, split, raw_path, with_answers=with_answers)
    if cfg.limit:
        examples = examples[: cfg.limit]
    log(f"  载入 {human_int(len(examples))} 条问题")

    stats = StatAccumulator()
    stats.count("split", split)
    sub_stats = StatAccumulator()
    sub_dir = out_dir / "subgraphs" / split
    materialize = cfg.materialize_subgraphs

    qa_path = out_dir / f"qa_{split}.jsonl.gz"
    gold_path = out_dir / f"gold_{split}.jsonl.gz"
    prog = Progress(len(examples), every=500, label=f"{split}", log=log)

    with JsonlWriter(qa_path) as w_qa, JsonlWriter(gold_path) as w_gold:
        for i, ex in enumerate(examples):
            if not with_answers:
                # 官方 CWQ test：没有答案，也不做黄金路径对齐
                w_qa.write({
                    "qid": ex.qid, "dataset": ex.dataset, "split": split,
                    "question": ex.question, "answers": [], "answer_mids": [],
                    "source_entities": [], "gold": None,
                    "subgraph_stats": None,
                    "extra": {k: ex.extra.get(k) for k in
                              ("machine_question", "webqsp_id", "webqsp_question",
                               "compositionality_type", "sparql")},
                })
                stats.n += 1
                prog.tick()
                continue

            resolved = resolve_example(ex, kg, linker, mid2name, cfg.align)
            src = resolved.sources[0] if resolved.sources else None
            gold = resolved.gold

            if src is not None:
                stats.count("source_method", src.method)
                stats.count("source_in_kg", src.in_kg)
            stats.count("gold_matched", gold.matched)
            stats.count("gold_match_method", gold.match_method)
            stats.add("gold_n_triples", len(gold.triples))
            stats.add("gold_n_constraints", len(gold.constraints))
            stats.add("n_answers", len(ex.answers))
            n_ans_in_kg = sum(1 for a in ex.answers if kg.resolve_entity(a) is not None)
            stats.add("answers_in_kg_frac",
                      (n_ans_in_kg / len(ex.answers)) if ex.answers else 0.0)

            subgraph: Optional[Subgraph] = None
            sample_cap = cfg.subgraph_sample.get(split)
            want_subgraph = sample_cap is None or i < sample_cap
            if src is not None and gold.matched and want_subgraph:
                subgraph = build_subgraph(
                    kg, ex.qid, [src.name], gold.all_triples, ex.answers, cfg.subgraph)
                st = subgraph.stats
                sub_stats.n += 1
                for key in ("n_nodes", "n_edges", "n_lateral_edges", "n_gold_edges",
                            "rel_filtered_edges_bfs", "gold_recall_in_subgraph"):
                    if st.get(key) is not None:
                        sub_stats.add(key, st[key])
                sub_stats.count("truncated", st.get("truncated"))
                for layer, cnt in (st.get("layer_node_counts") or {}).items():
                    sub_stats.add(f"layer_nodes_{layer}", cnt)

                if materialize and i < materialize:
                    sub_dir.mkdir(parents=True, exist_ok=True)
                    with gzip.open(sub_dir / f"{_safe(ex.qid)}.json.gz", "wt",
                                   encoding="utf-8") as fh:
                        json.dump(subgraph.to_dict(), fh, ensure_ascii=False)

            record = ex.to_dict(with_gold=True)
            record["source_entities"] = [src.to_dict()] if src is not None else []
            record["gold"] = gold.to_dict()
            record["subgraph_stats"] = subgraph.stats if subgraph else None
            record["extra"] = {
                k: ex.extra.get(k) for k in
                ("parse_id", "parse_quality", "topic_mid", "inferential_chain",
                 "webqsp_id", "webqsp_question", "compositionality_type",
                 "machine_question")
                if ex.extra.get(k) is not None
            }
            w_qa.write(record)

            w_gold.write({
                "qid": ex.qid,
                "dataset": ex.dataset,
                "split": split,
                "source": [s.name for s in resolved.sources],
                "answers": ex.answers,
                "answer_mids": ex.answer_mids,
                "matched": gold.matched,
                "match_method": gold.match_method,
                "triples": [t.as_list() for t in gold.all_triples],
                "n_path_triples": len(gold.triples),
            })

            stats.n += 1
            prog.tick()
    prog.done()

    report = {
        "dataset": cfg.dataset,
        "split": split,
        "raw_file": relpath(raw_path),
        "n_questions": stats.n,
        "subgraph_sampled": cfg.subgraph_sample.get(split),
        "qa_file": relpath(qa_path),
        "gold_file": relpath(gold_path),
        "qa_bytes": qa_path.stat().st_size,
        "stats": stats.result(),
        "subgraph": sub_stats.result() if sub_stats.n else {"n": 0},
        "config": cfg.to_dict(),
        "seconds": round(time.time() - t0, 2),
    }
    write_json(out_dir / f"stats_{split}.json", report)
    log(f"  写出 {relpath(qa_path)} ({human_bytes(qa_path.stat().st_size)})  "
        f"黄金匹配率 {stats.counters.get('gold_matched', {}).get('True', 0) / max(stats.n, 1):.1%}")
    return report


def _safe(qid: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in str(qid))
