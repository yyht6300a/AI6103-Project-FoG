"""Verify the potential-subgraph boundary without model or API dependencies.

Synthetic refined scores cover Eq. 10 and later-round recovery. No scorer
training, live LLM calls or memory-management state is required.
"""

from copy import deepcopy
import math
import unittest

import yaml

from src.agent.llm_config import ROOT
from src.agent.potential_subgraph import build_potential_subgraph


class PotentialSubgraphTests(unittest.TestCase):
    """Exercise strict filtering and the member C input contract.

    Invalid data must fail explicitly rather than silently truncating or
    transforming the scores used to select LLM evidence.
    """

    def test_default_strict_boundary_matches_yaml(self) -> None:
        """Exclude equality and distinguish adjacent floating-point values.

        The shared configuration must agree with the callable's default.
        """
        triples = [("below", "r", "o"), ("equal", "r", "o"), ("above", "r", "o")]
        scores = [math.nextafter(0.7, -math.inf), 0.7, math.nextafter(0.7, math.inf)]
        self.assertEqual(build_potential_subgraph(triples, scores), [triples[2]])
        with (ROOT / "config/config-dev.yml").open(encoding="utf-8") as stream:
            threshold = yaml.safe_load(stream)["potential_threshold"]
        self.assertEqual(threshold, 0.7)
        self.assertEqual(build_potential_subgraph(triples, scores, threshold), [triples[2]])

    def test_graph_identity_order_and_input_preservation(self) -> None:
        """Preserve literal strings and first qualifying order while deduplicating.

        Reversed edges remain distinct; changing an input list later cannot
        change the immutable triples returned to the memory module.
        """
        triples = [[" 国家 ", "r", "B"], ["B", "r", " 国家 "],
                   [" 国家 ", "r", "B"], ["C", "r", "D"]]
        scores = [0.1, 0.8, 0.9, 0.75]
        snapshot = deepcopy((triples, scores))
        result = build_potential_subgraph(triples, scores)
        self.assertEqual(result, [tuple(triples[1]), tuple(triples[2]), tuple(triples[3])])
        self.assertEqual((triples, scores), snapshot)
        self.assertEqual(build_potential_subgraph(triples * 2, scores * 2), result)
        triples[1][0] = "changed"
        self.assertEqual(result[0], ("B", "r", " 国家 "))

    def test_empty_results_and_later_recovery(self) -> None:
        """Allow no candidates and recover a previously filtered edge next round.

        Threshold filtering must not create persistent rejection state.
        """
        triple = ("A", "r", "B")
        self.assertEqual(build_potential_subgraph([], []), [])
        self.assertEqual(build_potential_subgraph([triple], [0.6]), [])
        self.assertEqual(build_potential_subgraph([triple], [0.9]), [triple])

    def test_custom_threshold_uses_unmodified_scores(self) -> None:
        """Accept finite score scales without assuming probability calibration.

        Applying sigmoid internally would change both of these decisions.
        """
        triples = [("A", "r", "B"), ("C", "r", "D")]
        self.assertEqual(build_potential_subgraph(triples, [-2, -1], -1.5), [triples[1]])
        self.assertEqual(build_potential_subgraph(triples, [1, 2], 1), [triples[1]])

    def test_invalid_numbers_and_alignment(self) -> None:
        """Reject nonfinite scalars, booleans and mismatched input lengths.

        Invalid thresholds also fail on empty inputs; no zip truncation occurs.
        """
        triple = ("A", "r", "B")
        for value in (True, None, "0.8", [], complex(1, 2), math.nan,
                      math.inf, -math.inf, 10 ** 1000):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(ValueError):
                    build_potential_subgraph([triple], [value])
                with self.assertRaises(ValueError):
                    build_potential_subgraph([], [], value)
        for triples, scores in (([triple], []), ([], [0.8]), (None, []),
                                ([], None), ("abc", [0.8]), ([], ""), ({}, [])):
            with self.subTest(triples=triples, scores=scores), self.assertRaises(ValueError):
                build_potential_subgraph(triples, scores)

    def test_malformed_triples_are_validated_even_when_filtered(self) -> None:
        """Reject malformed evidence without printing its contents in errors.

        All candidates are validated, including rows with low scores.
        """
        for triple in (None, "secret", [], ["secret", "r"], ["secret", "r", "o", "x"],
                       ["secret", None, "o"], ["secret", " ", "o"], ["secret", "r", 1]):
            with self.subTest(triple=triple), self.assertRaises(ValueError) as error:
                build_potential_subgraph([("A", "r", "B"), triple], [0.9, 0.1])
            self.assertNotIn("secret", str(error.exception))
