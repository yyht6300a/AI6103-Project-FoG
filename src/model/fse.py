# -*- coding: utf-8 -*-
"""Foresight subgraph expansion: batch construction + depth-aware pruning (member C).

Member A ships the *candidate pool*: every L-hop reachable edge with distance
annotations and inward ``src`` / ``dst`` orientation. This module implements the
"scoring + pruning" half of FSE. It turns the candidate pool into the paper's
expanded subgraph ``G_xpd`` by scoring each edge with member B's frozen scorer
and keeping edges whose probability exceeds the expansion threshold ``tau_e``.

Threshold scale
---------------
``tau_e`` is a probability. Use ``ForesightTripleScorer.prob`` (never the raw
logit from ``score``) when comparing against 0.1.

Depth schedule
--------------
The "layer" of an edge is the distance of its farther endpoint,
``l = max(ds, dt)``. With ``tau_lo`` / ``tau_hi`` set, the threshold is linear
in ``l``; with both ``None``, a single fixed ``tau_e`` is used for every layer.

Data flow
---------
    A.Subgraph + EmbeddingStore + question embedding
        -> subgraph_to_batch() -> TripleBatch
        -> scorer.encode(batch) -> z_t            (cached, fixed within a round)
        -> scorer.prob(z_t, None, None, ...) -> prob_t
        -> fse_expand(subgraph, prob_t, ...) -> keep mask
        -> G_xpd = kept edges (+ their z_t rows)
"""

from __future__ import annotations

import math

import torch

from .scorer import TripleBatch


def edge_layer(ds: int, dt: int) -> int:
    """The BFS layer where an edge first appears: distance of its far endpoint."""
    return max(int(ds), int(dt))


def depth_schedule(layer: int, L: int, tau_lo: float, tau_hi: float) -> float:
    """Linear threshold from ``tau_lo`` at layer 1 to ``tau_hi`` at layer L."""
    w = (layer - 1) / max(L - 1, 1)
    return tau_lo + (tau_hi - tau_lo) * w


def subgraph_to_batch(subgraph, store, kg, q_ori: torch.Tensor,
                      q_index: int = 0) -> TripleBatch:
    """Build a :class:`TripleBatch` from member A's subgraph.

    Args:
        subgraph: member A's ``Subgraph``. ``subgraph.nodes[i].gid`` is the
            global entity id; ``subgraph.edges[i]`` exposes ``s`` / ``t``
            (local node indices) and ``r`` (relation string).
        store: member B's ``EmbeddingStore`` (``entity`` / ``relation`` /
            ``entity_is_mid`` lookups).
        kg: member A's ``TripleIndex`` (``relation_id`` lookup).
        q_ori: original-question embedding ``[B, d]`` with ``B >= 1``.
        q_index: which row of ``q_ori`` every triple belongs to (single
            question = 0 for every edge).
    """
    n = len(subgraph.edges)
    head_gid = [subgraph.nodes[e.s].gid for e in subgraph.edges]
    tail_gid = [subgraph.nodes[e.t].gid for e in subgraph.edges]
    rel_id = [kg.relation_id(e.r) for e in subgraph.edges]
    return TripleBatch(
        head_emb=store.entity(head_gid),
        rel_emb=store.relation(rel_id),
        tail_emb=store.entity(tail_gid),
        head_is_mid=store.entity_is_mid(head_gid),
        tail_is_mid=store.entity_is_mid(tail_gid),
        q_index=torch.full((n,), q_index, dtype=torch.long),
        q_ori=q_ori,
    )


def fse_expand(subgraph, prob: torch.Tensor, tau_e: float = 0.1,
               tau_lo: float | None = None, tau_hi: float | None = None,
               L: int = 3, beta: float | None = None,
               sigma_eps: float = 1e-6) -> torch.Tensor:
    """Return a boolean keep mask aligned with ``subgraph.edges``.

    Modes (mutually exclusive; later ones take precedence):
      * fixed:      keep ``prob > tau_e`` (tau_lo / tau_hi / beta all None).
      * scheduled:  keep ``prob > depth_schedule(max(ds, dt))`` (tau_lo / tau_hi).
      * degree:     keep ``prob > mu_v + beta * sigma_v`` per frontier entity ``v``.

    The frontier entity of an edge is its far endpoint (``e.src``); grouping by it
    makes the cutoff scale-aware for high-degree vs low-degree entities.
    """
    scores = prob.detach().float()

    local_cut: dict[int, float] | None = None
    if beta is not None:
        groups: dict[int, list[float]] = {}
        for e, s in zip(subgraph.edges, scores):
            groups.setdefault(int(e.src), []).append(float(s))
        local_cut = {}
        for v, vals in groups.items():
            if len(vals) < 2:            # too few candidates -> no reliable statistics
                local_cut[v] = float("-inf")
                continue
            mu = sum(vals) / len(vals)
            var = sum((x - mu) ** 2 for x in vals) / len(vals)   # population variance
            local_cut[v] = mu + beta * max(math.sqrt(var), sigma_eps)

    keep = []
    for e, p in zip(subgraph.edges, scores):
        if local_cut is not None:
            th = local_cut[int(e.src)]
        elif tau_lo is not None:
            th = depth_schedule(edge_layer(e.ds, e.dt), L, tau_lo, tau_hi)
        else:
            th = tau_e
        keep.append(bool(p.item() > th))
    return torch.tensor(keep, dtype=torch.bool)

