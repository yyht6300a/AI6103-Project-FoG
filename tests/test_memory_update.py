"""Exercise multi-round LLM memory replacement and its failure boundary.

Synthetic graphs and mocked clients/transports avoid paid requests and private
configuration. Tests check evidence retention, removal and atomic publication.
"""

import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

from src.agent.llm_client import LLMClient
from src.agent.llm_config import LLMConfig, ROOT
from src.agent.memory_state import QuestionMemory
from src.agent.memory_update import update_memory


A = ("Country A", "anthem", "Song C")
B = ("Country A", "led_by", "Person B")
C = ("Person B", "born_in", "Town D")
REJECTED = ("Other", "relation", "Entity")


def completion(selected=(), answers=(), questions=()) -> dict:
    """Build a completed synthetic Figure 8 response for a controlled selection.

    Args:
        selected: Triples copied into the model's selected_triplets array.
        answers: Entity strings in model ranking order.
        questions: Subsequent clarification strings in model order.

    Returns:
        Chat Completions envelope containing exactly the three JSON fields.
    """
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
        "selected_triplets": selected, "predict_answer": answers,
        "subsequent_question": questions,
    })}}]}


class MemoryUpdateTests(unittest.TestCase):
    """Check Eq. 12 across successive question-local snapshots.

    Old snapshots must remain usable after both successful and failed updates.
    """

    def test_multi_round_retention_removal_and_empty_replacement(self) -> None:
        """Retain bridge evidence across rounds and later remove it explicitly.

        Every prompt must include the current memory, even without new evidence.
        """
        client = Mock(spec=LLMClient)
        client.complete.side_effect = [completion([A], questions=["Who leads Country A?"]),
                                       completion([A, B], [B[2]]),
                                       completion([B], [B[2]]),
                                       completion(questions=["Which country has Song C?"])]
        state = QuestionMemory("Who leads the country with Song C?")
        first, _ = update_memory(state, [A], client, prompt_variant="retrieval_questions")
        second, _ = update_memory(first, [B], client, prompt_variant="retrieval_questions")
        third, _ = update_memory(second, [], client, prompt_variant="retrieval_questions")
        fourth, _ = update_memory(third, [], client, prompt_variant="retrieval_questions")
        self.assertEqual(first.memory_triplets, {A})
        self.assertEqual(second.memory_triplets, {A, B})
        self.assertEqual(third.memory_triplets, {B})
        self.assertEqual(fourth.memory_triplets, set())
        self.assertEqual(first.rejected_triplets, set())
        self.assertEqual(second.rejected_triplets, set())
        self.assertEqual(third.rejected_triplets, {A})
        self.assertEqual(fourth.rejected_triplets, {A, B})
        self.assertEqual(state.memory_triplets, set())
        for call, graph in zip(client.complete.call_args_list, ({A}, {A, B}, {A, B}, {B})):
            prompt = call.args[0][0]["content"]
            knowledge = json.loads(prompt.rsplit("Knowledge: ", 1)[1])
            self.assertEqual({tuple(row) for row in knowledge}, graph)
            self.assertIn("Keep a triple unless", prompt)
            self.assertIn(state.original_question, prompt)

    def test_literal_generator_input_and_ordered_response(self) -> None:
        """Reuse materialized generator evidence for both prompting and parsing.

        Set memory deduplicates rows while response ranking and repeats survive.
        """
        literal = (' Country "A" 国家 ', "has\\relation", "Line\nB")
        client = Mock(spec=LLMClient)
        client.complete.return_value = completion([literal, B, literal], [B[2], literal[0]])
        state = QuestionMemory("Original?", rejected_triplets=[REJECTED])
        updated, result = update_memory(state, (row for row in [literal, B]), client, prompt_variant="retrieval_questions")
        self.assertEqual(updated.memory_triplets, {literal, B})
        self.assertEqual(result.selected_triplets, (literal, B, literal))
        self.assertEqual(result.predict_answer, (B[2], literal[0]))
        self.assertEqual(updated.rejected_triplets, {REJECTED})
        self.assertEqual(updated.original_question, state.original_question)
        client.close.assert_not_called()

    def test_subsequent_questions_guide_next_round_retrieval(self) -> None:
        """Thread generated queries into caller-owned retrieval and a later update.

        A first round retains bridge evidence without answering; retrieved leader
        evidence then resolves the original query, which stays in both prompts.
        """
        query = "Who leads the country with Song C?"
        questions = ("Who leads Country A?", "Who leads Country A?")
        client = Mock(spec=LLMClient)
        client.complete.side_effect = [completion([A], questions=questions),
                                       completion([A, B], [B[2]])]
        first, response = update_memory(QuestionMemory(query), [A], client, prompt_variant="retrieval_questions")
        self.assertEqual(response.predict_answer, ())
        self.assertEqual(response.subsequent_question, questions)
        retrieve = Mock(return_value=[B])
        potential = retrieve(response.subsequent_question)
        second, final = update_memory(first, potential, client, prompt_variant="retrieval_questions")
        retrieve.assert_called_once_with(questions)
        self.assertEqual(first.memory_triplets, {A})
        self.assertEqual(second.memory_triplets, {A, B})
        self.assertEqual(final.predict_answer, (B[2],))
        self.assertEqual(final.subsequent_question, ())
        for call in client.complete.call_args_list:
            prompt = call.args[0][0]["content"]
            self.assertIn("Question: " + query, prompt)
            self.assertIn("if undetermined, ask 1–3 clarification questions", prompt)

    def test_invalid_clarification_preserves_state_without_retry(self) -> None:
        """Reject missing or malformed follow-up before publishing new evidence.

        Both existing sets survive validation failures, including when the model
        returns no selected evidence. Application validation never retries.
        """
        state = QuestionMemory("Who?", [A], [REJECTED])
        client = Mock(spec=LLMClient)
        for selected in ([], [B]):
            for questions in ([], ["Q?"] * 4, [" \n"], [None]):
                client.reset_mock()
                client.complete.return_value = completion(selected, questions=questions)
                with self.subTest(selected=selected, questions=questions), self.assertRaises(ValueError):
                    update_memory(state, [B], client, prompt_variant="retrieval_questions")
                client.complete.assert_called_once()
                self.assertEqual(state.memory_triplets, {A})
                self.assertEqual(state.rejected_triplets, {REJECTED})

    def test_exact_match_failures_preserve_both_sets_without_repair(self) -> None:
        """Keep the previous snapshot when a model alters submitted evidence.

        A valid prefix selection must not publish before a later invalid triple
        or answer is rejected. Prompt constraints accompany each single call.
        """
        state = QuestionMemory("Who?", [A], [REJECTED])
        client = Mock(spec=LLMClient)
        cases = [
            completion([A, (B[2], B[1], B[0])], questions=["Who?"]),
            completion([A, (A[0], B[1], A[2])], questions=["Who?"]),
            completion([A, (B[0] + " ", B[1], B[2])], questions=["Who?"]),
            completion([A, B], [B[2] + " "]),
            completion([A, B], ["External answer"]),
        ]
        for output in cases:
            client.reset_mock()
            client.complete.return_value = output
            with self.subTest(output=output), self.assertRaises(ValueError):
                update_memory(state, [B], client, prompt_variant="retrieval_questions")
            client.complete.assert_called_once()
            prompt = client.complete.call_args.args[0][0]["content"]
            self.assertIn("No external knowledge; no fabrication;", prompt)
            self.assertIn("no rewriting/creating/reordering/merging/splitting triples.", prompt)
            self.assertIn("Selected triples must match input exactly.", prompt)
            self.assertEqual(state.memory_triplets, {A})
            self.assertEqual(state.rejected_triplets, {REJECTED})

    def test_invalid_inputs_prevent_requests(self) -> None:
        """Reject malformed or permanently masked evidence before paid work.

        Existing rejection state is never silently discarded or reintroduced.
        """
        state = QuestionMemory("Original?", [A], [REJECTED])
        client = Mock(spec=LLMClient)
        for potential in (None, "bad", [["short"]], [REJECTED]):
            with self.subTest(potential=potential), self.assertRaises(ValueError):
                update_memory(state, potential, client, prompt_variant="retrieval_questions")
        with self.assertRaises(ValueError):
            update_memory(None, [], client, prompt_variant="retrieval_questions")
        client.complete.assert_not_called()
        self.assertEqual(state.memory_triplets, {A})

    def test_every_output_field_is_validated_before_publication(self) -> None:
        """Reject bad answers and questions even when selection itself is valid.

        Out-of-input triples and malformed JSON also leave both old sets intact.
        """
        state = QuestionMemory("Original?", [A], [REJECTED])
        client = Mock(spec=LLMClient)
        cases = [completion([C]), completion([B], ["Invented"]),
                 completion([B], questions=["Q?"] * 4), completion([REJECTED])]
        malformed = completion([B])
        malformed["choices"][0]["message"]["content"] = "not JSON"
        for response in cases + [malformed]:
            client.reset_mock()
            client.complete.return_value = response
            with self.subTest(response=response), self.assertRaises(ValueError):
                update_memory(state, [B], client, prompt_variant="retrieval_questions")
            client.complete.assert_called_once()
            self.assertEqual(state.memory_triplets, {A})
            self.assertEqual(state.rejected_triplets, {REJECTED})

    def test_bad_envelopes_refusals_and_truncation(self) -> None:
        """Fail closed even if incomplete or refused content could be parsed.

        Envelope errors cause one application call without business retries.
        """
        client = Mock(spec=LLMClient)
        cases = [None, {}, {"choices": []}, {"choices": [None]},
                 {"choices": [{"finish_reason": "stop", "message": None}]}]
        for field, value in (("finish_reason", "length"), ("finish_reason", "content_filter"),
                             ("refusal", "Refused"), ("content", None)):
            response = completion([A])
            choice = response["choices"][0]
            (choice if field == "finish_reason" else choice["message"])[field] = value
            cases.append(response)
        state = QuestionMemory("Original?", [A])
        for response in cases:
            client.reset_mock()
            client.complete.return_value = response
            with self.subTest(response=response), self.assertRaises(ValueError):
                update_memory(state, [], client, prompt_variant="retrieval_questions")
            client.complete.assert_called_once()
            self.assertEqual(state.memory_triplets, {A})

    def test_unselected_answers_preserve_state_without_retry(self) -> None:
        """Keep both old sets when an answer relies on discarded input evidence.

        Historical and potential entities are equally ineligible when their only
        supporting triple was omitted; invalid answers cannot publish selection.
        """
        state = QuestionMemory("Who?", [A], [REJECTED])
        client = Mock(spec=LLMClient)
        for response in (completion([A], [B[2]]), completion([B], [A[2]]),
                         completion([], [B[0]])):
            client.reset_mock()
            client.complete.return_value = response
            with self.subTest(response=response), self.assertRaises(ValueError):
                update_memory(state, [B], client, prompt_variant="retrieval_questions")
            client.complete.assert_called_once()
            self.assertEqual(state.memory_triplets, {A})
            self.assertEqual(state.rejected_triplets, {REJECTED})

    def test_tentative_ranking_uses_selected_cross_round_evidence(self) -> None:
        """Return model-ranked literal entities after retaining a multi-hop path.

        The paper prompt carries evidence-only answering and ranking instructions;
        orchestration preserves the ranking without alphabetical resorting.
        """
        state = QuestionMemory("Where was the leader born?", [A, B])
        client = Mock(spec=LLMClient)
        client.complete.return_value = completion([B, C], [C[2], B[2]])
        updated, result = update_memory(state, [C], client, prompt_variant="retrieval_questions")
        self.assertEqual(updated.memory_triplets, {B, C})
        self.assertEqual(result.predict_answer, (C[2], B[2]))
        self.assertEqual(state.memory_triplets, {A, B})
        prompt = client.complete.call_args.args[0][0]["content"]
        self.assertIn("Using only selected triples, predict the answer", prompt)
        self.assertIn("if multiple answers, rank by likelihood from triples", prompt)

    def test_transport_and_accounting_failures_preserve_state(self) -> None:
        """Propagate shared-client errors while keeping the caller's snapshot.

        Timeout, HTTP and accounting failures add no orchestration retries.
        """
        state = QuestionMemory("Original?", [A], [REJECTED])
        client = Mock(spec=LLMClient)
        for failure in (requests.Timeout(), requests.HTTPError(), OSError()):
            client.reset_mock()
            client.complete.side_effect = failure
            with self.subTest(failure=type(failure)), self.assertRaises(type(failure)):
                update_memory(state, [B], client, prompt_variant="retrieval_questions")
            client.complete.assert_called_once()
            self.assertEqual(state.memory_triplets, {A})
            self.assertEqual(state.rejected_triplets, {REJECTED})

    def test_question_runs_remain_isolated(self) -> None:
        """Update one run without sharing evidence with another identical query.

        Explicitly threading the returned state establishes round persistence.
        """
        first = QuestionMemory("Same question?")
        second = QuestionMemory("Same question?")
        client = Mock(spec=LLMClient)
        client.complete.return_value = completion([A], questions=["What evidence is missing?"])
        updated, _ = update_memory(first, [A, B], client, prompt_variant="retrieval_questions")
        self.assertEqual(updated.memory_triplets, {A})
        self.assertEqual(updated.rejected_triplets, {B})
        self.assertEqual(second.memory_triplets, set())
        self.assertEqual(first.memory_triplets, set())

    def test_real_client_retry_payload_and_usage(self) -> None:
        """Integrate memory selection with HTTP retries and private accounting.

        A timeout followed by success produces two usage rows and one update.
        """
        (ROOT / "tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="tmp_memory_update_") as name:
            usage_path = Path(name) / "tmp_usage.csv"
            config = LLMConfig(model_name="gpt-4o-mini", api_key="offline-test-key",
                               base_url="https://example.invalid/v1", timeout=60,
                               max_retries=1)
            client = LLMClient(config, usage_path)
            self.addCleanup(client.close)
            response = requests.Response()
            response.status_code = 200
            response._content = json.dumps(completion([A, B], [B[2]])).encode()
            state = QuestionMemory("Original?", [A])
            with patch("requests.adapters.HTTPAdapter.send") as send, patch(
                "src.agent.llm_client.time.sleep",
            ):
                send.side_effect = [requests.Timeout(), response]
                updated, result = update_memory(state, [B], client, prompt_variant="retrieval_questions")
                self.assertEqual(send.call_count, 2)
                payload = json.loads(send.call_args.args[0].body)
                self.assertEqual(payload["model"], "gpt-4o-mini")
                prompt = payload["messages"][0]["content"]
                self.assertEqual(json.loads(prompt.rsplit("Knowledge: ", 1)[1]),
                                 [list(A), list(B)])
            self.assertEqual(updated.memory_triplets, {A, B})
            self.assertEqual(result.predict_answer, (B[2],))
            with usage_path.open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual([row["status"] for row in rows], ["error", "success"])
            self.assertNotIn("Original?", usage_path.read_text(encoding="utf-8"))
