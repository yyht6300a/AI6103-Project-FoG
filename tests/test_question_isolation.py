"""Validate independent question runs through interleaved public MSM calls.

The same real LLMClient serves every run; only HTTP transport is mocked.
Synthetic credentials and a private temporary CSV avoid paid API requests.
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


class QuestionIsolationTests(unittest.TestCase):
    """Challenge question-local memory and rejection with overlapping evidence.

    Interleaving returns to earlier runs after other runs change their masks.
    Identical query text must also support independent state lifecycles.
    """

    def test_interleaved_runs_and_fresh_restart_through_shared_client(self) -> None:
        """Keep memory, rejection and retrieval eligibility local to each run.

        Independent expectations specify submitted evidence and state after
        each call. A late-created run must start empty and accept evidence
        already rejected in both older runs, including the same query text.
        Every historical snapshot is checked after every successful update.
        """
        first_question = "Who leads the country with Song C?"
        for second_question in ("Where was Person B born?", first_question):
            with self.subTest(second_question=second_question):
                questions = {"left": first_question, "right": second_question,
                             "fresh": first_question}
                # Run, candidates, selected output, submitted union, permanent
                # rejection and eligible retrieval rows are fixed separately.
                rounds = (
                    ("left", [A, B], [A], {A, B}, {B}, [C]),
                    ("right", [B, C], [B], {B, C}, {C}, [A]),
                    ("left", [B, C], [C, A], {A, C}, {B}, []),
                    ("right", [A, C], [B, A], {A, B}, {C}, []),
                    ("left", [], [C], {A, C}, {A, B}, []),
                    ("right", [C], [A], {A, B}, {B, C}, []),
                    ("fresh", [A, B, C], [B, A], {A, B, C}, {C}, []),
                    ("left", [A, B, C], [C], {C}, {A, B}, []),
                    ("right", [A, B, C], [A], {A}, {B, C}, []),
                )
                responses = []
                for _, _, selected, _, _, _ in rounds:
                    response = requests.Response()
                    response.status_code = 200
                    response._content = json.dumps(completion(
                        selected, [selected[0][2]], ["Which evidence is needed next?"]
                    )).encode("utf-8")
                    responses.append(response)

                (ROOT / "tmp").mkdir(exist_ok=True)
                with tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="tmp_question_isolation_") as name:
                    usage_path = Path(name) / "tmp_usage.csv"
                    config = LLMConfig(model_name="gpt-4o-mini", api_key="offline-test-key",
                                       base_url="https://example.invalid/v1", timeout=60,
                                       max_retries=0)
                    client = LLMClient(config, usage_path)
                    states = {}
                    expected = {}
                    history = []
                    try:
                        with patch("requests.adapters.HTTPAdapter.send", side_effect=responses) as send:
                            for index, (run, candidates, selected, submitted, rejected, eligible) in enumerate(rounds):
                                with self.subTest(round=index + 1, run=run):
                                    if run not in states:
                                        states[run] = QuestionMemory(questions[run])
                                        expected[run] = (frozenset(), frozenset(), [A, B, C])
                                        self.assertEqual(states[run].memory_triplets, frozenset())
                                        self.assertEqual(states[run].rejected_triplets, frozenset())
                                        self.assertEqual(states[run].excluded_triplets, frozenset())
                                        self.assertEqual(states[run].filter_expansion_triplets([A, B, C]),
                                                         [A, B, C])
                                        history.append((states[run], questions[run], frozenset(), frozenset()))
                                    prior = states[run]
                                    result = manage_memory(prior, candidates, [0.9] * len(candidates), client, prompt_variant="retrieval_questions")
                                    self.assertEqual(send.call_count, index + 1)
                                    payload = json.loads(send.call_args.args[0].body)
                                    self.assertEqual(len(payload["messages"]), 1)
                                    prompt = payload["messages"][0]["content"]
                                    self.assertIn("Question: " + questions[run] + "\n", prompt)
                                    knowledge = json.loads(prompt.rsplit("Knowledge: ", 1)[1])
                                    self.assertEqual(len(knowledge), len(submitted))
                                    self.assertEqual({tuple(row) for row in knowledge}, submitted)
                                    self.assertIsNot(result.state, prior)
                                    self.assertEqual(result.response.selected_triplets, tuple(selected))
                                    self.assertEqual(result.response.predict_answer, (selected[0][2],))
                                    self.assertEqual(result.response.subsequent_question,
                                                     ("Which evidence is needed next?",))
                                    states[run] = result.state
                                    expected[run] = (frozenset(selected), frozenset(rejected), eligible)
                                    history.append((result.state, questions[run],
                                                    frozenset(selected), frozenset(rejected)))

                                    # Recheck inactive runs and every earlier snapshot,
                                    # not just the run which received this response.
                                    for label, state in states.items():
                                        memory, masks, available = expected[label]
                                        self.assertEqual(state.original_question, questions[label])
                                        self.assertEqual(state.memory_triplets, memory)
                                        self.assertEqual(state.rejected_triplets, masks)
                                        self.assertEqual(state.excluded_triplets, memory | masks)
                                        self.assertEqual(state.filter_expansion_triplets([A, B, C]), available)
                                    for snapshot, question, memory, masks in history:
                                        self.assertEqual(snapshot.original_question, question)
                                        self.assertEqual(snapshot.memory_triplets, memory)
                                        self.assertEqual(snapshot.rejected_triplets, masks)
                                        self.assertEqual(snapshot.excluded_triplets, memory | masks)
                    finally:
                        client.close()

                    with usage_path.open(encoding="utf-8", newline="") as stream:
                        rows = list(csv.DictReader(stream))
                    self.assertEqual(len(rows), len(rounds))
                    self.assertEqual([row["status"] for row in rows], ["success"] * len(rounds))
