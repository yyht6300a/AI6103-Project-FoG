"""Fixed and adaptive threshold policies for the two decision points."""

from __future__ import annotations

import statistics as st
from abc import ABC, abstractmethod
from collections import defaultdict

from .instrument import Candidate


class ThresholdPolicy(ABC):
    name = "base"

    @abstractmethod
    def expansion_keep(self, candidates: list[Candidate]) -> list[bool]:
        """Decision point 1: keep during FSE expansion."""

    @abstractmethod
    def potential_keep(self, candidates: list[Candidate], n_sub_questions: int) -> list[bool]:
        """Decision point 2: keep for the potential subgraph."""


class FixedThresholdPolicy(ThresholdPolicy):
    name = "fixed"

    def __init__(self, tau_e: float = 0.1, tau_p: float = 0.7):
        self.tau_e = tau_e
        self.tau_p = tau_p

    def expansion_keep(self, candidates):
        return [c.score > self.tau_e for c in candidates]

    def potential_keep(self, candidates, n_sub_questions):
        return [c.score > self.tau_p for c in candidates]


class DepthDegreeAdaptivePolicy(ThresholdPolicy):
    """Depth-scheduled threshold plus per-frontier-entity z-normalization."""

    name = "depth_degree"

    def __init__(self, tau_lo: float = 0.05, tau_hi: float = 0.25, L: int = 3,
                 beta: float = 1.0, sigma_eps: float = 1e-6):
        self.tau_lo = tau_lo
        self.tau_hi = tau_hi
        self.L = L
        self.beta = beta
        self.sigma_eps = sigma_eps

    def _depth_cutoff(self, depth: int) -> float:
        w = (depth - 1) / max(self.L - 1, 1)
        return self.tau_lo + (self.tau_hi - self.tau_lo) * w

    def _local_cutoffs(self, candidates):
        groups = defaultdict(list)
        for c in candidates:
            groups[c.frontier_entity].append(c.score)
        return {
            v: st.mean(s) + self.beta * max(st.pstdev(s), self.sigma_eps)
            for v, s in groups.items()
        }

    def expansion_keep(self, candidates):
        local = self._local_cutoffs(candidates)
        out = []
        for c in candidates:
            cutoff = max(self._depth_cutoff(c.depth), local[c.frontier_entity])
            out.append(c.score >= cutoff)
        return out

    def potential_keep(self, candidates, n_sub_questions):
        raise NotImplementedError("Use BudgetPotentialPolicy for the potential stage")


class BudgetPotentialPolicy(ThresholdPolicy):
    """Top-k selection with a budget that scales with question complexity."""

    name = "budget"

    def __init__(self, k0: int = 5, alpha: int = 3):
        self.k0 = k0
        self.alpha = alpha

    def potential_keep(self, candidates, n_sub_questions):
        k = min(self.k0 + self.alpha * n_sub_questions, len(candidates))
        ranked = sorted(candidates, key=lambda c: c.score, reverse=True)
        top = set(ranked[:k])
        return [c in top for c in candidates]

    def expansion_keep(self, candidates):
        raise NotImplementedError("Use DepthDegreeAdaptivePolicy for the expansion stage")


class ComposedPolicy(ThresholdPolicy):
    """Combine an expansion policy with a potential policy."""

    def __init__(self, expansion: ThresholdPolicy, potential: ThresholdPolicy):
        self.expansion = expansion
        self.potential = potential
        self.name = f"{expansion.name}+{potential.name}"

    def expansion_keep(self, candidates):
        return self.expansion.expansion_keep(candidates)

    def potential_keep(self, candidates, n_sub_questions):
        return self.potential.potential_keep(candidates, n_sub_questions)


class OraclePolicy(ThresholdPolicy):
    """Upper bound: keep every gold edge plus a fixed noise budget."""

    name = "oracle"

    def __init__(self, noise_budget: int = 10):
        self.noise_budget = noise_budget

    def expansion_keep(self, candidates):
        return [bool(c.is_gold) or i < self.noise_budget for i, c in enumerate(candidates)]

    def potential_keep(self, candidates, n_sub_questions):
        return [True] * len(candidates)

