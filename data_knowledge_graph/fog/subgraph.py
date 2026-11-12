# -*- coding: utf-8 -*-
"""前瞻子图展开（FSE）的**数据侧**部分：从源实体出发的 L 跳分层 BFS。

成员 C 负责 FSE 的打分与剪枝（用评分器按 τ_e 过滤 frontier 边）；本模块只负责
"候选供给"这一半：把源实体周围 L 跳的可达邻域完整地、带距离地、朝内定向地取出来。
两者是同一份图约定，C 直接在这个 :class:`~fog.schema.Subgraph` 上跑评分器即可。

关键约定
--------
* **距离**：``dist`` 是到最近源实体的跳数（0 表示源实体本身），由分层 BFS 得到。
  C 的"深度调度 τ_e(l)"直接用这个字段。
* **朝内定向**：FoG §3.1 要求把展开子图按距离重定向为"远 → 近"，供 FFP 传播。
  这里直接给出 ``src``（远）与 ``dst``（近），C 不需要再算一遍。
* **关系度数过滤**：决策点 7 —— 全局度数超过 ``rel_degree_max`` 的通用关系不进入展开。
* **截断**：候选爆炸时按 (到源距离, 关系度数) 确定性排序后截断，并置 ``truncated`` 标志，
  保证同一个问题每次跑出来完全一样。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .kg.tsv_index import TripleIndex
from .schema import Subgraph, SubgraphEdge, SubgraphNode, Triple


@dataclass
class SubgraphConfig:
    """展开参数。

    ⚠️ 关于决策点 7 的"度数大于 1000 过滤"
    ---------------------------------------
    《复现计划》给的建议是"度数大于 1000 过滤"。实测本子图（8,258,891 条三元组）后可以
    确认：这个 1000 **只能用在实体度数上，不能用在关系频次上**——

    * 按**关系频次**过滤 1000：7042 个关系里只剩 6345 个，三元组只剩 552,951 条（6.70%），
      等于把 93% 的图谱扔掉，3 跳后的黄金边召回率只剩 **5.8%**；
    * 按**实体度数**过滤 1000：只影响 614 个枢纽实体（United States of America、
      Male、English Language、Date of birth……），但会把"黄金路径穿过枢纽节点"的情形
      一起剪掉，WebQSP test 上黄金边召回率从 97.7% 掉到 70.1%。

    所以本实现把它拆成三个旋钮，并在 WebQSP test（100 题）上扫网格定标：

    * ``node_degree_max=5000``（枢纽不展开；5000 比 1000 好、也比"不限制"好——不限制会
      让 20k 节点预算被枢纽邻居挤占，召回反而降到 92%）；
    * ``rel_degree_max=200000``（只砍极端通用关系；10 万以下会损失 >10% 黄金边）；
    * ``rel_blocklist``（元数据 / 类型断言类关系的硬黑名单）。

    网格结果见 ``reports/stats/expansion_webqsp_test.json`` 与报告第 4 节。
    """

    hops: int = 3                       # L
    node_degree_max: int = 20000        # 决策点 7（实体侧）：枢纽节点不展开
    rel_degree_max: int = 200_000       # 关系频次侧：只砍极端通用关系
    rel_blocklist: tuple = (            # 元数据/类型断言类关系，永远不进展开
        "common.topic.notable_types",
        "freebase.valuenotation.",
        "common.webpage.",
        "type.object.",
    )
    max_nodes: int = 20000              # 节点上限（候选爆炸保护）
    max_edges: int = 40000              # 边上限（网格校准：40k + nd=20k 时召回 95.1%）
    max_gather: int = 2_000_000         # 单层邻居收集的硬上限（防枢纽节点把内存打爆）
    include_gold: bool = True           # 是否标注黄金边
    keep_lateral: bool = True           # 保留同层（距离相等）的边

    def to_dict(self) -> Dict:
        return {
            "hops": self.hops,
            "node_degree_max": self.node_degree_max,
            "rel_degree_max": self.rel_degree_max,
            "rel_blocklist": list(self.rel_blocklist),
            "max_nodes": self.max_nodes,
            "max_edges": self.max_edges,
            "max_gather": self.max_gather,
            "include_gold": self.include_gold,
            "keep_lateral": self.keep_lateral,
        }

    def relation_allowed(self, relation: str, rel_degree: int) -> bool:
        if rel_degree > self.rel_degree_max:
            return False
        for pat in self.rel_blocklist:
            if relation == pat or (pat.endswith(".") and relation.startswith(pat)):
                return False
        return True

    @classmethod
    def from_dict(cls, d: Dict) -> "SubgraphConfig":
        """从 ``to_dict()`` / JSON 配置还原（忽略未知键，黑名单自动转 tuple）。"""
        fields = set(cls.__dataclass_fields__)
        kw = {k: v for k, v in d.items() if k in fields}
        if "rel_blocklist" in kw:
            kw["rel_blocklist"] = tuple(kw["rel_blocklist"])
        return cls(**kw)


def relation_mask(kg: TripleIndex, cfg: SubgraphConfig) -> np.ndarray:
    """关系级过滤掩码（按 关系频次 + 黑名单），按 (kg, cfg) 缓存。"""
    key = (cfg.rel_degree_max, tuple(cfg.rel_blocklist))
    cached = getattr(kg, "_subgraph_rel_mask", None)
    if cached is not None and cached[0] == key:
        return cached[1]
    rd = kg.rel_degree
    mask = np.array([cfg.relation_allowed(kg.relations[i], int(rd[i]))
                     for i in range(kg.n_relations)], dtype=bool)
    kg._subgraph_rel_mask = (key, mask)
    return mask


def _unique_ints(arr: np.ndarray) -> np.ndarray:
    """int32 数组去重（sort + 掩码，比 np.unique 的哈希实现快一个量级）。"""
    if arr.size <= 1:
        return arr
    arr = np.sort(arr, kind="quicksort")
    mask = np.empty(arr.size, dtype=bool)
    mask[0] = True
    np.not_equal(arr[1:], arr[:-1], out=mask[1:])
    return arr[mask]


def _dist_buffer(kg: TripleIndex) -> np.ndarray:
    """复用的距离数组（-1 表示不可达），避免每次分配 2.5M 元素。"""
    buf = getattr(kg, "_subgraph_dist_buf", None)
    if buf is None or buf.shape[0] != kg.n_entities:
        buf = np.full(kg.n_entities, -1, dtype=np.int32)
        kg._subgraph_dist_buf = buf
    return buf


def bfs_distances(
    kg: TripleIndex,
    seeds: Sequence[int],
    cfg: SubgraphConfig,
) -> Tuple[Dict[int, int], Dict[str, int]]:
    """分层 BFS，返回 ``{实体 id: 距离}``。

    ``node_degree_max`` 只限制**是否继续展开**（枢纽节点仍作为叶子进入子图）。
    逐层用 numpy 向量化：先按出/入 CSR 抓出 frontier 的全部边，再对"恰好一端已知"的边取另一端。
    """
    rel_mask = relation_mask(kg, cfg)
    ent_deg = kg.entity_degree
    dist_arr = _dist_buffer(kg)
    dist_arr.fill(-1)

    seeds_arr = _unique_ints(np.asarray(list(seeds), dtype=np.int32))
    dist_arr[seeds_arr] = 0
    frontier = seeds_arr
    stats = {"rel_filtered_edges": 0, "hub_skipped": 0, "node_cap_hit": 0}
    n_reached = int(seeds_arr.size)

    for layer in range(1, cfg.hops + 1):
        # 枢纽节点不展开，但**源实体本身永远要展开**：
        # 否则源实体度数 > node_degree_max 时会得到"1 个节点、0 条边"的空子图。
        keep_src = (ent_deg[frontier] <= cfg.node_degree_max) | np.isin(frontier, seeds_arr)
        cur = frontier[keep_src]
        stats["hub_skipped"] += int(frontier.size - cur.size)
        if cur.size == 0:
            break
        edges = _unique_ints(np.concatenate((
            _gather(kg.out_eids, kg.out_indptr, cur, cap=cfg.max_gather),
            _gather(kg.in_eids, kg.in_indptr, cur, cap=cfg.max_gather))))
        if edges.size == 0:
            break
        ok = rel_mask[kg.rel[edges]]
        stats["rel_filtered_edges"] += int((~ok).sum())
        edges = edges[ok]
        if edges.size == 0:
            break

        h = kg.head[edges]
        t = kg.tail[edges]
        h_known = dist_arr[h] >= 0
        t_known = dist_arr[t] >= 0
        sel = h_known ^ t_known          # 只保留"一端已知、一端未知"的边
        if not sel.any():
            break
        others = np.where(h_known[sel], t[sel], h[sel])
        others = _unique_ints(others)
        others = others[dist_arr[others] < 0]
        if others.size == 0:
            break
        room = cfg.max_nodes - n_reached
        if others.size > room:
            stats["node_cap_hit"] += int(others.size - max(room, 0))
            others = others[: max(room, 0)]
            if others.size == 0:
                break
        dist_arr[others] = layer
        n_reached += int(others.size)
        frontier = others

    ids = np.flatnonzero(dist_arr >= 0)
    dist = {int(i): int(dist_arr[i]) for i in ids}
    return dist, stats


def induced_edges(
    kg: TripleIndex,
    dist: Dict[int, int],
    cfg: SubgraphConfig,
) -> Tuple[np.ndarray, bool, int]:
    """取节点集合 ``dist`` 的诱导边集（关系过滤 + 确定性截断）。全向量化。"""
    ids = np.fromiter(dist.keys(), dtype=np.int32, count=len(dist))
    vals = np.fromiter(dist.values(), dtype=np.int32, count=len(dist))
    dist_arr = _dist_buffer(kg)
    dist_arr.fill(-1)
    dist_arr[ids] = vals

    candidate = _unique_ints(np.concatenate((
        _gather(kg.out_eids, kg.out_indptr, ids, cap=cfg.max_gather),
        _gather(kg.in_eids, kg.in_indptr, ids, cap=cfg.max_gather))))
    if candidate.size == 0:
        return np.zeros(0, dtype=np.int32), False, 0

    rel_mask = relation_mask(kg, cfg)
    ok = rel_mask[kg.rel[candidate]]
    n_rel_filtered = int((~ok).sum())
    candidate = candidate[ok]
    if candidate.size == 0:
        return np.zeros(0, dtype=np.int32), False, n_rel_filtered

    # 只保留两端都在 dist 内的边
    hd = dist_arr[kg.head[candidate]]
    td = dist_arr[kg.tail[candidate]]
    inside = (hd >= 0) & (td >= 0)
    candidate, hd, td = candidate[inside], hd[inside], td[inside]

    truncated = False
    if candidate.size > cfg.max_edges:
        # 确定性截断：优先保留离源更近的边，其次关系更"专"（频次更低）的边
        maxd = np.maximum(hd, td)
        order = np.lexsort((kg.rel_degree[kg.rel[candidate]], maxd))
        candidate, hd, td = (candidate[order][:cfg.max_edges],
                             hd[order][:cfg.max_edges], td[order][:cfg.max_edges])
        truncated = True
    return candidate.astype(np.int32), truncated, n_rel_filtered


def _gather(eids: np.ndarray, indptr: np.ndarray, nodes: np.ndarray,
            cap: int | None = None) -> np.ndarray:
    """把若干节点的邻接边 id 拼接起来（numpy 版 ragged gather）。

    等价于 ``np.concatenate([eids[indptr[n]:indptr[n+1]] for n in nodes])``，
    但用 repeat + arange 一次算完，20k 个节点也只要几毫秒。

    ``cap`` 给定时按节点顺序**确定性地**截断到至多 ``cap`` 条边——防止 frontier 里
    混入枢纽节点时一次抓起上千万条边（曾把单题耗时从 0.25 s 拖到 >10 s）。
    """
    if nodes.size == 0:
        return np.zeros(0, dtype=np.int32)
    starts = indptr[nodes].astype(np.int64)
    lens = (indptr[nodes + 1] - indptr[nodes]).astype(np.int64)
    if cap is not None and int(lens.sum()) > cap:
        cum = np.cumsum(lens)
        keep = int(np.searchsorted(cum, cap, side="left")) + 1
        keep = max(1, min(keep, nodes.size))
        nodes = nodes[:keep]
        starts = starts[:keep]
        lens = lens[:keep]
        cum = np.cumsum(lens)
        if cum[-1] > cap:
            lens[-1] = max(0, cap - (int(cum[-1]) - int(lens[-1])))
    total = int(lens.sum())
    if total == 0:
        return np.zeros(0, dtype=np.int32)
    block_off = np.cumsum(lens) - lens
    pos = np.repeat(starts - block_off, lens) + np.arange(total, dtype=np.int64)
    return eids[pos]


def build_subgraph(
    kg: TripleIndex,
    qid: str,
    seed_names: Sequence[str],
    gold_triples: Sequence[Triple] = (),
    answer_names: Sequence[str] = (),
    config: Optional[SubgraphConfig] = None,
) -> Subgraph:
    """构建一条问题的展开子图。"""
    cfg = config or SubgraphConfig()
    seeds: List[int] = []
    missing: List[str] = []
    for name in seed_names:
        eid = kg.resolve_entity(name)
        if eid is None:
            missing.append(name)
        elif eid not in seeds:
            seeds.append(eid)

    gold_keys = {t.key() for t in gold_triples}
    if not seeds:
        return Subgraph(qid=qid, nodes=[], edges=[], stats={
            "n_nodes": 0, "n_edges": 0, "seeds_in_kg": 0,
            "missing_seeds": missing, "empty_reason": "no_seed_in_kg",
            "config": cfg.to_dict(),
        })

    dist, bfs_stats = bfs_distances(kg, seeds, cfg)
    edge_ids, truncated, n_rel_filtered = induced_edges(kg, dist, cfg)

    # 节点表（保持确定性顺序：按 (dist, 名字) 排序）
    node_ids = sorted(dist.keys(), key=lambda i: (dist[i], kg.entities[i]))
    local = {gid: i for i, gid in enumerate(node_ids)}
    answer_set = {a.casefold() for a in answer_names if a}
    nodes = [
        SubgraphNode(gid=gid, name=kg.entities[gid], dist=dist[gid],
                     is_source=dist[gid] == 0,
                     is_answer=kg.entities[gid].casefold() in answer_set, idx=i)
        for i, gid in enumerate(node_ids)
    ]

    edges: List[SubgraphEdge] = []
    n_gold_edges = 0
    present_keys: set[tuple[str, str, str]] = set()
    for e in edge_ids:
        e = int(e)
        h, r, t = int(kg.head[e]), int(kg.rel[e]), int(kg.tail[e])
        if h == t:
            # 名字空间合并同名实体后会出现自环（同一实体名对应两个不同 mid），无信息量
            continue
        dh, dt = dist[h], dist[t]
        if not cfg.keep_lateral and dh == dt:
            continue
        if dt > dh:            # 尾端更远 -> 朝内：t -> h
            src, dst = local[t], local[h]
        else:                  # 头端更远或同层 -> 朝内：h -> t
            src, dst = local[h], local[t]
        key = (kg.entities[h], kg.relations[r], kg.entities[t])
        is_gold = key in gold_keys
        n_gold_edges += int(is_gold)
        if cfg.include_gold:
            present_keys.add(key)
        edges.append(SubgraphEdge(
            s=local[h], r=kg.relations[r], t=local[t],
            ds=int(dh), dt=int(dt), src=src, dst=dst, gold=int(is_gold)))

    n_lateral = sum(1 for e in edges if e.ds == e.dt)
    layer_counts: Dict[int, int] = {}
    for n in nodes:
        layer_counts[n.dist] = layer_counts.get(n.dist, 0) + 1

    gold_present = sum(1 for t in gold_triples if t.key() in present_keys) if cfg.include_gold else 0

    sub = Subgraph(qid=qid, nodes=nodes, edges=edges, stats={
        "n_nodes": len(nodes),
        "n_edges": len(edges),
        "n_edges_total_before_cap": int(edge_ids.size),
        "truncated": truncated,
        "seeds_in_kg": len(seeds),
        "missing_seeds": missing,
        "layer_node_counts": {str(k): v for k, v in sorted(layer_counts.items())},
        "n_lateral_edges": n_lateral,
        "n_gold_edges": n_gold_edges,
        "n_gold_triples": len(gold_triples),
        "gold_edges_present": gold_present,
        "gold_recall_in_subgraph": round(gold_present / len(gold_triples), 4) if gold_triples else None,
        "rel_filtered_edges_bfs": bfs_stats["rel_filtered_edges"],
        "rel_filtered_edges_induced": n_rel_filtered,
        "hub_skipped": bfs_stats["hub_skipped"],
        "node_cap_hit": bfs_stats["node_cap_hit"],
        "config": cfg.to_dict(),
    })
    return sub
