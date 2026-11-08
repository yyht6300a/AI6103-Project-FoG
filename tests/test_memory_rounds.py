"""Validate controlled multi-round memory through the public MSM boundary.

Only HTTP transport is mocked; real prompting, response decoding, validation,
snapshot replacement and usage accounting run without private configuration.
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
from test_memory_update import A, B, C, completion


class ControlledMemoryRoundsTests(unittest.TestCase):
    """Check evidence accumulation and replacement across one question run.

    A fixed response script isolates state mechanics from model quality and
    exercises the same scored-round entry point that member E will call.
    """

    def test_accumulation_removal_and_clearing_through_http_client(self) -> None:
        """Accumulate three rounds, remove old evidence, then clear memory.

        Later rounds have no new candidates, so retained evidence must come
        from the previous snapshot. All saved snapshots stay unchanged.
        """
        question = "Where was the leader of the country with Song C born?"
        # Each row fixes new input, selected output, answer, clarification and
        # actual submitted union independently of the implementation's helpers.
        rounds = (
            ([A], [A], [], ["Who leads Country A?"], {A}),
            ([B], [A, B], [], ["Where was Person B born?"], {A, B}),
            ([C], [C, A, B], [C[2]], [], {A, B, C}),
            ([], [C, B], [C[2]], [], {A, B, C}),
            ([], [], [], ["Which evidence should be retrieved next?"], {B, C}),
        )
        responses = []
        for _, selected, answers, questions, _ in rounds:
            response = requests.Response()
            response.status_code = 200
            response._content = json.dumps(completion(selected, answers, questions)).encode("utf-8")
            responses.append(response)

        (ROOT / "tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="tmp_memory_rounds_") as name:
            usage_path = Path(name) / "tmp_usage.csv"
            config = LLMConfig(model_name="gpt-4o-mini", api_key="offline-test-key",
                               base_url="https://example.invalid/v1", timeout=60,
                               max_retries=0)
            client = LLMClient(config, usage_path)
            self.addCleanup(client.close)
            snapshots = [QuestionMemory(question)]
            expected_memories = [frozenset()]
            with patch("requests.adapters.HTTPAdapter.send", side_effect=responses) as send:
                for index, (candidates, selected, answers, questions, submitted) in enumerate(rounds):
                    with self.subTest(round=index + 1):
                        prior = snapshots[-1]
                        result = manage_memory(prior, candidates, [0.9] * len(candidates), client)
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
                        self.assertEqual(result.response.selected_triplets, tuple(selected))
                        self.assertEqual(result.response.predict_answer, tuple(answers))
                        self.assertEqual(result.response.subsequent_question, tuple(questions))
                        snapshots.append(result.state)
                        expected_memories.append(frozenset(selected))
                        # A future successful replacement must not mutate any
                        # earlier snapshot retained by the caller.
                        self.assertEqual([state.memory_triplets for state in snapshots],
                                         expected_memories)

            with usage_path.open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), len(rounds))
            self.assertEqual([row["status"] for row in rows], ["success"] * len(rounds))
