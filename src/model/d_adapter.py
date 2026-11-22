# -*- coding: utf-8 -*-
"""C -> D adapter: convert member C's FSE/FFP output into member D's potential-subgraph input.

Member D's ``potential_subgraph.build_potential_subgraph(triples, scores, tau_p)``
expects triples in member A's name space plus the FFP-refined scores aligned with
them. This module performs that conversion so C and D can be wired without either
side changing its interface.

After member D (``part_d``) is merged, the one-call bridge
``build_potential_from_output`` runs D's selection on C's output directly.
"""

from __future__ import annotations

from .infer import FseFfpOutput


def fse_ffp_to_potential(out: FseFfpOutput, subgraph):
    """Return ``(triples, refined_scores)`` aligned with ``out.kept_edges``.

    ``triples`` are ``[head_name, relation, tail_name]`` in the original KG
    direction (``e.s`` / ``e.t``), matching member A's data contract and member
    D's prompt vocabulary. ``refined_scores`` are the FFP re-scored probabilities
    that member D compares against ``tau_p = 0.7``.
    """
    nodes = subgraph.nodes
    triples = [[nodes[e.s].name, e.r, nodes[e.t].name] for e in out.kept_edges]
    scores = [float(p.detach()) for p in out.refined_prob]
    return triples, scores


def build_potential_from_output(out: FseFfpOutput, subgraph, potential_threshold: float = 0.7):
    """One-call bridge: run member D's potential-subgraph selection on C's output."""
    from src.agent.potential_subgraph import build_potential_subgraph  # part_d (lazy import)

    triples, scores = fse_ffp_to_potential(out, subgraph)
    return build_potential_subgraph(triples, scores, potential_threshold)
