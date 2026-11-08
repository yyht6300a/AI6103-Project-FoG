"""Validate permanent rejection and temporary threshold hiding via scored rounds.

Only HTTP transport is replaced. Public MSM orchestration, prompting, response
parsing and accounting run with synthetic credentials and private usage files.
"""

import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import requests

from src.agent.llm_client import LLMClient
from src.agent.llm_config import LLMConfig, ROOT
from src.agent.memory_management import manage_memory
from src.agent.memory_state import QuestionMemory
from test_memory_update import A, B, C, REJECTED, completion


class ControlledRejectionRoundsTests(unittest.TestCase):
    """Track rejected and hidden evidence across an entire question run.

    Fixed expected sets independently specify each prompt and state transition;
    stale high-scoring inputs deliberately challenge the facade's exclusion rule.
    """

    def test_permanent_masks_and_threshold_recovery_through_http_client(self) -> None:
        """Recover hidden rows while submitted omissions stay blocked.

        Below-cutoff and equal-cutoff evidence remains unseen for two rounds,
        then enters memory. Removed historical evidence also becomes rejected,
        and empty memory must not reset accumulated masks. Default and custom
        cutoffs exercise the same strict comparison without lowering thresholds.
        """
        for threshold in (0.7, 0.5):
            with self.subTest(threshold=threshold):
                question = "Where was the leader of the country with Song C born?"
                candidates = [A, B, C, REJECTED]
                # Input scores, selected output, submitted union, cumulative
                # rejection and eligible expansion are independent expectations.
                hidden_scores = [0.95, 0.95, threshold - 0.1, threshold]
                high_scores = [0.95] * len(candidates)
                rounds = (
                    (hidden_scores, [A], {A, B}, {B}, [C, REJECTED]),
                    (hidden_scores, [A], {A}, {B}, [C, REJECTED]),
                    (high_scores, [A, C, REJECTED], {A, C, REJECTED}, {B}, []),
                    (high_scores, [C], {A, C, REJECTED}, {A, B, REJECTED}, []),
                    (high_scores, [], {C}, set(candidates), []),
                    (high_scores, [], set(), set(candidates), []),
                )
                responses = []
                for _, selected, _, _, _ in rounds:
                    response = requests.Response()
                    response.status_code = 200
                    response._content = json.dumps(completion(
                        selected, questions=["Which evidence should be retrieved next?"]
                    )).encode("utf-8")
                    responses.append(response)

                (ROOT / "tmp").mkdir(exist_ok=True)
                with tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="tmp_rejection_rounds_") as name:
                    usage_path = Path(name) / "tmp_usage.csv"
                    config = LLMConfig(model_name="gpt-4o-mini", api_key="offline-test-key",
                                       base_url="https://example.invalid/v1", timeout=60,
                                       max_retries=0)
                    client = LLMClient(config, usage_path)
                    snapshots = [QuestionMemory(question)]
                    expected_states = [(frozenset(), frozenset())]
                    try:
                        with patch("requests.adapters.HTTPAdapter.send", side_effect=responses) as send:
                            for index, (scores, selected, submitted, rejected, eligible) in enumerate(rounds):
                                with self.subTest(round=index + 1):
                                    prior = snapshots[-1]
                                    result = manage_memory(prior, candidates, scores, client,
                                                           potential_threshold=threshold)
                                    self.assertEqual(send.call_count, index + 1)
                                    payload = json.loads(send.call_args.args[0].body)
                                    prompt = payload["messages"][0]["content"]
                                    self.assertIn("Question: " + question, prompt)
                                    knowledge = json.loads(prompt.rsplit("Knowledge: ", 1)[1])
                                    self.assertEqual(len(knowledge), len(submitted))
                                    self.assertEqual({tuple(row) for row in knowledge}, submitted)
                                    self.assertIsNot(result.state, prior)
                                    self.assertEqual(result.state.original_question, question)
                                    self.assertEqual(result.state.memory_triplets, set(selected))
                                    self.assertEqual(result.state.rejected_triplets, rejected)
                                    self.assertEqual(result.state.excluded_triplets, set(selected) | rejected)
                                    self.assertEqual(result.state.filter_expansion_triplets(candidates), eligible)
                                    self.assertEqual(result.response.selected_triplets, tuple(selected))
                                    self.assertEqual(result.response.predict_answer, ())
                                    self.assertEqual(result.response.subsequent_question,
                                                     ("Which evidence should be retrieved next?",))
                                    snapshots.append(result.state)
                                    expected_states.append((frozenset(selected), frozenset(rejected)))
                                    self.assertEqual([(state.memory_triplets, state.rejected_triplets)
                                                      for state in snapshots], expected_states)
                    finally:
                        client.close()

                    with usage_path.open(encoding="utf-8", newline="") as stream:
                        rows = list(csv.DictReader(stream))
                    self.assertEqual(len(rows), len(rounds))
                    self.assertEqual([row["status"] for row in rows], ["success"] * len(rounds))
