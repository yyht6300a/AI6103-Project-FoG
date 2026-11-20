# -*- coding: utf-8 -*-
"""成员 A 的数据契约（schema）。

这些 dataclass 同时承担两个作用：

1. 流水线内部的结构化类型；
2. 与成员 B/C/D/E 的**接口契约**——``to_dict()`` 的键名即 JSONL 产物的字段名，
   下游只依赖这里定义的字段，不依赖任何内部实现。

字段总览见 ``docs/DATA_CONTRACT.md``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- 常量
#: 源实体的来源，用于统计与排错（决策点 1）
SOURCE_FROM_ANNOTATION = "annotation"      # 数据集自带话题实体
SOURCE_FROM_LINKING = "linking"            # 用问题文本对 KG 实体词表做提及匹配
SOURCE_FROM_SPARQL = "sparql_constant"     # 退化为 SPARQL 模式中的根常量 mid
SOURCE_FROM_FALLBACK = "fallback"          # 仍未命中，直接用 mid 字符串

#: 黄金三元组的角色（训练时可只取 path）
ROLE_PATH = "path"                # 从源实体到答案的主链
ROLE_CONSTRAINT = "constraint"    # 挂在链上节点的约束三元组


@dataclass(frozen=True)
class Triple:
    """（头实体, 关系, 尾实体）。名字空间与 KG 索引完全一致。"""

    h: str
    r: str
    t: str

    def as_list(self) -> List[str]:
        return [self.h, self.r, self.t]

    def key(self) -> Tuple[str, str, str]:
        return (self.h, self.r, self.t)


@dataclass
class SourceEntity:
    """源实体（检索起点）。``name`` 是送入嵌入模型的字符串。"""

    name: str
    mid: Optional[str] = None
    kg_id: Optional[int] = None          # KG 索引中的实体 id（None 表示 KG 里没有）
    method: str = SOURCE_FROM_ANNOTATION
    mention: Optional[str] = None        # 问题中的字面提及
    in_kg: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "mid": self.mid,
            "kg_id": self.kg_id,
            "method": self.method,
            "mention": self.mention,
            "in_kg": self.in_kg,
        }


@dataclass
class GoldStep:
    """黄金路径的一步。

    ``direction`` 为 ``"fwd"`` 表示沿 KG 边 (head -> tail) 前进，
    ``"bwd"`` 表示逆向（尾实体 -> 头实体）。mid 字段是数据集标注里的
    Freebase mid，名字空间解析结果放在 :class:`GoldPath` 上。
    """

    relation: str
    head_mid: Optional[str] = None
    tail_mid: Optional[str] = None
    direction: str = "fwd"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GoldPath:
    """黄金推理路径（由数据集标注反推）。

    Attributes
    ----------
    steps:
        有序的关系步序列（mid 空间），直接来自 SPARQL / S-expression。
    triples:
        **名字空间**下对齐后的黄金三元组，是训练用的正样本。
    matched:
        主链是否在 KG 中被完整匹配到。
    constraints:
        挂在链上节点的约束三元组（同样名字空间），角色为 ``constraint``。
    """

    steps: List[GoldStep] = field(default_factory=list)
    triples: List[Triple] = field(default_factory=list)
    constraints: List[Triple] = field(default_factory=list)
    matched: bool = False
    match_method: str = "none"       # exact / relaxed / shortest / none
    n_path_hops: int = 0
    errors: List[str] = field(default_factory=list)

    @property
    def all_triples(self) -> List[Triple]:
        out: List[Triple] = list(self.triples)
        seen = {t.key() for t in out}
        for t in self.constraints:
            if t.key() not in seen:
                out.append(t)
                seen.add(t.key())
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "steps": [s.to_dict() for s in self.steps],
            "triples": [t.as_list() for t in self.triples],
            "constraints": [t.as_list() for t in self.constraints],
            "matched": self.matched,
            "match_method": self.match_method,
            "n_path_hops": self.n_path_hops,
            "errors": self.errors,
        }


@dataclass
class QAExample:
    """一条问题样本（含标注、源实体、黄金路径）。"""

    qid: str
    dataset: str
    split: str
    question: str
    answers: List[str] = field(default_factory=list)
    answer_mids: List[str] = field(default_factory=list)
    source_entities: List[SourceEntity] = field(default_factory=list)
    gold: Optional[GoldPath] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self, with_gold: bool = True) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "qid": self.qid,
            "dataset": self.dataset,
            "split": self.split,
            "question": self.question,
            "answers": self.answers,
            "answer_mids": self.answer_mids,
            "source_entities": [s.to_dict() for s in self.source_entities],
        }
        if with_gold:
            d["gold"] = self.gold.to_dict() if self.gold is not None else None
        if self.extra:
            d["extra"] = self.extra
        return d


@dataclass
class SubgraphNode:
    gid: int                 # KG 全局实体 id
    name: str
    dist: int                # 到源实体的最短跳数
    is_source: bool = False
    is_answer: bool = False
    idx: int = -1            # 局部索引（= 在 nodes 列表中的位置）

    def to_dict(self) -> Dict[str, Any]:
        return {"gid": self.gid, "name": self.name, "dist": self.dist,
                "is_source": int(self.is_source), "is_answer": int(self.is_answer)}


@dataclass
class SubgraphEdge:
    """展开子图中的一条边（用局部节点下标表示，体积小且便于批处理）。

    * ``s`` / ``r`` / ``t``：**KG 原始方向**的三元组 ``(nodes[s], r, nodes[t])``；
    * ``ds`` / ``dt``：两端到源实体的跳数；
    * ``src`` / ``dst``：FoG §3.1 的**朝内定向**结果（远 → 近），FFP 直接用它做消息传递；
    * ``gold``：该边是否落在黄金推理路径上（训练正样本标记）。
    """

    s: int
    r: str
    t: int
    ds: int
    dt: int
    src: int
    dst: int
    gold: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {"s": self.s, "r": self.r, "t": self.t, "ds": self.ds, "dt": self.dt,
                "src": self.src, "dst": self.dst, "g": self.gold}


@dataclass
class Subgraph:
    """一条问题的展开子图（局部编号）。"""

    qid: str
    nodes: List[SubgraphNode] = field(default_factory=list)
    edges: List[SubgraphEdge] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "qid": self.qid,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "stats": self.stats,
        }

    def name_to_local(self) -> Dict[str, int]:
        return {n.name: i for i, n in enumerate(self.nodes)}

    def triple_names(self, e: SubgraphEdge) -> Tuple[str, str, str]:
        return (self.nodes[e.s].name, e.r, self.nodes[e.t].name)


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)
