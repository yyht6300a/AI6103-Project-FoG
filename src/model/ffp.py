"""Foresight feedback propagation (paper Section 3.2, Eq. 6-8).

Operates on the directed subgraph oriented inward toward the source entities.
Nodes are updated in decreasing order of shortest-path distance so information
flows far-to-near:

    m_t = h_s + MLP_m([h_s; z_t])
    h_o <- h_o + phi(sum_t alpha_t * m_t)

After propagation, every edge is re-scored with the shared foresight scorer.
"""

from __future__ import annotations

import torch
import torch.nn as nn


class ForesightFeedbackPropagation(nn.Module):
    """Placeholder skeleton; fill in the far-to-near message passing layers."""

    def __init__(self, dim: int, num_layers: int = 2):
        super().__init__()
        self.dim = dim
        self.num_layers = num_layers
        # TODO: edge message MLP_m and node update activation phi.

    def forward(self, node_states: torch.Tensor, triples: torch.Tensor, sub_questions: torch.Tensor) -> torch.Tensor:
        raise NotImplementedError("Implement far-to-near feedback propagation (paper Eq. 6-8).")

