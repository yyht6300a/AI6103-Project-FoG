"""Verify per-attempt accounting across real response objects and retries.

All network calls use controlled fixtures and all CSV files remain under tmp.
"""

import csv
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import requests

from src.agent.llm_client import LLMClient
from src.agent.llm_config import ROOT, LLMConfig
from src.agent.llm_usage import UsageLog


class LLMUsageTests(unittest.TestCase):
    """Check public metadata, error outcomes and accounting failures.

    Each test owns its CSV and a client whose HTTP adapter never reaches a server.
    """

    def setUp(self) -> None:
        """Create a private accounting destination and a synthetic client.

        Temporary files and session resources are released after each test.
        """
        (ROOT / "tmp").mkdir(exist_ok=True)
        directory = tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="tmp_usage_")
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "usage.csv"
        self.client = LLMClient(
            LLMConfig("gpt-4o-mini", "secret-test-key", "https://test.invalid/v1", 2, 1),
            self.path,
        )
        self.addCleanup(self.client.close)

    def response(self, status=200, body=None, request_id="req_test"):
        """Build a real requests response with a service header and JSON body.

        Args:
            status: HTTP status used by raise_for_status.
            body: JSON value, or malformed bytes to exercise decoding failures.
            request_id: Optional server request ID, unrelated to completion ID.

        Returns:
            In-memory response ready for a mocked post call.
        """
        response = requests.Response()
        response.status_code = status
        response._content = body if isinstance(body, bytes) else json.dumps(body).encode()
        if status == 302:
            response.headers["Location"] = "https://redirect.invalid"
        if request_id:
            response.headers["x-request-id"] = request_id
        return response

    def rows(self):
        """Read the complete audit trail after calls finish.

        Returns:
            Ordered CSV records with unknown values represented by empty strings.
        """
        with self.path.open(encoding="utf-8", newline="") as stream:
            return list(csv.DictReader(stream))

    @patch("src.agent.llm_client.time.sleep")
    def test_retries_and_success_are_separate_rows(self, sleep) -> None:
        """Keep timeout, rate limit and successful usage as independent attempts.

        Args:
            sleep: Patched delay to avoid waiting between synthetic attempts.
        """
        success = self.response(body={
            "model": "gpt-4o-mini-2024-07-18", "id": "completion-not-request-id",
            "usage": {"prompt_tokens": 9, "completion_tokens": 0},
            "choices": [{"message": {"content": "private response"}}],
        })
        with patch.object(self.client.session, "post") as post:
            post.side_effect = [requests.Timeout("secret-test-key"), success]
            self.client.complete([{"role": "user", "content": "private question"}])
            post.side_effect = [self.response(429, {"error": "private detail"}), success]
            self.client.complete([])
        rows = self.rows()
        self.assertEqual([row["status"] for row in rows],
                         ["error", "success", "error", "success"])
        self.assertEqual([row["error_type"] for row in rows],
                         ["Timeout", "", "HTTPError", ""])
        self.assertEqual(rows[0]["input_tokens"], "")
        self.assertEqual(rows[0]["request_id"], "")
        self.assertEqual(rows[1]["input_tokens"], "9")
        self.assertEqual(rows[1]["output_tokens"], "0")
        self.assertEqual(rows[1]["model"], "gpt-4o-mini-2024-07-18")
        self.assertEqual(rows[1]["request_id"], "req_test")
        for row in rows:
            self.assertIsNotNone(datetime.fromisoformat(row["timestamp"]).tzinfo)
        text = self.path.read_text(encoding="utf-8")
        for private in ("secret-test-key", "private", "completion-not-request-id"):
            self.assertNotIn(private, text)
        self.assertEqual(text.count("timestamp,model,"), 1)
        self.assertEqual(sleep.call_count, 2)

    @patch("src.agent.llm_client.time.sleep")
    def test_terminal_failures_and_missing_usage(self, sleep) -> None:
        """Log every terminal failure and leave unknown or invalid usage blank.

        Args:
            sleep: Patched retry delay used for exhausted server errors.
        """
        cases = [
            ([self.response(503, b"bad"), self.response(503, b"bad")], requests.HTTPError, 2),
            ([requests.ConnectionError(), requests.ConnectionError()], requests.ConnectionError, 2),
            ([self.response(401, {"error": "unauthorized"})], requests.HTTPError, 1),
            ([self.response(200, b"bad")], ValueError, 1),
            ([self.response(200, [])], ValueError, 1),
            ([self.response(302, {})], requests.HTTPError, 1),
        ]
        for responses, error, count in cases:
            before = len(self.rows())
            with self.subTest(error=error, count=count), patch.object(
                self.client.session, "post", side_effect=responses,
            ) as post:
                with self.assertRaises(error):
                    self.client.complete([])
                self.assertEqual(post.call_count, count)
                added = self.rows()[before:]
                self.assertEqual(len(added), count)
                self.assertTrue(all(row["status"] == "error" for row in added))
        with patch.object(self.client.session, "post", return_value=self.response(body={
            "usage": {"prompt_tokens": True, "completion_tokens": -1},
        }, request_id="")):
            self.client.complete([])
        self.assertEqual(self.rows()[-1]["input_tokens"], "")
        self.assertEqual(self.rows()[-1]["output_tokens"], "")
        self.assertEqual(self.rows()[-1]["request_id"], "")

    def test_log_initialization_and_append_failures(self) -> None:
        """Reject incompatible files and surface append failures without retrying.

        Logging failure must never silently trigger another paid request.
        """
        self.path.write_text("wrong,header\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            UsageLog(self.path)
        with patch.object(self.client.session, "post", return_value=self.response(body={})), patch.object(
            self.client.usage_log, "record", side_effect=OSError("unwritable"),
        ) as record:
            with self.assertRaises(OSError):
                self.client.complete([])
        record.assert_called_once()
