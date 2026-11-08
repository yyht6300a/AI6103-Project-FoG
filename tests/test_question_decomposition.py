"""Check Figure 7 decomposition and its integration with shared accounting.

All HTTP calls are mocked; no local credentials or paid services are used.
"""

from dataclasses import replace
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

from src.agent.llm_client import LLMClient
from src.agent.llm_config import LLMConfig, ROOT
from src.agent.question_decomposition import (
    build_decomposition_prompt,
    decompose_question,
    parse_sub_questions,
)


class QuestionDecompositionTests(unittest.TestCase):
    """Verify input, response and failure boundaries without network access.

    The HTTP integration case exercises the actual client and private CSV log.
    """

    def test_prompt_preserves_paper_instructions_and_input(self) -> None:
        """Retain Figure 7's constraints and interpolate arbitrary text once.

        Braces, Unicode, quotes and line breaks in questions remain literal.
        """
        question = 'Which country uses "Libya, Libya, Libya"?\n{question} 国家'
        prompt = build_decomposition_prompt(question)
        self.assertIn("standalone sub-questions", prompt)
        self.assertIn("Do not assume missing facts", prompt)
        self.assertIn("add clarification sub-questions only if needed", prompt)
        self.assertIn("Output only sub-questions, no explanations.", prompt)
        self.assertIn('["Sub question 1", ...]', prompt)
        self.assertTrue(prompt.endswith("Question: " + question))

    def test_invalid_question_prevents_requests(self) -> None:
        """Reject invalid user input before invoking the paid transport.

        The client remains untouched for every invalid input type and blank form.
        """
        client = Mock(spec=LLMClient)
        for question in (None, 1, [], "", " \n\t"):
            with self.subTest(question=question), self.assertRaises(ValueError):
                decompose_question(question, client)
        client.complete.assert_not_called()

    def test_valid_list_preserves_order_unicode_and_duplicates(self) -> None:
        """Accept JSON whitespace without rewriting or limiting sub-questions.

        Four entries guard against importing Figure 8's one-to-three limit.
        """
        questions = [" Which country? ", "谁是领导人？", "Which year?", "Which year?"]
        self.assertEqual(parse_sub_questions("\n" + json.dumps(questions) + "\t"),
                         questions)
        self.assertEqual(parse_sub_questions('["A single question?"]'),
                         ["A single question?"])

    def test_invalid_lists_are_not_repaired(self) -> None:
        """Reject malformed lists and nonquestion values with safe errors.

        Model output must not be included in exception messages or silently
        extracted from prose, Python literals or Markdown code fences.
        """
        cases = (None, 2, [], "", "[]", "{}", "null", '"Question?"',
                 '["Q?", 1]', '["Q?", null]', '[true]', '[["Q?"]]',
                 '[""]', '[" \\n"]', '["Q?",]', "['Q?']",
                 '```json\n["Q?"]\n```', 'Here: ["Q?"]', '["Q?"] trailing')
        for content in cases:
            with self.subTest(content=content), self.assertRaises(ValueError) as error:
                parse_sub_questions(content)
            self.assertNotIn("Q?", str(error.exception))

    def test_invalid_envelopes_and_incomplete_completions(self) -> None:
        """Fail closed on missing content, refusals and truncated responses.

        Even a syntactically valid list must not hide a length or safety stop.
        Application errors cause no additional API calls.
        """
        cases = (None, [], {}, {"choices": []}, {"choices": None},
                 {"choices": [None]}, {"choices": [{"message": {"content": '["Q?"]'}}]},
                 {"choices": [{"finish_reason": "stop", "message": None}]})
        client = Mock(spec=LLMClient)
        for response in cases:
            client.reset_mock()
            client.complete.return_value = response
            with self.subTest(response=response), self.assertRaises(ValueError):
                decompose_question("Original?", client)
            client.complete.assert_called_once()
        for reason, refusal, content in (("length", None, '["Q?"]'),
                                         ("content_filter", None, '["Q?"]'),
                                         ("stop", "Refused", '["Q?"]'),
                                         ("stop", None, None),
                                         ("stop", None, "not JSON")):
            client.reset_mock()
            client.complete.return_value = {"choices": [{
                "finish_reason": reason,
                "message": {"content": content, "refusal": refusal},
            }]}
            with self.subTest(reason=reason, content=content), self.assertRaises(ValueError):
                decompose_question("Original?", client)
            client.complete.assert_called_once()

    def test_transport_failure_propagates(self) -> None:
        """Preserve client failures without inventing sub-questions or retrying.

        Retry scheduling and per-attempt usage remain owned by LLMClient.
        """
        client = Mock(spec=LLMClient)
        for failure in (requests.Timeout(), requests.HTTPError(), OSError()):
            client.reset_mock()
            client.complete.side_effect = failure
            with self.subTest(failure=type(failure)), self.assertRaises(type(failure)):
                decompose_question("Original?", client)
            client.complete.assert_called_once()

    def test_real_client_payload_retries_and_accounting(self) -> None:
        """Exercise prompt, parsing, timeout retry and logging together.

        Successful HTTP responses remain successful accounting rows even when
        subsequent application validation rejects their content.
        """
        (ROOT / "tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="tmp_decomposition_") as name:
            usage_path = Path(name) / "tmp_usage.csv"
            config = LLMConfig(model_name="gpt-4o-mini", api_key="offline-test-key",
                               base_url="https://example.invalid/v1", timeout=60,
                               max_retries=1)
            client = LLMClient(config, usage_path)
            self.addCleanup(client.close)
            response = requests.Response()
            response.status_code = 200
            response._content = json.dumps({"choices": [{
                "finish_reason": "stop", "message": {"content": '["Country?", "Leader?"]'},
            }], "usage": {"prompt_tokens": 12, "completion_tokens": 8}}).encode()
            with patch("requests.adapters.HTTPAdapter.send") as send, patch(
                "src.agent.llm_client.time.sleep",
            ):
                send.side_effect = [requests.Timeout(), response]
                self.assertEqual(decompose_question("Original?", client), ["Country?", "Leader?"])
                self.assertEqual(send.call_count, 2)
                payload = json.loads(send.call_args.args[0].body)
                self.assertEqual(payload, {"model": "gpt-4o-mini", "messages": [{
                    "role": "user", "content": build_decomposition_prompt("Original?"),
                }]})
                client.config = replace(config, max_retries=0)
                response._content = b'{"choices":[{"finish_reason":"stop","message":{"content":"[]"}}]}'
                send.side_effect = None
                send.return_value = response
                with self.assertRaises(ValueError):
                    decompose_question("Original?", client)
            with usage_path.open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual([row["status"] for row in rows], ["error", "success", "success"])
            self.assertEqual(rows[0]["error_type"], "Timeout")
            self.assertEqual(rows[1]["input_tokens"], "12")
            self.assertNotIn("Original?", usage_path.read_text(encoding="utf-8"))
