"""Verify atomic MSM failures through real HTTP decoding and accounting.

Only the network adapter and backoff clock are replaced. Synthetic responses
exercise the public scored-round entry point without contacting an API service.
"""

import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import call, patch

import requests

from src.agent.llm_client import LLMClient
from src.agent.llm_config import ROOT, LLMConfig
from src.agent.memory_management import manage_memory
from src.agent.memory_prompt import build_memory_prompt
from src.agent.memory_state import QuestionMemory
from test_memory_update import A, B, C, REJECTED, completion


class MemoryFailureTests(unittest.TestCase):
    """Check retry limits, output rejection and safe subsequent recovery.

    Each scenario starts with nonempty memory and permanent rejections, making
    accidental clearing or partial updates visible at the public API boundary.
    """

    def setUp(self) -> None:
        """Create an isolated client and immutable question snapshot.

        All credentials are synthetic; accounting remains in the private tmp
        directory and cleanup closes the client before removing its files.
        """
        (ROOT / "tmp").mkdir(exist_ok=True)
        directory = tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="tmp_msm_failures_")
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "tmp_usage.csv"
        self.client = LLMClient(
            LLMConfig("gpt-4o-mini", "offline-test-key", "https://test.invalid/v1", 2, 2),
            self.path,
        )
        self.addCleanup(self.client.close)
        self.state = QuestionMemory("Private original question?", [A], [REJECTED])

    def response(self, body: object, status: int = 200) -> requests.Response:
        """Build a real response so JSON decoding and HTTP errors remain active.

        Args:
            body: JSON-serializable envelope or raw malformed response bytes.
            status: HTTP code interpreted by requests.raise_for_status.

        Returns:
            Buffered response with a synthetic service request identifier.
        """
        response = requests.Response()
        response.status_code = status
        response._content = body if isinstance(body, bytes) else json.dumps(body).encode()
        response.headers["x-request-id"] = "req_offline"
        return response

    def rows(self) -> list[dict[str, str]]:
        """Read completed attempts from the scenario's private CSV.

        Returns:
            Ordered metadata rows, with unknown token counts left blank.
        """
        with self.path.open(encoding="utf-8", newline="") as stream:
            return list(csv.DictReader(stream))

    def run_round(self):
        """Submit one scored round without publishing its returned state.

        Returns:
            Validated MSMResult, or propagates the production failure unchanged.
            B is eligible and C is threshold-hidden; old evidence stays separate.
        """
        return manage_memory(self.state, [B, C, REJECTED], [0.9, 0.7, 1.0], self.client)

    def assert_original_state(self) -> None:
        """Check all persistent fields after a failure or independent success.

        Failed selections must neither reject submitted B nor blacklist hidden C.
        """
        self.assertEqual(self.state.original_question, "Private original question?")
        self.assertEqual(self.state.memory_triplets, {A})
        self.assertEqual(self.state.rejected_triplets, {REJECTED})
        self.assertEqual(self.state.excluded_triplets, {A, REJECTED})
        self.assertEqual(self.state.filter_expansion_triplets([A, B, C, REJECTED]), [B, C])

    def test_timeout_and_rate_limit_exhaustion_preserve_state(self) -> None:
        """Stop after the configured budget and account for every failed attempt.

        Both zero extra retries and the normal two-retry budget are covered;
        rate-limit response objects retain their real HTTP status semantics.
        """
        for retries in (0, 2):
            for rate_limited in (False, True):
                with self.subTest(retries=retries, rate_limited=rate_limited):
                    client = LLMClient(
                        LLMConfig("gpt-4o-mini", "offline-test-key",
                                  "https://test.invalid/v1", 2, retries), self.path,
                    )
                    self.addCleanup(client.close)
                    error_type = requests.HTTPError if rate_limited else requests.Timeout
                    attempts = [self.response({"error": "private detail"}, 429)
                                if rate_limited else requests.Timeout("private detail")
                                for _ in range(retries + 1)]
                    before = len(self.rows())
                    with patch("requests.adapters.HTTPAdapter.send", side_effect=attempts) as send, patch(
                        "src.agent.llm_client.time.sleep",
                    ) as sleep:
                        with self.assertRaises(error_type) as caught:
                            manage_memory(self.state, [B, C], [0.9, 0.7], client)
                        self.assertEqual(send.call_count, retries + 1)
                        self.assertEqual(sleep.call_args_list, [call(2 ** i) for i in range(retries)])
                    if rate_limited:
                        self.assertEqual(caught.exception.response.status_code, 429)
                    self.assert_original_state()
                    rows = self.rows()[before:]
                    self.assertEqual(len(rows), retries + 1)
                    self.assertEqual([row["status"] for row in rows], ["error"] * len(rows))
                    self.assertEqual([row["error_type"] for row in rows],
                                     [error_type.__name__] * len(rows))
                    for row in rows:
                        self.assertEqual((row["input_tokens"], row["output_tokens"]), ("", ""))
                        self.assertEqual(row["request_id"], "req_offline" if rate_limited else "")

    def test_transient_failures_retry_identical_evidence_then_commit_once(self) -> None:
        """Recover from timeout and rate limiting within one round's budget.

        Every attempt must carry the same original evidence, with no transient
        rejection; only the final valid selection produces a new snapshot.
        """
        body = completion([B], [B[2]])
        body["usage"] = {"prompt_tokens": 19, "completion_tokens": 7}
        attempts = [requests.Timeout(), self.response({}, 429), self.response(body)]
        with patch("requests.adapters.HTTPAdapter.send", side_effect=attempts) as send, patch(
            "src.agent.llm_client.time.sleep",
        ) as sleep:
            result = self.run_round()
        expected = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content":
                    build_memory_prompt(self.state.original_question, [A], [B])}]}
        self.assertEqual(send.call_count, 3)
        for attempt in send.call_args_list:
            self.assertEqual(json.loads(attempt.args[0].body), expected)
            self.assertEqual(attempt.kwargs["timeout"], 2)
        self.assertEqual(sleep.call_args_list, [call(1), call(2)])
        self.assertEqual(result.state.memory_triplets, {B})
        self.assertEqual(result.state.rejected_triplets, {A, REJECTED})
        self.assertNotIn(C, result.state.excluded_triplets)
        self.assertEqual(result.response.predict_answer, (B[2],))
        self.assert_original_state()
        rows = self.rows()
        self.assertEqual([row["status"] for row in rows], ["error", "error", "success"])
        self.assertEqual([row["error_type"] for row in rows], ["Timeout", "HTTPError", ""])
        self.assertEqual((rows[-1]["input_tokens"], rows[-1]["output_tokens"]), ("19", "7"))
        for private in (self.client.config.api_key, self.state.original_question, B[2]):
            self.assertNotIn(private, self.path.read_text(encoding="utf-8"))

    def test_invalid_json_and_out_of_range_output_do_not_retry_or_publish(self) -> None:
        """Reject malformed HTTP JSON and invalid successful completion content.

        Valid HTTP envelopes retain success accounting even when their model
        output fails business validation; a later explicit call can still succeed.
        """
        bad_json = completion([B], [B[2]])
        bad_json["choices"][0]["message"]["content"] = "not JSON"
        cases = (
            (b"not JSON", "error"),
            ([], "error"),
            ({"choices": []}, "success"),
            (bad_json, "success"),
            (completion([("Invented", "relation", "Outside")], ["Outside"]), "success"),
            (completion([C], [C[2]]), "success"),  # Hidden input is not submitted.
            (completion([REJECTED], [REJECTED[2]]), "success"),
            (completion([B], ["Outside entity"]), "success"),
            (completion([B], [A[2]]), "success"),  # Unselected history is not answer evidence.
        )
        for body, status in cases:
            with self.subTest(body=body):
                before = len(self.rows())
                with patch("requests.adapters.HTTPAdapter.send", return_value=self.response(body)) as send, patch(
                    "src.agent.llm_client.time.sleep",
                ) as sleep:
                    with self.assertRaises(ValueError):
                        self.run_round()
                    send.assert_called_once()
                    sleep.assert_not_called()
                self.assert_original_state()
                rows = self.rows()[before:]
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["status"], status)
                self.assertEqual(bool(rows[0]["error_type"]), status == "error")
        with patch("requests.adapters.HTTPAdapter.send", return_value=self.response(completion([A, B], [B[2]]))):
            result = self.run_round()
        self.assertEqual(result.state.memory_triplets, {A, B})
        self.assertEqual(result.state.rejected_triplets, {REJECTED})
        self.assert_original_state()

    def test_failed_round_after_success_preserves_state_until_explicit_recovery(self) -> None:
        """Keep a live caller snapshot across failed rounds and later recovery.

        Each scenario first establishes memory and rejection through the public
        entry point. Failed assignment must preserve that exact snapshot; a
        subsequent explicit call can select the failed round's candidate and
        newly eligible threshold-hidden evidence without reviving rejections.
        """
        invalid_content = completion([B], [B[2]])
        invalid_content["choices"][0]["message"]["content"] = "{invalid JSON"
        scenarios = (
            ("timeout", [requests.Timeout("private failure")] * 3,
             requests.Timeout, ["error"] * 3, ["Timeout"] * 3),
            ("rate_limit", [self.response({}, 429) for _ in range(3)],
             requests.HTTPError, ["error"] * 3, ["HTTPError"] * 3),
            ("outer_json", [self.response(b"{invalid JSON")],
             ValueError, ["error"], ["JSONDecodeError"]),
            ("content_json", [self.response(invalid_content)],
             ValueError, ["success"], [""]),
            ("unknown_triple", [self.response(completion(
                [("Invented", "relation", "Outside")], ["Outside"],
            ))], ValueError, ["success"], [""]),
            ("unknown_answer", [self.response(completion([B], ["Outside entity"]))],
             ValueError, ["success"], [""]),
        )
        for label, failures, exception, statuses, errors in scenarios:
            with self.subTest(failure=label):
                before = len(self.rows())
                initial = QuestionMemory("Private original question?")
                state = initial
                ranked = completion()
                ranked["choices"][0]["message"]["content"] = json.dumps({"predict_answer": [C[2], B[2]]})
                attempts = [self.response(completion([A], [A[2]])), *failures,
                            self.response(completion([C, B], [C[2], B[2]])), self.response(ranked)]
                with patch("requests.adapters.HTTPAdapter.send", side_effect=attempts) as send, patch(
                    "src.agent.llm_client.time.sleep",
                ) as sleep:
                    state = manage_memory(
                        state, [A, REJECTED, C], [0.9, 0.9, 0.7], self.client,
                    ).state
                    established = state
                    self.assertEqual(state.memory_triplets, {A})
                    self.assertEqual(state.rejected_triplets, {REJECTED})

                    # Mirror E's assignment: an exception must leave the caller
                    # holding the snapshot from the last successful round.
                    with self.assertRaises(exception):
                        state = manage_memory(
                            state, [B, C, REJECTED], [0.9, 0.7, 1.0], self.client,
                        ).state
                    self.assertIs(state, established)
                    self.assertEqual(state.original_question, initial.original_question)
                    self.assertEqual(state.memory_triplets, {A})
                    self.assertEqual(state.rejected_triplets, {REJECTED})
                    self.assertEqual(state.excluded_triplets, {A, REJECTED})
                    self.assertEqual(state.filter_expansion_triplets([A, B, C, REJECTED]), [B, C])
                    self.assertEqual(send.call_count, 1 + len(failures))
                    self.assertEqual(sleep.call_args_list,
                                     [call(1), call(2)] if len(failures) == 3 else [])

                    recovered = manage_memory(
                        state, [B, C, REJECTED], [0.9, 0.9, 1.0], self.client,
                    )
                    state = recovered.state
                    self.assertEqual(send.call_count, len(attempts))
                    unions = [{A, REJECTED}] + [{A, B}] * len(failures) + [{A, B, C}]
                    for attempt, submitted in zip(send.call_args_list[:-1], unions):
                        payload = json.loads(attempt.args[0].body)
                        self.assertEqual(payload, {"model": "gpt-4o-mini", "messages": [
                            {"role": "user", "content": build_memory_prompt(
                                initial.original_question, submitted, [],
                            )},
                        ]})
                        self.assertEqual(attempt.kwargs["timeout"], 2)

                self.assertIsNot(state, established)
                self.assertEqual(state.original_question, initial.original_question)
                self.assertEqual(state.memory_triplets, {B, C})
                self.assertEqual(state.rejected_triplets, {A, REJECTED})
                self.assertEqual(state.excluded_triplets, {A, B, C, REJECTED})
                self.assertEqual(state.filter_expansion_triplets([A, B, C, REJECTED]), [])
                self.assertEqual(recovered.response.selected_triplets, (C, B))
                self.assertEqual(recovered.response.predict_answer, (C[2], B[2]))
                self.assertEqual(recovered.response.subsequent_question, ())
                self.assertEqual(established.memory_triplets, {A})
                self.assertEqual(established.rejected_triplets, {REJECTED})
                self.assertEqual(established.excluded_triplets, {A, REJECTED})
                self.assertEqual(initial.memory_triplets, frozenset())
                self.assertEqual(initial.rejected_triplets, frozenset())
                rows = self.rows()[before:]
                self.assertEqual(len(rows), len(attempts))
                self.assertEqual([row["status"] for row in rows], ["success", *statuses, "success", "success"])
                self.assertEqual([row["error_type"] for row in rows], ["", *errors, "", ""])
                for row in rows:
                    self.assertEqual((row["input_tokens"], row["output_tokens"]), ("", ""))
                for private in (self.client.config.api_key, initial.original_question,
                                "private failure", "Outside entity", A[2], B[2], C[2]):
                    self.assertNotIn(private, self.path.read_text(encoding="utf-8"))
