# -*- coding: utf-8 -*-
"""从 SPARQL 反推黄金推理路径（决策点 3）。

数据集里能直接拿到的三样东西决定了本模块的做法：

* WebQSP 的 ``Parses[i].InferentialChain`` —— 官方给出的关系链（最权威，直接用）；
* WebQSP / CWQ 的 ``Sparql`` —— 完整的图模式，含变量、mid 常量、约束三元组。

因此 ``sparql.py`` 负责把 SPARQL 文本解析成一个**图模式**（变量 + 常量 + 带方向的三元组），
再在模式图上取"源常量 → 答案变量"的最短路径，得到有序的关系步序列；
真正的**名字空间对齐**交给 :mod:`fog.gold.matcher` 在 KG 上做。

解析要点（都是这两个数据集里真实出现的写法）：

* ``PREFIX ns: <http://rdf.freebase.com/ns/>`` 前缀声明；
* ``FILTER (...)`` / ``FILTER(NOT EXISTS { ... })`` —— 整段剔除，不参与模式；
* ``?x ns:rel ns:m.xxx .`` / ``ns:m.xxx ns:rel ?y .`` —— 变量与 mid 常量混排；
* ``OPTIONAL`` / ``UNION`` 里的花括号块 —— 整体跳过（本数据集的主链不依赖它们）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

NS = "ns:"
FULL_NS = "http://rdf.freebase.com/ns/"

VAR_RE = re.compile(r"^[?$]([A-Za-z_][A-Za-z0-9_]*)$")
MID_RE = re.compile(r"^(?:ns:|<http://rdf\.freebase\.com/ns/>)([mg]\.[A-Za-z0-9_]+)>?$")
URI_RE = re.compile(r"^<([^>]+)>$")


@dataclass(frozen=True)
class Term:
    """模式里的一个项：变量 / 实体常量 / 字面量。"""

    kind: str          # 'var' | 'const' | 'lit'
    value: str

    def __str__(self) -> str:  # pragma: no cover
        return f"?{self.value}" if self.kind == "var" else self.value


@dataclass(frozen=True)
class PatternTriple:
    s: Term
    r: str
    o: Term

    def as_tuple(self) -> Tuple[str, str, str]:
        return (self.s.value, self.r, self.o.value)

    def to_dict(self) -> Dict[str, str]:
        return {"s": f"{self.s.kind}:{self.s.value}", "r": self.r, "o": f"{self.o.kind}:{self.o.value}"}

    @classmethod
    def from_dict(cls, d: Dict[str, str]) -> "PatternTriple":
        sk, sv = d["s"].split(":", 1)
        ok, ov = d["o"].split(":", 1)
        return cls(Term(sk, sv), d["r"], Term(ok, ov))


@dataclass
class SparqlPattern:
    """解析后的 SPARQL 图模式。"""

    select_vars: List[str] = field(default_factory=list)
    triples: List[PatternTriple] = field(default_factory=list)
    raw: str = ""
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "select_vars": list(self.select_vars),
            "triples": [t.to_dict() for t in self.triples],
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "SparqlPattern":
        return cls(
            select_vars=list(d.get("select_vars") or []),
            triples=[PatternTriple.from_dict(x) for x in d.get("triples") or []],
            warnings=list(d.get("warnings") or []),
        )

    # ---------------------------------------------------------------- 查询
    @property
    def constants(self) -> List[str]:
        seen: List[str] = []
        for t in self.triples:
            for term in (t.s, t.o):
                if term.kind == "const" and term.value not in seen:
                    seen.append(term.value)
        return seen

    def answer_var(self) -> Optional[str]:
        return self.select_vars[0] if self.select_vars else None

    def path_to(self, start: str, goal: str) -> Optional[List[Tuple[PatternTriple, str]]]:
        """在模式图上求 ``start``（常量 mid）到 ``goal``（变量名）的最短路径。

        返回 ``[(pattern_triple, direction), ...]``，``direction`` 为 ``'fwd'`` 表示
        沿三元组 s -> o 前进，``'bwd'`` 表示 o -> s。
        """
        # 邻接：节点 -> [(next, triple, direction)]
        adj: Dict[str, List[Tuple[str, PatternTriple, str]]] = {}
        for t in self.triples:
            if t.s.kind == "lit" or t.o.kind == "lit":
                continue
            adj.setdefault(t.s.value, []).append((t.o.value, t, "fwd"))
            adj.setdefault(t.o.value, []).append((t.s.value, t, "bwd"))
        if start == goal:
            return []
        # BFS
        prev: Dict[str, Tuple[str, PatternTriple, str]] = {}
        frontier = [start]
        visited = {start}
        while frontier:
            nxt: List[str] = []
            for u in frontier:
                for v, tr, d in adj.get(u, []):
                    if v in visited:
                        continue
                    visited.add(v)
                    prev[v] = (u, tr, d)
                    if v == goal:
                        chain: List[Tuple[PatternTriple, str]] = []
                        cur = goal
                        while cur != start:
                            p, tr, dd = prev[cur]
                            chain.append((tr, dd))
                            cur = p
                        chain.reverse()
                        return chain
                    nxt.append(v)
            frontier = nxt
        return None

    def chain_from(self, const_mid: str) -> Optional[List[Tuple[str, str]]]:
        """``const_mid`` 到 SELECT 变量的关系步序列 ``[(relation, direction), ...]``。"""
        goal = self.answer_var()
        if goal is None:
            return None
        path = self.path_to(const_mid, goal)
        if path is None:
            return None
        return [(tr.r, d) for tr, d in path]


# --------------------------------------------------------------------------- 解析
def _strip_filters(text: str) -> str:
    """删除 ``FILTER (...)``（含嵌套括号与花括号块）。"""
    out = []
    i = 0
    n = len(text)
    while i < n:
        m = re.compile(r"FILTER\s*\(", re.IGNORECASE).match(text, i)
        if not m:
            out.append(text[i])
            i += 1
            continue
        j = m.end() - 1  # 指向 '('
        depth = 0
        while j < n:
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            j += 1
        i = j
    return "".join(out)


def _strip_braced_blocks(text: str) -> str:
    """删除所有 ``{ ... }`` 块（OPTIONAL / UNION / 子查询）。"""
    out = []
    depth = 0
    for ch in text:
        if ch == "{":
            depth += 1
            continue
        if ch == "}":
            depth = max(0, depth - 1)
            continue
        if depth == 0:
            out.append(ch)
    return "".join(out)


def _where_body(text: str) -> str:
    """取 ``WHERE { ... }`` 的最外层花括号内容。"""
    m = re.search(r"\bWHERE\s*\{", text, re.IGNORECASE)
    start = m.end() if m else None
    if start is None:
        m = re.search(r"\{", text)
        if not m:
            return text
        start = m.end()
    depth = 1
    i = start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i]
        i += 1
    return text[start:]


def _parse_term(tok: str) -> Term:
    tok = tok.strip()
    if VAR_RE.match(tok):
        return Term("var", VAR_RE.match(tok).group(1))
    m = MID_RE.match(tok)
    if m:
        return Term("const", m.group(1))
    m = URI_RE.match(tok)
    if m:
        uri = m.group(1)
        if uri.startswith(FULL_NS):
            return Term("const", uri[len(FULL_NS):])
        return Term("const", uri)
    if tok.startswith("ns:"):
        return Term("const", tok[3:])
    if tok.startswith('"') or tok.startswith("'"):
        return Term("lit", tok)
    return Term("const", tok)


def parse_sparql(text: str) -> SparqlPattern:
    """把 SPARQL 文本解析成 :class:`SparqlPattern`。"""
    pat = SparqlPattern(raw=text or "")
    if not text:
        pat.warnings.append("empty sparql")
        return pat

    # SELECT 变量
    m = re.search(r"SELECT\s+(?:DISTINCT\s+|REDUCED\s+)?(.*?)\bWHERE\b", text, re.IGNORECASE | re.DOTALL)
    if m:
        for v in re.findall(r"[?$]([A-Za-z_][A-Za-z0-9_]*)", m.group(1)):
            if v not in pat.select_vars:
                pat.select_vars.append(v)
    else:
        pat.warnings.append("no SELECT ... WHERE")

    body = _where_body(text)
    body = _strip_filters(body)
    body = _strip_braced_blocks(body)
    body = re.sub(r"\bPREFIX\b.*", "", body, flags=re.IGNORECASE)
    body = re.sub(r"#[^\n]*", "", body)

    # 按 '.' 切语句；同时支持 ';' 续写主语的写法
    statements = re.split(r"\.\s*(?=(?:[?$]|ns:|<))|\n\s*\.\s*", body)
    for stmt in statements:
        stmt = stmt.strip().strip(".").strip()
        if not stmt:
            continue
        parts = stmt.split()
        # 去掉 BIND / OPTIONAL 之类的关键字残片
        parts = [p for p in parts if p.upper() not in {"OPTIONAL", "UNION", "BIND", "MINUS"}]
        if len(parts) < 3:
            continue
        if ";" in stmt:
            # ?s r1 ?o1 ; r2 ?o2
            chunks = [c.strip() for c in stmt.split(";") if c.strip()]
            subj = _parse_term(chunks[0].split()[0]) if chunks else None
            for k, ch in enumerate(chunks):
                toks = ch.split()
                if k == 0 and subj is not None:
                    toks = toks[1:]
                if len(toks) < 2:
                    continue
                pat.triples.append(PatternTriple(subj, _parse_term(toks[0]).value, _parse_term(toks[1])))
            continue
        if len(parts) == 3:
            s, r, o = (_parse_term(p) for p in parts)
            pat.triples.append(PatternTriple(s, r.value if r.kind != "lit" else r.value, o))
        elif len(parts) > 3:
            pat.warnings.append(f"skip statement: {stmt[:80]}")
    return pat


# --------------------------------------------------------------------------- 便捷函数
def chain_from_inferential_chain(relations: Sequence[str]) -> List[Tuple[str, str]]:
    """WebQSP 的 ``InferentialChain`` 直接就是正向关系序列。"""
    return [(r, "fwd") for r in relations]


def candidate_chains(pat: SparqlPattern) -> Dict[str, List[Tuple[str, str]]]:
    """对模式里每个常量求到答案变量的关系链（供 matcher 逐个尝试）。"""
    out: Dict[str, List[Tuple[str, str]]] = {}
    for c in pat.constants:
        chain = pat.chain_from(c)
        if chain:
            out[c] = chain
    return out
