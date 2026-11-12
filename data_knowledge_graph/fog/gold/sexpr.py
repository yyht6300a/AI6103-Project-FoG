# -*- coding: utf-8 -*-
"""从 CWQ 的 S-expression 反推黄金推理路径（决策点 3 的另一条路径）。

背景
----
《复现计划》把黄金路径的来源写成"由 WebQSP SPARQL 或 CWQ S-expression 反推"。
但实际下载到的官方 CWQ JSON（``ComplexWebQuestions_{train,dev}.json``）**没有** ``sexpr``
字段，只有 ``question`` / ``machine_question`` / ``sparql`` / ``answers``
（见 ``reports/A_数据核查.md`` 的记录）。因此生产管线对两个数据集统一走 SPARQL 模式解析
（:mod:`fog.gold.sparql`）。

本模块仍然完整实现 S-expression 解析，原因有三：
1. 若后续拿到带 ``sexpr`` 的 CWQ 版本（官方原版 zipped release 含该字段），可直接切换；
2. S-expression 是**有向关系链**的权威表达，用它交叉验证 SPARQL 解析结果很有价值；
3. 单元测试用论文 Figure 9/10 的案例做回归。

S-expression 的节点形态（CWQ 官方格式）::

    {"type": "TERM", "function": "<relation>", "inputs": [<child>], "output": ...}
    {"type": "ARG",  "value": "m.010vz"}          # 实体或字面量参数
    {"type": "JOIN", "children": [...]}           # 有序子节点
    {"type": "AND",  "children": [...]}           # 合取（约束分支）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

MID_RE = re.compile(r"^[mg]\.[A-Za-z0-9_]+$")

#: 仅作为"算子"出现、不对应 KG 关系的节点类型
_OPERATOR_TYPES = {
    "AND", "OR", "JOIN", "COUNT", "MAX", "MIN", "ARGMIN", "ARGMAX",
    "COMPARATIVE", "CONDITION", "CAST", "SELECT", "FILTER",
}


@dataclass
class SexprChain:
    """S-expression 线性化结果。"""

    tokens: List[tuple] = field(default_factory=list)   # ('arg', value) / ('rel', relation)
    relations: List[str] = field(default_factory=list)
    entities: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def direction(self) -> str:
        return "fwd"


def _is_mid(value: str) -> bool:
    return bool(MID_RE.match(value))


def _walk(node: Any, out: List[tuple], warnings: List[str], depth: int = 0) -> None:
    if depth > 40 or node is None:
        return
    if isinstance(node, str):
        out.append(("arg", node) if _is_mid(node) else ("rel", node))
        return
    if isinstance(node, list):
        for child in node:
            _walk(child, out, warnings, depth + 1)
        return
    if not isinstance(node, dict):
        return

    ntype = str(node.get("type", "")).upper()

    # 叶子：实体 / 字面量参数
    if ntype == "ARG" or ("value" in node and "function" not in node and "children" not in node):
        value = node.get("value", node.get("function"))
        if isinstance(value, str):
            out.append(("arg", value))
        return

    fn = node.get("function")
    if isinstance(fn, str) and ntype not in {"ARG"}:
        # TERM / CAST / COMPARATIVE 等：先展开输入，再记录关系（由深到浅 = 正向链）
        for key in ("inputs", "children"):
            if key in node:
                _walk(node[key], out, warnings, depth + 1)
        if ntype in _OPERATOR_TYPES and ntype not in {"CAST"}:
            if ntype in {"AND", "OR", "JOIN"}:
                return
        if fn and not _is_mid(fn):
            out.append(("rel", fn))
        return

    if "children" in node:
        _walk(node["children"], out, warnings, depth + 1)
        return
    if "inputs" in node:
        _walk(node["inputs"], out, warnings, depth + 1)
        return
    warnings.append(f"unknown sexpr node: {list(node)[:4]}")


def parse_sexpr(node: Any) -> SexprChain:
    """把 CWQ S-expression 线性化为 (实体, 关系) 序列。"""
    chain = SexprChain()
    if node is None:
        chain.warnings.append("empty sexpr")
        return chain
    _walk(node, chain.tokens, chain.warnings)

    # 规范化：合并连续 rel / 去重相邻同值
    norm: List[tuple] = []
    for kind, value in chain.tokens:
        if norm and norm[-1] == (kind, value):
            continue
        norm.append((kind, value))
    chain.tokens = norm
    chain.relations = [v for k, v in norm if k == "rel"]
    chain.entities = [v for k, v in norm if k == "arg" and _is_mid(v)]
    return chain


def parse_cwq_record(record: Dict[str, Any]) -> Optional[SexprChain]:
    """从一条 CWQ 记录里取 ``sexpr`` 并解析；没有该字段则返回 ``None``。"""
    for key in ("sexpr", "Sexpr", "s_expr", "sexprs"):
        if key in record and record[key]:
            value = record[key]
            if isinstance(value, list) and value:
                value = value[0]
            return parse_sexpr(value)
    return None
