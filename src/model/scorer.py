"""Foresight triple scorer interface (paper Section 3.1 and 3.2).

The scorer consumes a question-conditioned triple representation z_t, the
structural context [z_t; h_s; h_o], and an attention-pooled sub-question
vector q_bar_t, then outputs the foresight score s_t:

    u_t   = [e_s + p_s; e_r + p_r; e_o + p_o]   (role-biased input)
    z_t   = CrossAttn(query=q_ori, key/value=SelfAttn(u_t))
    q_bar = sum_t alpha_{t,q} * q               (attention over sub-questions)
    s_t   = MLP_s([z_t; h_s; h_o; q_bar])

Implement the actual transformer blocks here. The encoder weights are frozen;
only role biases, attention projections, and the scorer MLP are trained.
"""

from __future__ import annotations

import torch
import torch.nn as nn


class ForesightTripleScorer(nn.Module):
    """Placeholder skeleton; fill in the encoder and scoring layers."""

    def __init__(self, dim: int, num_sub_questions: int):
        super().__init__()
        self.dim = dim
        self.num_sub_questions = num_sub_questions
        # TODO: role biases p_s, p_r, p_o; self/cross attention; scorer MLP.

    def forward(self, triples: torch.Tensor, question: torch.Tensor, sub_questions: torch.Tensor) -> torch.Tensor:
        raise NotImplementedError("Implement the foresight triple scorer (paper Eq. 1-4).")

