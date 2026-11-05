"""Retrieval metrics from paper Section 4.5."""

from __future__ import annotations

from .instrument import Candidate


def evidence_recall(kept: list[Candidate], gold_triples: set[tuple]) -> float:
    gold = set(gold_triples)
    hit = sum(1 for c in kept if c.triple in gold)
    return hit / max(len(gold), 1)


def noise_pruning(all_candidates: list[Candidate], kept: list[Candidate],
                  gold_triples: set[tuple]) -> float:
    noise = [c for c in all_candidates if c.triple not in gold_triples]
    kept_noise = sum(1 for c in kept if c.triple not in gold_triples)
    return 1 - kept_noise / max(len(noise), 1)


def path_hit(kept: list[Candidate], gold_paths: list[list[tuple]]) -> bool:
    kept_set = {c.triple for c in kept}
    return any(all(t in kept_set for t in path) for path in gold_paths)


def one_shot_rate(round_kept: list[list[Candidate]], gold_triples: set[tuple]) -> bool:
    gold = set(gold_triples)
    return any(gold.issubset({c.triple for c in kept}) for kept in round_kept)

