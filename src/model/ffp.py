"""Foresight feedback propagation (paper Section 3.2, Eq 6-8) - member C.

Far-to-near message passing over member A's inward-oriented expanded subgraph,
followed by re-scoring with member B's shared foresight scorer.

    m_t = h_s + MLP_m([h_s; z_t])                      (Eq 6)
    h_o <- h_o + phi(sum_{t in G_in(o)} alpha_t m_t)   (Eq 8)

Here ``s`` / ``o`` are the inward-oriented endpoints: ``s`` is the far node
(``edge.src``) and ``o`` is the near node (``edge.dst``). ``z_t`` is the
question-guided triple embedding computed once by member B's scorer and kept
fixed within a reasoning round.

Message weighting (``alpha_t``): the paper's Eq 7 defines alpha over
sub-questions while Eq 8 aggregates per edge and the prose says "compatibility
between message and target state". This first version uses uniform aggregation
(alpha_t = 1) and leaves attention weighting as a documented ablation.

Re-scoring keeps the *original* subject/object orientation: ``score`` receives
``h[edge.s]`` / ``h[edge.t]`` (not the far/near indices), because ``z_t`` was
built from the original triple and ``MLP_s`` consumes ``[z_t; h_s; h_o]``.
"""

from __future__ import annotations

import torch
import torch.nn as nn


class ForesightFeedbackPropagation(nn.Module):
    """Trainable far-to-near message passing over the expanded subgraph."""

    def __init__(self, dim: int, num_layers: int = 2,
                 mlp_hidden: int | None = None,
                 activation: str = "tanh", dropout: float = 0.0):
        super().__init__()
        self.dim = dim
        self.num_layers = num_layers
        hidden = mlp_hidden or 4 * dim
        self.mlp_m = nn.Sequential(
            nn.Linear(2 * dim, hidden),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden, dim),
        )
        self.phi = nn.Tanh() if activation == "tanh" else nn.ReLU()

    def message(self, h_src: torch.Tensor, z: torch.Tensor) -> torch.Tensor:
        """Eq 6: ``m_t = h_s + MLP_m([h_s; z_t])``."""
        return h_src + self.mlp_m(torch.cat([h_src, z], dim=-1))

    def forward(self, h: torch.Tensor, edge_src: torch.Tensor,
                edge_dst: torch.Tensor, z: torch.Tensor,
                src_dist: torch.Tensor) -> torch.Tensor:
        """Update node states with ``num_layers`` far-to-near sweeps.

        ``edge_src`` / ``edge_dst`` are local node indices of the far / near
        endpoints. ``src_dist[i]`` is the distance of ``edge_src[i]`` and orders
        the sweep so that farther nodes are updated before nearer ones.
        """
        for _ in range(self.num_layers):
            for d in range(int(src_dist.max().item()), 0, -1):
                sel = src_dist == d
                if not bool(sel.any()):
                    continue
                m = self.message(h[edge_src[sel]], z[sel])
                agg = torch.zeros_like(h)
                agg.index_add_(0, edge_dst[sel], m)
                h = h + self.phi(agg)
        return h


def ffp_node_states(ffp: ForesightFeedbackPropagation, z: torch.Tensor,
                    subgraph, device) -> torch.Tensor:
    """Message passing only: return the updated node states ``[n_nodes, dim]``.

    ``z`` must be aligned with ``subgraph.edges``. Lateral edges (ds == dt) do
    not propagate; they only remain available for the later re-scoring step.
    """
    n_nodes = len(subgraph.nodes)
    h = torch.zeros(n_nodes, ffp.dim, device=device, dtype=z.dtype)
    far = torch.tensor([e.src for e in subgraph.edges], dtype=torch.long, device=device)
    near = torch.tensor([e.dst for e in subgraph.edges], dtype=torch.long, device=device)
    node_dist = torch.tensor([n.dist for n in subgraph.nodes], dtype=torch.long, device=device)
    src_dist = node_dist[far]
    strict = src_dist > node_dist[near]
    if not bool(strict.any()):
        return h
    return ffp(h, far[strict], near[strict], z[strict], src_dist[strict])


def ffp_propagate(ffp: ForesightFeedbackPropagation, z: torch.Tensor,
                  subgraph, scorer, q_sub, q_sub_mask, q_index):
    """Run FFP over the expanded subgraph, then re-score every edge.

    ``z`` must be aligned with ``subgraph.edges`` (only the edges kept by FSE).
    ``subgraph.nodes`` is the full node list; isolated nodes keep zero state.

    Returns ``(h, refined_prob)`` where ``h`` is ``[n_nodes, dim]`` and
    ``refined_prob`` is ``sigma(s_t)`` per edge, in ``subgraph.edges`` order.
    """
    device = z.device
    h = ffp_node_states(ffp, z, subgraph, device)
    head = torch.tensor([e.s for e in subgraph.edges], dtype=torch.long, device=device)
    tail = torch.tensor([e.t for e in subgraph.edges], dtype=torch.long, device=device)
    logit = scorer.score(z, h[head], h[tail], q_sub, q_sub_mask, q_index)
    return h, torch.sigmoid(logit)

