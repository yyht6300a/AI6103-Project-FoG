# -*- coding: utf-8 -*-
"""把 mid 空间的关系链对齐到 KG 的名字空间，得到训练用的黄金三元组。

为什么需要"对齐"
----------------
数据集标注是 mid 空间（``m.06w2sn5``），KG 索引是名字空间（``Justin Bieber``）。
RoG 式预处理在实体无英文名时用 mid 兜底，所以 CVT 节点天然就是 mid，可以直接对；
真正的难点是**有名字的中间实体**：标注里只有 mid，没有名字，无法直接投影。

本模块的做法是"链引导 + 答案校验"：
在 KG 上按标注给出的关系序列逐跳游走，只接受**终点落在答案集合**的路径。
这样即便不知道中间实体的名字，也能唯一地锁定正确路径；关系序列来自标注，
答案来自标注，两边都是监督信息，不存在泄漏。

匹配强度由弱到强记录在 ``method`` 上：

==================  =============================================================
method              含义
==================  =============================================================
``exact``           严格按标注方向逐跳走通，且终点命中答案
``relaxed``         某一跳允许反向（KG 与 SPARQL 书写方向不一致时会发生）
``relaxed_order``   关系集正确但顺序/方向被放宽（黄金边召回优先）
``shortest_any``    放弃关系约束，只求"源 → 答案"的最短路（受 ``allow_shortest_fallback`` 控制）
``none``            完全没对上（该问题的正样本缺失，需要统计并上报）
==================  =============================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from ..kg.tsv_index import TripleIndex
from ..schema import GoldPath, GoldStep, Triple

Step = Tuple[str, str]  # (relation, 'fwd'|'bwd')


@dataclass
class MatchResult:
    triples: List[Triple] = field(default_factory=list)
    nodes: List[str] = field(default_factory=list)
    method: str = "none"
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.triples)


def _norm(name: str) -> str:
    return name.strip().casefold()


def _resolve_start(kg: TripleIndex, names: Sequence[str]) -> List[Tuple[str, int]]:
    out: List[Tuple[str, int]] = []
    seen: Set[int] = set()
    for name in names:
        eid = kg.resolve_entity(name)
        if eid is None:
            continue
        if eid in seen:
            continue
        seen.add(eid)
        out.append((kg.entities[eid], eid))
    return out


def _step_candidates(
    kg: TripleIndex,
    eid: int,
    relation: str,
    direction: str,
    max_branch: int,
) -> List[Tuple[int, Triple, str]]:
    """给定当前实体与一步关系，返回候选 (下一实体 id, 三元组, 实际方向)。

    标注方向优先，但**另一个方向也保留**：SPARQL 里三元组的书写方向与 KG 存储方向
    偶尔不一致，只走标注方向会漏掉黄金边。方向是否"顺从标注"由调用方记为 exact/relaxed。
    """
    rid = kg.relation_id(relation)
    if rid is None:
        return []

    fwd: List[Tuple[int, Triple, str]] = []
    for e in kg.out_edges_with_rel(eid, rid):
        nxt = int(kg.tail[e])
        fwd.append((nxt, Triple(kg.entities[eid], relation, kg.entities[nxt]), "fwd"))
        if len(fwd) >= max_branch:
            break

    bwd: List[Tuple[int, Triple, str]] = []
    for e in kg.in_edges_with_rel(eid, rid):
        nxt = int(kg.head[e])
        bwd.append((nxt, Triple(kg.entities[nxt], relation, kg.entities[eid]), "bwd"))
        if len(bwd) >= max_branch:
            break

    first, second = (fwd, bwd) if direction == "fwd" else (bwd, fwd)
    out: List[Tuple[int, Triple, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for cand in first + second:
        key = cand[1].key()
        if key in seen:
            continue
        seen.add(key)
        out.append(cand)
        if len(out) >= max_branch:
            break
    return out


def walk_chain(
    kg: TripleIndex,
    start_names: Sequence[str],
    chain: Sequence[Step],
    answer_names: Iterable[str],
    answer_mids: Iterable[str] = (),
    max_branch: int = 40,
    max_paths: int = 64,
    max_visited: int = 20000,
) -> MatchResult:
    """沿 ``chain`` 在 KG 上从 ``start_names`` 游走，保留终点属于答案集合的路径。"""
    targets = {_norm(a) for a in answer_names if a}
    targets |= {_norm(m) for m in answer_mids if m}
    starts = _resolve_start(kg, start_names)
    if not starts:
        return MatchResult(method="none", warnings=["源实体不在 KG 中"])
    if not chain:
        return MatchResult(method="none", warnings=["空的黄金关系链"])

    results: List[Tuple[str, List[Triple], List[str]]] = []  # (method, triples, nodes)
    visited_count = 0

    for start_name, start_eid in starts:
        # DFS: (当前 id, 已用三元组, 已过节点, 是否全程严格方向)
        stack: List[Tuple[int, List[Triple], List[str], bool]] = [
            (start_eid, [], [start_name], True)
        ]
        while stack and len(results) < max_paths:
            eid, triples, nodes, strict = stack.pop()
            depth = len(triples)
            if depth == len(chain):
                if _norm(kg.entities[eid]) in targets:
                    results.append(("exact" if strict else "relaxed", triples, nodes))
                continue
            if visited_count > max_visited:
                break
            relation, direction = chain[depth]
            cands = _step_candidates(kg, eid, relation, direction, max_branch)
            visited_count += len(cands)
            for nxt, tr, actual_dir in reversed(cands):
                if nxt == eid:
                    continue
                stack.append((nxt, triples + [tr], nodes + [kg.entities[nxt]],
                              strict and actual_dir == direction))

    if not results:
        return MatchResult(method="none", warnings=["关系链在 KG 上未走通到任何答案"])

    # 优先 strict，其次三元组出现次数少的（更"专一"的路径）
    results.sort(key=lambda r: (r[0] != "exact", len(r[1])))
    method, triples, nodes = results[0]
    return MatchResult(triples=triples, nodes=nodes, method=method)


def bfs_fallback(
    kg: TripleIndex,
    start_names: Sequence[str],
    answer_names: Iterable[str],
    answer_mids: Iterable[str] = (),
    max_depth: int = 3,
    relation_allow: Optional[Set[str]] = None,
    max_expand: int = 20000,
    hub_degree_max: int = 20000,
) -> MatchResult:
    """放弃顺序约束的兜底：在 ``max_depth`` 内找"源 → 答案"的路径。

    ``relation_allow`` 非空时只允许走这些关系（用 (实体,关系) 索引，避免在枢纽实体上
    逐边扫描）；为空则允许任意关系，但跳过度数超过 ``hub_degree_max`` 的枢纽节点。
    """
    targets = {_norm(a) for a in answer_names if a}
    targets |= {_norm(m) for m in answer_mids if m}
    starts = _resolve_start(kg, start_names)
    if not starts or not targets:
        return MatchResult(method="none", warnings=["缺少起点或答案"])
    rel_ids: List[int] | None = None
    if relation_allow:
        rel_ids = [r for r in (kg.relation_id(x) for x in relation_allow) if r is not None]
        if not rel_ids:
            return MatchResult(method="none", warnings=["允许的关系都不在 KG 中"])

    from collections import deque

    for start_name, start_eid in starts:
        prev: Dict[int, Tuple[int, Triple]] = {}
        q = deque([(start_eid, 0)])
        seen = {start_eid}
        expanded = 0
        while q:
            eid, d = q.popleft()
            if d >= max_depth:
                continue
            expanded += 1
            if expanded > max_expand:
                break
            edges: List[Tuple[int, Triple]] = []
            if rel_ids is not None:
                for rid in rel_ids:
                    rel = kg.relations[rid]
                    for e in kg.out_edges_with_rel(eid, rid):
                        nxt = int(kg.tail[e])
                        edges.append((nxt, Triple(kg.entities[eid], rel, kg.entities[nxt])))
                    for e in kg.in_edges_with_rel(eid, rid):
                        nxt = int(kg.head[e])
                        edges.append((nxt, Triple(kg.entities[nxt], rel, kg.entities[eid])))
            else:
                if kg.degree(eid) > hub_degree_max:
                    continue
                for e in kg.out_edges(eid):
                    rel = kg.relations[int(kg.rel[e])]
                    nxt = int(kg.tail[e])
                    edges.append((nxt, Triple(kg.entities[eid], rel, kg.entities[nxt])))
                for e in kg.in_edges(eid):
                    rel = kg.relations[int(kg.rel[e])]
                    nxt = int(kg.head[e])
                    edges.append((nxt, Triple(kg.entities[nxt], rel, kg.entities[eid])))
            for nxt, tr in edges:
                if nxt in seen:
                    continue
                seen.add(nxt)
                prev[nxt] = (eid, tr)
                if _norm(kg.entities[nxt]) in targets:
                    # 回溯
                    triples: List[Triple] = []
                    nodes: List[str] = [kg.entities[nxt]]
                    cur = nxt
                    while cur != start_eid:
                        p, t = prev[cur]
                        triples.append(t)
                        nodes.append(kg.entities[p])
                        cur = p
                    triples.reverse()
                    nodes.reverse()
                    method = "relaxed_order" if relation_allow else "shortest_any"
                    return MatchResult(triples=triples, nodes=nodes, method=method)
                q.append((nxt, d + 1))
    return MatchResult(method="none", warnings=["兜底搜索未找到通路"])


def align_gold(
    kg: TripleIndex,
    start_names: Sequence[str],
    answer_names: Sequence[str],
    answer_mids: Sequence[str],
    primary_chain: Sequence[Step],
    alt_chains: Sequence[Sequence[Step]] = (),
    steps_meta: Optional[Sequence[GoldStep]] = None,
    allow_shortest_fallback: bool = False,
    max_depth: int = 4,
) -> GoldPath:
    """统一的黄金路径对齐入口。

    依次尝试：主链 → 备选链（其它 SPARQL 常量给出的链） → 关系集受限兜底 → 最短路兜底。
    """
    gold = GoldPath(steps=list(steps_meta or []))
    tried: List[str] = []

    chains: List[Sequence[Step]] = []
    if primary_chain:
        chains.append(primary_chain)
    for c in alt_chains:
        if c and list(c) != list(primary_chain):
            chains.append(c)

    for i, chain in enumerate(chains):
        res = walk_chain(kg, start_names, chain, answer_names, answer_mids)
        if res.ok:
            gold.triples = res.triples
            gold.matched = True
            gold.match_method = res.method if i == 0 else f"alt{i}:{res.method}"
            gold.n_path_hops = len(res.triples)
            gold.errors.extend(res.warnings)
            return gold
        tried.extend(res.warnings)

    # 关系集受限兜底
    rel_set = {r for r, _d in chains[0]} if chains else None
    if rel_set:
        res = bfs_fallback(kg, start_names, answer_names, answer_mids,
                           max_depth=max_depth, relation_allow=rel_set)
        if res.ok:
            gold.triples = res.triples
            gold.matched = True
            gold.match_method = res.method
            gold.n_path_hops = len(res.triples)
            gold.errors.extend(tried)
            return gold

    if allow_shortest_fallback:
        res = bfs_fallback(kg, start_names, answer_names, answer_mids,
                           max_depth=min(max_depth, 3), relation_allow=None)
        if res.ok:
            gold.triples = res.triples
            gold.matched = True
            gold.match_method = res.method
            gold.n_path_hops = len(res.triples)
            gold.errors.extend(tried)
            return gold

    gold.matched = False
    gold.match_method = "none"
    gold.errors.extend(tried[:4])
    return gold


def constraint_triples(
    kg: TripleIndex,
    path_nodes: Sequence[str],
    constraints: Sequence[Dict[str, str]],
    mid2name: Optional[Dict[str, str]] = None,
) -> List[Triple]:
    """把 WebQSP 的 ``Constraints`` 转成名字空间三元组（角色：constraint）。

    ``Constraints[i]`` 的语义是"链上某个节点以 ``NodePredicate`` 指向某个实体"，
    但 ``SourceNodeIndex`` 的基准有点绕：在 800 条 WebQSP train 上实测
    （232 条约束、190 题），**用 ``idx+1`` 命中 70 条，原样用 ``idx`` 一条都不中**，
    再叠加"答案节点"和"任意链上节点"兜底可覆盖到 142/232 ≈ 61%。

    因此这里按 ``[idx+1, idx, 答案节点, 其余节点]`` 的顺序尝试，**取第一个在 KG 中真实存在**的。
    这个名字空间校验保证了不会造出假三元组。
    """
    out: List[Triple] = []
    if not constraints or not path_nodes:
        return out
    n = len(path_nodes)
    for c in constraints:
        pred = c.get("NodePredicate") or c.get("predicate")
        name = c.get("EntityName") or (mid2name or {}).get(c.get("Argument", ""))
        if not pred or not name:
            continue
        idx = int(c.get("SourceNodeIndex", -1) or -1)
        order: List[int] = []
        for cand in (idx + 1, idx, n - 1):
            if 0 <= cand < n and cand not in order:
                order.append(cand)
        for cand in range(n - 1, -1, -1):
            if cand not in order:
                order.append(cand)
        for i in order:
            tr = Triple(path_nodes[i], pred, name)
            if kg.has_triple(tr.h, tr.r, tr.t):
                out.append(tr)
                break
    return out
