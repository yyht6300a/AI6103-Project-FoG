"""Training-free stand-in for :class:`ForesightTripleScorer`, for integration work.

Same interface (``encode`` / ``score`` / ``prob`` / ``forward``, attribute ``d``), so
members C and D can wire up FSE, FFP and MSM before the real scorer is trained.

Scoring rule: z_t is the normalised sum of the triple's three embeddings (unnamed
nodes contribute zero), and the logit is a scaled cosine between
``z_t + h_s + h_o`` and the mean of the question's sub-question vectors. It has no
trainable parameters and gives no quality guarantee.
"""

from __future__ import annotations

from typing import Optional

import torch
import torch.nn as nn
import torch.nn.functional as F

from .scorer import TripleBatch


class CosineScorer(nn.Module):
    def __init__(self, emb_dim: int = 2560, scale: float = 10.0, shift: float = 0.3) -> None:
        super().__init__()
        self.d = emb_dim
        self.scale = scale
        self.shift = shift

    def encode(self, batch: TripleBatch) -> torch.Tensor:
        head = batch.head_emb.masked_fill(batch.head_is_mid.unsqueeze(-1), 0.0)
        tail = batch.tail_emb.masked_fill(batch.tail_is_mid.unsqueeze(-1), 0.0)
        return F.normalize(head + batch.rel_emb + tail, dim=-1)

    def score(self, z: torch.Tensor, h_s: Optional[torch.Tensor], h_o: Optional[torch.Tensor],
              q_sub: torch.Tensor, q_sub_mask: torch.Tensor, q_index: torch.Tensor) -> torch.Tensor:
        x = z
        if h_s is not None:
            x = x + h_s
        if h_o is not None:
            x = x + h_o
        weights = q_sub_mask.to(q_sub.dtype).unsqueeze(-1)
        q_mean = (q_sub * weights).sum(1) / weights.sum(1).clamp_min(1.0)          # [B, d]
        cos = F.cosine_similarity(x, q_mean[q_index], dim=-1)
        return self.scale * (cos - self.shift)

    def prob(self, z, h_s, h_o, q_sub, q_sub_mask, q_index) -> torch.Tensor:
        return torch.sigmoid(self.score(z, h_s, h_o, q_sub, q_sub_mask, q_index))

    def forward(self, batch: TripleBatch, q_sub: torch.Tensor, q_sub_mask: torch.Tensor,
                h_s: Optional[torch.Tensor] = None, h_o: Optional[torch.Tensor] = None) -> torch.Tensor:
        return self.score(self.encode(batch), h_s, h_o, q_sub, q_sub_mask, batch.q_index)
