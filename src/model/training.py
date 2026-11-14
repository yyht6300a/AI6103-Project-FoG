"""Scorer training loop and local negative sampling (member E).

Implements paper Section 3.2 / Appendix E and DATA_CONTRACT section 7:

- positives: triples on the gold reasoning path (``gold.triples``)
- negatives: one-hop neighbours of gold-path entities, sampled with
  ``kappa * deg_pos(v)`` per seed and filtered by relation degree
- loss: binary cross entropy over candidate triples, AdamW with the paper's
  hyperparameters.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

import torch
import torch.nn as nn


def _as_tuple(triple) -> tuple:
    """Normalize a triple-like value to ``(head, relation, tail)``."""
    try:
        return (triple[0], triple[1], triple[2])
    except (TypeError, KeyError, IndexError):
        return (triple.h, triple.r, triple.t)


def sample_negatives(
    kg,
    gold_triples: Sequence[Sequence[str]],
    *,
    kappa: int = 10,
    rel_degree_max: int = 200_000,
    seed: Optional[int] = None,
) -> list[tuple]:
    """Sample one-hop local negatives around gold-path entities.

    ``kg`` must expose: ``resolve_entity(name) -> id | None``,
    ``out_edges(eid)`` / ``in_edges(eid)`` (iterables of edge ids),
    ``edge_triple(eid) -> (head, relation, tail)``,
    ``relation_id(relation) -> id`` and an indexable ``rel_degree`` array.
    """
    rng = random.Random(seed)
    positives = {tuple(t) for t in gold_triples}
    pos_degree: Counter[str] = Counter()
    for h, _r, t in positives:
        pos_degree[h] += 1
        pos_degree[t] += 1

    negatives: list[tuple] = []
    for name, deg in pos_degree.items():
        eid = kg.resolve_entity(name)
        if eid is None:
            continue
        seen: set[tuple] = set()
        for edge_id in [*kg.out_edges(eid), *kg.in_edges(eid)]:
            triple = _as_tuple(kg.edge_triple(edge_id))
            if triple in positives or triple in seen:
                continue
            if kg.rel_degree[kg.relation_id(triple[1])] > rel_degree_max:
                continue
            seen.add(triple)
        k = kappa * deg
        negatives.extend(rng.sample(sorted(seen), min(k, len(seen))))
    return negatives


@dataclass
class TrainingConfig:
    """Paper-reported optimizer configuration."""

    epochs: int = 5
    lr: float = 1e-5
    beta1: float = 0.9
    beta2: float = 0.999
    weight_decay: float = 1e-3
    device: str = "cpu"


def train_scorer(
    scorer: nn.Module,
    batches: Iterable[tuple[list, torch.Tensor]],
    config: Optional[TrainingConfig] = None,
    *,
    save_path: Optional[str] = None,
) -> list[float]:
    """Run the BCE / AdamW loop over re-iterable ``batches``.

    Each batch is ``(inputs, labels)`` where ``inputs`` is a list of arguments
    forwarded to ``scorer(*inputs)`` and ``labels`` is a float tensor of
    ``0/1`` with the same first dimension as the scorer output. ``batches``
    must be re-iterable (for example a list), since every epoch consumes it
    once.
    """
    cfg = config or TrainingConfig()
    scorer = scorer.to(cfg.device)
    scorer.train()
    optimizer = torch.optim.AdamW(
        scorer.parameters(),
        lr=cfg.lr,
        betas=(cfg.beta1, cfg.beta2),
        weight_decay=cfg.weight_decay,
    )
    loss_fn = nn.BCEWithLogitsLoss()
    history: list[float] = []
    for _ in range(cfg.epochs):
        total = 0.0
        n = 0
        for inputs, labels in batches:
            inputs = [
                x.to(cfg.device) if isinstance(x, torch.Tensor) else x
                for x in inputs
            ]
            labels = labels.to(cfg.device).float()
            optimizer.zero_grad()
            logits = scorer(*inputs)
            loss = loss_fn(logits.view(-1), labels.view(-1))
            loss.backward()
            optimizer.step()
            total += loss.item() * labels.numel()
            n += labels.numel()
        history.append(total / max(n, 1))
    if save_path is not None:
        torch.save(scorer.state_dict(), save_path)
    return history


def load_scorer(scorer: nn.Module, path: str) -> nn.Module:
    """Load weights saved by :func:`train_scorer` into ``scorer``."""
    scorer.load_state_dict(torch.load(path, map_location="cpu"))
    return scorer
