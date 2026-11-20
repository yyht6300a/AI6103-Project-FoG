# -*- coding: utf-8 -*-
"""Member C's FSE + FFP inference entry (per-question, single reasoning round).

Turns member A's candidate subgraph into the expanded subgraph ``G_xpd`` and its
FFP-refined edge scores:

    subgraph_to_batch -> scorer.encode -> scorer.prob -> fse_expand -> ffp_propagate

The returned refined probabilities are the sigmoid scores that member D's
``potential_subgraph`` consumes with ``tau_p = 0.7``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import torch

from .ffp import ForesightFeedbackPropagation, ffp_propagate
from .fse import fse_expand, subgraph_to_batch
from .scorer import ForesightTripleScorer, pack_sub_questions, sub_question_set


@dataclass
class FseFfpOutput:
    keep: torch.Tensor             # bool, aligned with subgraph.edges
    kept_edges: list               # member A's SubgraphEdge objects that survived FSE
    refined_prob: torch.Tensor     # sigmoid scores [n_kept], aligned with kept_edges
    z: torch.Tensor                # question-guided embedding [n_kept, d]
    node_states: torch.Tensor      # updated h [n_nodes, d]
    empty_reason: str = ""


@dataclass
class _PrunedSubgraph:
    nodes: list
    edges: list = field(default_factory=list)


def run_fse_ffp(scorer: ForesightTripleScorer, ffp: ForesightFeedbackPropagation,
                store, kg, subgraph, question: str,
                sub_questions: Sequence[str] = (),
                tau_e: float = 0.1, tau_lo: float | None = 0.02,
                tau_hi: float | None = 0.10, L: int = 3,
                beta: float | None = None, sigma_eps: float = 1e-6,
                device="cpu") -> FseFfpOutput:
    """Score, prune, propagate, and re-score one question's subgraph."""
    if not getattr(subgraph, "edges", None):
        return FseFfpOutput(
            keep=torch.zeros(0, dtype=torch.bool),
            kept_edges=[], refined_prob=torch.zeros(0),
            z=torch.zeros(0, ffp.dim), node_states=torch.zeros(0, ffp.dim),
            empty_reason="no_edges",
        )

    # Query embeddings: original question + sub-questions (original always included).
    q_ori = store.encode_questions([question]).to(device)
    q_sub_vecs = store.encode_questions(sub_question_set(question, sub_questions)).to(device)
    q_sub, q_sub_mask = pack_sub_questions([q_sub_vecs])
    q_index = torch.zeros(len(subgraph.edges), dtype=torch.long, device=device)

    batch = subgraph_to_batch(subgraph, store, kg, q_ori).to(device)
    z = scorer.encode(batch)                                     # fixed within the round
    prob = scorer.prob(z, None, None, q_sub, q_sub_mask, q_index)
    keep = fse_expand(subgraph, prob, tau_e, tau_lo, tau_hi, L, beta, sigma_eps)

    kept_edges = [e for e, k in zip(subgraph.edges, keep) if bool(k)]
    if not kept_edges:
        return FseFfpOutput(
            keep=keep, kept_edges=[], refined_prob=torch.zeros(0, device=device),
            z=z[keep], node_states=torch.zeros(len(subgraph.nodes), ffp.dim, device=device),
            empty_reason="all_pruned",
        )

    z_kept = z[keep]
    pruned = _PrunedSubgraph(nodes=subgraph.nodes, edges=kept_edges)
    q_index_kept = torch.zeros(len(kept_edges), dtype=torch.long, device=device)
    h, refined = ffp_propagate(ffp, z_kept, pruned, scorer, q_sub, q_sub_mask, q_index_kept)
    return FseFfpOutput(
        keep=keep, kept_edges=kept_edges, refined_prob=refined,
        z=z_kept, node_states=h,
    )
