"""Check the paid acceptance runner without contacting a provider.

The runner uses production public APIs and parsing; only configuration, report
location and HTTP transport are substituted with isolated synthetic fixtures.
"""

from contextlib import redirect_stdout
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

from scripts import validate_part_d as validation
from src.agent.llm_client import LLMClient
from src.agent.llm_config import LLMConfig, ROOT
from test_memory_update import completion


class PartDValidationTests(unittest.TestCase):
    """Verify call bounds, safe reporting and cleanup at the executable boundary.

    Reports and usage rows stay inside private project tmp directories. No test
    loads the user's dotenv or writes to the shared usage CSV.
    """

    def test_unknown_memory_variant_fails_before_a_paid_request(self) -> None:
        """Reject configuration mistakes without publishing or sending anything.

        The public scored-round entry point must validate a variant even when
        its evidence is empty, preserving the caller's original snapshot.
        """
        client = Mock(spec=LLMClient)
        state = validation.QuestionMemory(validation.QUESTION)
        before = validation.snapshot(state)
        with self.assertRaises(ValueError):
            validation.manage_memory(state, [], [], client, prompt_variant="unknown")
        client.complete.assert_not_called()
        self.assertEqual(validation.snapshot(state), before)

    def test_clarification_comparison_routes_variants_without_changing_evidence(self) -> None:
        """Compare identical evidence stages through the public MSM boundary.

        Mocked model content verifies prompt routing, immutable states and the
        six-call budget; it makes no claim about real clarification quality.
        """
        client = Mock(spec=LLMClient)
        client.complete.side_effect = [
            envelope for _ in range(2) for envelope in (
                completion([], [], ["Who wrote The Glass Orchard?"]),
                completion([validation.AUTHOR], [], ["Where was Mira Vale born?"]),
                completion([validation.AUTHOR, validation.BIRTH], ["Linden City"], []),
            )
        ]
        report = {"plan": validation.case_plan("clarification_diagnostic"), "results": []}
        validation.run_cases(client, report, "clarification_diagnostic")
        self.assertEqual(client.complete.call_count, 6)
        records = report["results"]
        for index, record in enumerate(records):
            prompt = client.complete.call_args_list[index].args[0][0]["content"]
            self.assertEqual("### Retrieval Questions" in prompt, index >= 3)
            self.assertTrue(record["old_state_unchanged"])
            self.assertTrue(record["state_rules_match"])
        self.assertEqual([r["submitted"] for r in records[:3]],
                         [r["submitted"] for r in records[3:]])

    def test_reference_comparison_records_actual_queries_and_author_evidence(self) -> None:
        """Keep the comparison bounded and expose the actual MSM handoff.

        Synthetic completions verify orchestration, not semantic quality or
        retrieval recall. Both decomposition variants use the public parser.
        """
        client = Mock(spec=LLMClient)
        envelopes = []
        for questions in (["Who wrote it?"], ["Who wrote The Glass Orchard?"]):
            envelope = completion([], [], [])
            envelope["choices"][0]["message"]["content"] = json.dumps(questions)
            envelopes.append(envelope)
        envelopes.append(completion([validation.AUTHOR], [], ["Where was Mira Vale born?"]))
        client.complete.side_effect = envelopes
        report = {"plan": validation.case_plan("reference_diagnostic"), "results": []}
        validation.run_cases(client, report, "reference_diagnostic")
        self.assertEqual(client.complete.call_count, 3)
        records = report["results"]
        self.assertEqual([item["prompt_variant"] for item in records[:2]],
                         ["paper", "explicit_references"])
        self.assertEqual(records[2]["queries"], records[1]["sub_questions"])
        self.assertEqual(records[2]["submitted"], [validation.AUTHOR])
        self.assertEqual(records[2]["subsequent_question"], ("Where was Mira Vale born?",))
        self.assertTrue(records[2]["state_rules_match"])

    def response(self, body: dict) -> requests.Response:
        """Wrap a synthetic envelope in a real requests response.

        Args:
            body: JSON object passed through real HTTP and business decoders.

        Returns:
            Successful buffered HTTP response with synthetic request metadata.
        """
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(body).encode("utf-8")
        response.headers["x-request-id"] = "req_acceptance_offline"
        return response

    def test_preview_never_loads_credentials_or_constructs_client(self) -> None:
        """Expose fixed case counts and expectations without a paid call.

        Each suite is previewed independently so operators can select a smaller
        run before supplying private configuration.
        """
        for suite, count in (("single", 4), ("multi", 4), ("removal", 3), ("ranking", 2), ("empty_diagnostic", 1), ("reference_diagnostic", 3), ("clarification_diagnostic", 6), ("all", 13)):
            with self.subTest(suite=suite), patch.object(validation, "load_llm_config") as load, patch.object(
                validation, "LLMClient",
            ) as client, redirect_stdout(io.StringIO()) as output:
                self.assertEqual(validation.main(["--list-cases", "--suite", suite]), 0)
                plan = json.loads(output.getvalue())
                self.assertEqual(plan["logical_call_limit"], count + (2 if suite in ("ranking", "all") else 0))
                self.assertEqual(len(plan["plan"]), count)
                load.assert_not_called()
                client.assert_not_called()

    def test_all_cases_use_public_parsers_and_keep_semantic_review_pending(self) -> None:
        """Exercise thirteen cases and fifteen requests with real HTTP accounting.

        The model keeps the useful path and rejects a distractor. Absence of old
        memory removal is recorded as unobserved rather than fabricated success.
        """
        author, birth, distractor = validation.AUTHOR, validation.BIRTH, validation.DISTRACTOR
        decomp = completion([], [], [])
        decomp["choices"][0]["message"]["content"] = json.dumps(["Who wrote The Glass Orchard?"])
        envelopes = [
            decomp,
            completion([author, birth], [birth[2]], []),
            completion([author], [], ["Where was Mira Vale born?"]),
            completion([], [], [validation.QUESTION]),
            completion([author], [], ["Where was Mira Vale born?"]),
            completion([author, birth], [birth[2]], []),
            completion([author, birth], [birth[2]], []),
            completion([distractor], [distractor[2]], []),
            completion([birth], [], ["Who published The Glass Orchard?"]),
            completion([validation.PUBLISHER, validation.OTHER_BIRTH], ["Cedar City"], []),
            completion([validation.PUBLISHER, validation.OTHER_BIRTH], ["Cedar City"], []),
            completion([birth, validation.OTHER_BIRTH], ["Cedar City", "Linden City"], []),
            completion([], [], []),
            completion([validation.CONFIRMED_BIRTH, validation.POSSIBLE_BIRTH],
                       ["Linden City", "Cedar City"], []),
            completion([], [], []),
        ]
        for index, answers in ((12, ["Cedar City", "Linden City"]),
                               (14, ["Linden City", "Cedar City"])):
            envelopes[index]["choices"][0]["message"]["content"] = json.dumps({"predict_answer": answers})
        (ROOT / "tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="tmp_acceptance_") as directory:
            root = Path(directory)
            config = LLMConfig("gpt-4o-mini", "offline-secret", "https://test.invalid/v1", 2, 2)
            client = LLMClient(config, root / "tmp_usage.csv")
            with patch.object(validation, "ROOT", root), patch.object(
                validation, "load_llm_config", return_value=config,
            ), patch.object(validation, "LLMClient", return_value=client), patch(
                "requests.adapters.HTTPAdapter.send",
                side_effect=[self.response(body) for body in envelopes],
            ) as send, patch.object(client, "close", wraps=client.close) as close, redirect_stdout(io.StringIO()):
                self.assertEqual(validation.main([]), 0)
                self.assertEqual(send.call_count, 15)
                close.assert_called_once()
            report = json.loads(next((root / "tmp").glob("tmp_*.json")).read_text(encoding="utf-8"))
            self.assertEqual(report["http_attempt_limit"], 45)
            self.assertEqual(report["logical_calls_started"], 15)
            self.assertTrue(all(record["ranking_stage_enabled"] for record in report["results"][-2:]))
            self.assertEqual(report["semantic_review"], "pending")
            self.assertFalse(report["multi_observations"]["old_memory_removal"])
            self.assertTrue(report["multi_observations"]["overlapping_rejection_isolation"])
            self.assertEqual(report["results"][5]["queries"], ["Where was Mira Vale born?"])
            self.assertEqual(report["results"][9]["queries"], ["Who published The Glass Orchard?"])
            self.assertTrue(all(report["removal_observations"].values()))
            self.assertEqual(report["results"][9]["removed_memory"], [list(birth)])
            self.assertNotIn(list(birth), report["results"][10]["submitted"])
            self.assertEqual(report["results"][11]["predict_answer"], ["Cedar City", "Linden City"])
            self.assertEqual(report["results"][12]["predict_answer"], ["Linden City", "Cedar City"])
            self.assertEqual(report["results"][12]["state_before"]["memory"], [])
            self.assertEqual(report["results"][12]["state_before"]["question"],
                             validation.RANKING_QUESTION)
            self.assertEqual(report["results"][12]["newly_rejected"], [list(validation.RESIDENCE)])
            for record in report["results"][1:]:
                self.assertTrue(record["old_state_unchanged"])
                self.assertTrue(record["state_rules_match"])
            self.assertNotIn(config.api_key, json.dumps(report))
            with (root / "tmp_usage.csv").open(encoding="utf-8", newline="") as stream:
                self.assertEqual(len(list(csv.DictReader(stream))), 15)

    def test_failure_stops_suite_closes_client_and_preserves_previous_state(self) -> None:
        """Stop on malformed content or exhausted timeout without leaking detail.

        A successful partial round precedes the failure, so the report must retain
        real nonempty prior state. Only transport errors spend the retry budget.
        """
        for timeout in (False, True):
            with self.subTest(timeout=timeout):
                (ROOT / "tmp").mkdir(exist_ok=True)
                with tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="tmp_acceptance_failure_") as directory:
                    root = Path(directory)
                    config = LLMConfig("gpt-4o-mini", "offline-secret", "https://test.invalid/v1", 2, 2)
                    client = LLMClient(config, root / "tmp_usage.csv")
                    first = self.response(completion([validation.AUTHOR], [], ["Where was Mira Vale born?"]))
                    bad = completion([], [], [])
                    bad["choices"][0]["message"]["content"] = "sensitive-failed-content"
                    failures = ([requests.Timeout("sensitive-exception-detail")] * 3
                                if timeout else [self.response(bad)])
                    with patch.object(validation, "ROOT", root), patch.object(
                        validation, "load_llm_config", return_value=config,
                    ), patch.object(validation, "LLMClient", return_value=client), patch(
                        "requests.adapters.HTTPAdapter.send", side_effect=[first] + failures,
                    ) as send, patch("src.agent.llm_client.time.sleep"), patch.object(
                        client, "close", wraps=client.close,
                    ) as close, redirect_stdout(io.StringIO()) as output:
                        self.assertEqual(validation.main(["--suite", "multi"]), 1)
                        self.assertEqual(send.call_count, 4 if timeout else 2)
                        close.assert_called_once()
                    report_text = next((root / "tmp").glob("tmp_*.json")).read_text(encoding="utf-8")
                    report = json.loads(report_text)
                    failed = report["results"][-1]
                    self.assertEqual(report["logical_calls_started"], 2)
                    self.assertEqual(failed["case"], "multi.recovery")
                    self.assertTrue(failed["old_state_unchanged"])
                    self.assertEqual(failed["state_before"], failed["state_after"])
                    self.assertEqual(failed["state_before"]["memory"], [list(validation.AUTHOR)])
                    for private in (config.api_key, "sensitive-failed-content", "sensitive-exception-detail"):
                        self.assertNotIn(private, report_text + output.getvalue())
