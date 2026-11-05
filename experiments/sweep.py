"""Validation sweep for threshold policies."""

from __future__ import annotations

from itertools import product

from .metrics import evidence_recall, noise_pruning, path_hit
from .policy import (
    BudgetPotentialPolicy,
    ComposedPolicy,
    DepthDegreeAdaptivePolicy,
    FixedThresholdPolicy,
)


def evaluate(policy, questions_with_data) -> dict[str, float]:
    """Average retrieval metrics over dev questions.

    Each item in ``questions_with_data`` exposes ``run_expansion_decision``,
    ``gold_triples``, ``gold_paths``, and ``n_sub_questions``.
    """
    results = {"R_evid": [], "P_prune": [], "H_path": []}
    for q in questions_with_data:
        all_c, kept = q.run_expansion_decision(policy)
        results["R_evid"].append(evidence_recall(kept, q.gold_triples))
        results["P_prune"].append(noise_pruning(all_c, kept, q.gold_triples))
        results["H_path"].append(float(path_hit(kept, q.gold_paths)))
    return {k: sum(v) / len(v) for k, v in results.items()}


def sweep(questions_with_data) -> None:
    policies = [FixedThresholdPolicy(tau_e=0.1, tau_p=0.7)]
    for lo, hi, b, k0, a in product(
        [0.03, 0.05, 0.08], [0.20, 0.25, 0.30], [0.5, 1.0, 1.5], [3, 5], [2, 3]
    ):
        policies.append(
            ComposedPolicy(
                DepthDegreeAdaptivePolicy(tau_lo=lo, tau_hi=hi, beta=b),
                BudgetPotentialPolicy(k0=k0, alpha=a),
            )
        )
    for p in policies:
        m = evaluate(p, questions_with_data)
        print(p.name, {k: round(v, 3) for k, v in m.items()})

