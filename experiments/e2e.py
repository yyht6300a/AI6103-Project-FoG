"""End-to-end answer metrics for KBQA (member E).

These compare the ranked answers produced by the agent (member D's
``MemoryResponse.predict_answer``) against the gold ``answers`` in member A's
``qa_*.jsonl.gz``.

Conventions follow the ToG / GNN-RAG style KBQA evaluation:

- Hit@k: exact, case-insensitive match between any of the top-k predicted
  answers and any gold answer.
- Hit: any predicted answer matches gold (unbounded top-k).
- F1: token-level F1 between the single top-1 prediction and the best
  matching gold answer, over lowercase alphanumeric tokens.
"""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def hit_at_k(predicted: list[str], gold: list[str], k: int) -> float:
    """Return 1.0 if any of the top-k predictions exactly matches any gold."""
    gold_norm = {g.strip().lower() for g in gold}
    return 1.0 if any(p.strip().lower() in gold_norm for p in predicted[:k]) else 0.0


def token_f1(predicted: str, gold: list[str]) -> float:
    """Token-level F1 between one predicted answer and the best gold answer."""
    p = _tokens(predicted)
    if not p:
        return 0.0
    best = 0.0
    for g in gold:
        q = _tokens(g)
        if not q:
            continue
        overlap = len(p & q)
        prec = overlap / len(p)
        rec = overlap / len(q)
        if prec + rec > 0:
            best = max(best, 2.0 * prec * rec / (prec + rec))
    return best


def f1_score(predicted: list[str], gold: list[str]) -> float:
    """Token-level F1 of the top-1 prediction against the gold answers."""
    if not predicted or not gold:
        return 0.0
    return token_f1(predicted[0], gold)


def end_to_end_metrics(predicted: list[str], gold: list[str]) -> dict[str, float]:
    """Aggregate hit, hit@1 and f1 for one question."""
    return {
        "hit": hit_at_k(predicted, gold, len(predicted)),
        "hit@1": hit_at_k(predicted, gold, 1),
        "f1": f1_score(predicted, gold),
    }
