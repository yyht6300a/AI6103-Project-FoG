"""Question-conditioned triple encoder and foresight triple scorer (paper Sec 3.1, Eq 1-4).

    u_t   = [e_s + p_s ; e_r + p_r ; e_o + p_o]            in R^{3 x d}
    u^self_t = SelfAttn(u_t)                                (Eq 1)
    z_t   = CrossAttn(query = q_ori, key/value = u^self_t)  (Eq 2)
    c_t   = [z_t ; h_s ; h_o]                               (h_v initialised to zero)
    q̄_t  = sum_q alpha_{t,q} q,  alpha = Softmax_q(F_attn(c_t, q))   (Eq 3)
    s_t   = MLP_s([c_t ; q̄_t])                             (Eq 4)

What the paper fixes, and what this module therefore uses:

* d is the embedding dimension: u_t is built directly from the frozen embeddings
  plus role biases, and Sec 3.4 assumes d_h = d. With Qwen3-Embedding-4B, d = 2560.
  There is no projection layer.
* Dropout 0.1 in the scorer (App. E).
* s_t is a logit: the loss (Eq 9 / Eq 19) applies sigmoid to s_t.

What the paper leaves open, and the choice made here (all in :class:`ScorerConfig`):

* "standard Transformer layers": pre-LayerNorm blocks with residuals and a GELU
  feed-forward network of width 4d; one self-attention block (L_t = 1) and one
  cross-attention block; 8 heads.
* F_attn is bilinear: F_attn(c, q) = (W c) . q / sqrt(d).
* MLP_s is Linear(4d, d) - GELU - Dropout - Linear(d, 1).
* Role biases start at zero; the CVT vector starts as a random unit-scale vector.

Project conventions (member B):

* Unnamed nodes (Freebase m./g. ids, mostly CVTs) all share one learnable vector
  that replaces their (zero) cache embedding.
* Scores handed to other modules and compared with tau_e = 0.1 / tau_p = 0.7 are
  probabilities sigma(s_t); use :meth:`ForesightTripleScorer.prob`.
* The sub-question set of a question always contains the original question in
  addition to the current sub-questions (see :func:`sub_question_set`).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F

TAU_E = 0.1  # expansion threshold, probability scale
TAU_P = 0.7  # potential-subgraph threshold, probability scale


def logit_threshold(p: float) -> float:
    """The logit that corresponds to probability ``p`` (e.g. 0.7 -> 0.847)."""
    return math.log(p / (1.0 - p))


# ---------------------------------------------------------------------- config
@dataclass
class ScorerConfig:
    emb_dim: int = 2560          # d; Qwen3-Embedding-4B output size (paper: d_h = d)
    num_heads: int = 8           # not given in the paper
    num_self_blocks: int = 1     # L_t, not given in the paper
    num_cross_blocks: int = 1    # not given in the paper
    ffn_mult: int = 4            # standard Transformer feed-forward width
    dropout: float = 0.1         # paper App. E

    def __post_init__(self) -> None:
        if self.emb_dim % self.num_heads:
            raise ValueError(f"emb_dim {self.emb_dim} is not divisible by num_heads {self.num_heads}")

    @classmethod
    def from_dict(cls, d: dict) -> "ScorerConfig":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


# ----------------------------------------------------------------------- input
@dataclass
class TripleBatch:
    """A flat batch of N triples drawn from B questions.

    ``q_index[i]`` says which question (row of ``q_ori`` / ``q_sub``) triple i belongs to.
    """

    head_emb: torch.Tensor      # [N, d]
    rel_emb: torch.Tensor       # [N, d]
    tail_emb: torch.Tensor      # [N, d]
    head_is_mid: torch.Tensor   # [N] bool
    tail_is_mid: torch.Tensor   # [N] bool
    q_index: torch.Tensor       # [N] long
    q_ori: torch.Tensor         # [B, d]

    @property
    def n(self) -> int:
        return int(self.head_emb.shape[0])

    def to(self, device) -> "TripleBatch":
        return TripleBatch(**{f.name: getattr(self, f.name).to(device) for f in fields(self)})

    @classmethod
    def from_ids(cls, store, head_ids, rel_ids, tail_ids, q_index, q_ori: torch.Tensor) -> "TripleBatch":
        """Build a batch from member A's entity / relation ids via an :class:`EmbeddingStore`."""
        return cls(
            head_emb=store.entity(head_ids),
            rel_emb=store.relation(rel_ids),
            tail_emb=store.entity(tail_ids),
            head_is_mid=store.entity_is_mid(head_ids),
            tail_is_mid=store.entity_is_mid(tail_ids),
            q_index=torch.as_tensor(q_index, dtype=torch.long),
            q_ori=q_ori,
        )


def sub_question_set(original_question: str, sub_questions: Sequence[str]) -> List[str]:
    """The query set Q_sub used in Eq 3: the original question plus the sub-questions.

    The original question is always included, so the set is never empty; duplicates
    are dropped and order is kept.
    """
    return list(dict.fromkeys([original_question, *sub_questions]))


def pack_sub_questions(per_question: Sequence[torch.Tensor]) -> Tuple[torch.Tensor, torch.Tensor]:
    """Pad per-question sub-question vectors ``[M_i, d]`` into ``q_sub [B, M, d]`` and ``mask [B, M]``."""
    if not per_question:
        raise ValueError("no questions")
    d = per_question[0].shape[-1]
    m = max(1, max(int(q.shape[0]) for q in per_question))
    q_sub = per_question[0].new_zeros((len(per_question), m, d))
    mask = torch.zeros((len(per_question), m), dtype=torch.bool, device=q_sub.device)
    for i, q in enumerate(per_question):
        q_sub[i, :q.shape[0]] = q
        mask[i, :q.shape[0]] = True
    return q_sub, mask


# ---------------------------------------------------------------------- layers
class CrossAttentionBlock(nn.Module):
    """Pre-LayerNorm cross-attention block: the question attends over the triple tokens."""

    def __init__(self, d: int, num_heads: int, ffn_dim: int, dropout: float) -> None:
        super().__init__()
        self.norm_q = nn.LayerNorm(d)
        self.norm_kv = nn.LayerNorm(d)
        self.attn = nn.MultiheadAttention(d, num_heads, dropout=dropout, batch_first=True)
        self.norm_ffn = nn.LayerNorm(d)
        self.ffn = nn.Sequential(
            nn.Linear(d, ffn_dim), nn.GELU(), nn.Dropout(dropout), nn.Linear(ffn_dim, d))
        self.dropout = nn.Dropout(dropout)

    def forward(self, query: torch.Tensor, kv: torch.Tensor) -> torch.Tensor:
        kv = self.norm_kv(kv)
        x = query + self.dropout(self.attn(self.norm_q(query), kv, kv, need_weights=False)[0])
        return x + self.dropout(self.ffn(self.norm_ffn(x)))


# ----------------------------------------------------------------------- model
class ForesightTripleScorer(nn.Module):
    """Triple encoder (Eq 1-2) and foresight scorer (Eq 3-4).

    Two-step use, so FFP can re-score without re-encoding:

    >>> z = scorer.encode(batch)                                  # [N, d], fixed across rounds
    >>> logits = scorer.score(z, h_s, h_o, q_sub, q_sub_mask, batch.q_index)
    >>> probs = torch.sigmoid(logits)                             # compare with tau_e / tau_p
    """

    def __init__(self, config: Optional[ScorerConfig] = None) -> None:
        super().__init__()
        self.config = config or ScorerConfig()
        d = self.config.emb_dim
        ffn = self.config.ffn_mult * d
        drop = self.config.dropout
        self.d = d

        self.role_bias = nn.Parameter(torch.zeros(3, d))                   # p_s, p_r, p_o
        self.cvt_emb = nn.Parameter(torch.randn(d) / math.sqrt(d))         # shared unnamed-node vector
        self.self_blocks = nn.ModuleList([
            nn.TransformerEncoderLayer(d, self.config.num_heads, ffn, drop, activation="gelu",
                                       batch_first=True, norm_first=True)
            for _ in range(self.config.num_self_blocks)
        ])
        self.cross_blocks = nn.ModuleList([
            CrossAttentionBlock(d, self.config.num_heads, ffn, drop)
            for _ in range(self.config.num_cross_blocks)
        ])
        self.z_norm = nn.LayerNorm(d)
        self.f_attn = nn.Linear(3 * d, d, bias=False)                      # F_attn(c, q) = (W c) . q
        self.mlp_s = nn.Sequential(
            nn.Linear(4 * d, d), nn.GELU(), nn.Dropout(drop), nn.Linear(d, 1))

    # ------------------------------------------------------------- Eq 1-2
    def triple_tokens(self, batch: TripleBatch) -> torch.Tensor:
        """u_t = [e_s + p_s; e_r + p_r; e_o + p_o], unnamed nodes replaced by the CVT vector."""
        cvt = self.cvt_emb.to(batch.head_emb.dtype)
        e_s = torch.where(batch.head_is_mid.unsqueeze(-1), cvt, batch.head_emb)
        e_o = torch.where(batch.tail_is_mid.unsqueeze(-1), cvt, batch.tail_emb)
        return torch.stack([e_s, batch.rel_emb, e_o], dim=1) + self.role_bias      # [N, 3, d]

    def encode(self, batch: TripleBatch) -> torch.Tensor:
        """Question-guided triple embedding z_t ``[N, d]`` (Eq 1-2)."""
        u = self.triple_tokens(batch)
        for block in self.self_blocks:
            u = block(u)
        x = batch.q_ori[batch.q_index].unsqueeze(1)                                   # [N, 1, d]
        for block in self.cross_blocks:
            x = block(x, u)
        return self.z_norm(x.squeeze(1))

    # ------------------------------------------------------------- Eq 3-4
    def pool_sub_questions(self, c: torch.Tensor, q_sub: torch.Tensor, q_sub_mask: torch.Tensor,
                           q_index: torch.Tensor) -> torch.Tensor:
        """q̄_t (Eq 3): attention over the sub-questions of each triple's own question."""
        b, m, d = q_sub.shape
        keys = q_sub.reshape(b * m, d)
        att = self.f_attn(c) @ keys.t() / math.sqrt(d)                               # [N, B*M]
        owner = torch.arange(b * m, device=c.device) // m
        valid = (owner.unsqueeze(0) == q_index.unsqueeze(1)) & q_sub_mask.reshape(1, b * m)
        has_any = valid.any(dim=-1, keepdim=True)
        att = att.masked_fill(~valid, float("-inf"))
        att = torch.where(has_any, att, torch.zeros_like(att))                        # avoid NaN rows
        alpha = torch.softmax(att, dim=-1) * valid
        return alpha @ keys

    def score(self, z: torch.Tensor, h_s: Optional[torch.Tensor], h_o: Optional[torch.Tensor],
              q_sub: torch.Tensor, q_sub_mask: torch.Tensor, q_index: torch.Tensor) -> torch.Tensor:
        """Foresight score s_t as a logit ``[N]`` (Eq 3-4). ``h_s`` / ``h_o`` = None means zeros."""
        h_s = torch.zeros_like(z) if h_s is None else h_s
        h_o = torch.zeros_like(z) if h_o is None else h_o
        c = torch.cat([z, h_s, h_o], dim=-1)                                           # [N, 3d]
        q_bar = self.pool_sub_questions(c, q_sub, q_sub_mask, q_index)
        return self.mlp_s(torch.cat([c, q_bar], dim=-1)).squeeze(-1)

    def prob(self, z, h_s, h_o, q_sub, q_sub_mask, q_index) -> torch.Tensor:
        """sigma(s_t) ``[N]``: the scale that tau_e / tau_p and other modules use."""
        return torch.sigmoid(self.score(z, h_s, h_o, q_sub, q_sub_mask, q_index))

    def forward(self, batch: TripleBatch, q_sub: torch.Tensor, q_sub_mask: torch.Tensor,
                h_s: Optional[torch.Tensor] = None, h_o: Optional[torch.Tensor] = None) -> torch.Tensor:
        """encode + score; optional real FFP states [N, d] retain their gradients.

        None uses initial zero states. Those inputs cannot train state columns;
        attention selection also requires multiple distinct valid query vectors.
        """
        return self.score(self.encode(batch), h_s, h_o, q_sub, q_sub_mask, batch.q_index)

    # ------------------------------------------------------------- utils
    def num_parameters(self) -> int:
        return sum(p.numel() for p in self.parameters() if p.requires_grad)

    def save(self, path: str | Path, extra: Optional[dict] = None) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        torch.save({"config": asdict(self.config), "state_dict": self.state_dict(),
                    "extra": extra or {}}, path)

    @classmethod
    def load(cls, path: str | Path, map_location="cpu") -> "ForesightTripleScorer":
        ckpt = torch.load(path, map_location=map_location, weights_only=True)
        model = cls(ScorerConfig.from_dict(ckpt["config"]))
        model.load_state_dict(ckpt["state_dict"])
        return model
