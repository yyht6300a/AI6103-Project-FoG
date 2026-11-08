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
from unittest.mock import patch

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
        for suite, count in (("single", 4), ("multi", 4), ("removal", 3), ("ranking", 2), ("empty_diagnostic", 1), ("all", 13)):
            with self.subTest(suite=suite), patch.object(validation, "load_llm_config") as load, patch.object(
                validation, "LLMClient",
            ) as client, redirect_stdout(io.StringIO()) as output:
                self.assertEqual(validation.main(["--list-cases", "--suite", suite]), 0)
                plan = json.loads(output.getvalue())
                self.assertEqual(plan["logical_call_limit"], count)
                self.assertEqual(len(plan["plan"]), count)
                load.assert_not_called()
                client.assert_not_called()

    def test_all_cases_use_public_parsers_and_keep_semantic_review_pending(self) -> None:
        """Exercise thirteen calls with independent state and true HTTP accounting.

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
            completion([validation.CONFIRMED_BIRTH, validation.POSSIBLE_BIRTH],
                       ["Linden City", "Cedar City"], []),
        ]
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
                self.assertEqual(send.call_count, 13)
                close.assert_called_once()
            report = json.loads(next((root / "tmp").glob("tmp_*.json")).read_text(encoding="utf-8"))
            self.assertEqual(report["http_attempt_limit"], 39)
            self.assertEqual(report["logical_calls_started"], 13)
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
                self.assertEqual(len(list(csv.DictReader(stream))), 13)

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
