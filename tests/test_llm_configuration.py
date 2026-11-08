"""Verify LLM configuration and HTTP transmission without network access.

Run with python -m unittest discover -s tests -v from the repository root.
Temporary configuration fixtures live under the private project tmp directory.
"""

from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import requests

from src.agent.llm_client import LLMClient
from src.agent.llm_config import ROOT, load_llm_config, read_dotenv


class LLMConfigurationTests(unittest.TestCase):
    """Exercise precedence, validation, isolation and real client call arguments.

    All credentials and endpoints in fixtures are deliberately synthetic.
    The requests transport is mocked before any completion is attempted.
    """

    def setUp(self) -> None:
        """Create independent YAML and dotenv files for each test.

        Cleanup runs even when setup or an assertion subsequently fails.
        """
        (ROOT / "tmp").mkdir(exist_ok=True)
        directory = tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="tmp_llm_")
        self.addCleanup(directory.cleanup)
        self.yaml_path = Path(directory.name) / "config.yml"
        self.env_path = Path(directory.name) / ".env"
        self.usage_path = Path(directory.name) / "usage.csv"
        self.yaml_path.write_text(
            "llm:\n  provider: openai\n  model_name: gpt-4o-mini\n"
            "  base_url: https://yaml.invalid/v1\n  timeout: 60\n  max_retries: 2\n",
            encoding="utf-8",
        )
        self.env_path.write_text("LLM_API_KEY=offline-test-key\n", encoding="utf-8")

    def load(self):
        """Load the current test's private fixtures.

        Returns:
            Validated configuration or the loader's validation error.
        """
        return load_llm_config(self.yaml_path, self.env_path)

    def test_defaults_and_blank_overrides(self) -> None:
        """Keep YAML defaults and ignore legacy local request policy fields.

        The key remains local and is omitted from configuration repr.
        """
        with self.env_path.open("a", encoding="utf-8") as stream:
            stream.write("LLM_MODEL_NAME=\nLLM_BASE_URL=\nLLM_TIMEOUT=0\nLLM_MAX_RETRIES=-1\n")
        config = self.load()
        self.assertEqual((config.model_name, config.timeout, config.max_retries),
                         ("gpt-4o-mini", 60, 2))
        self.assertEqual(config.base_url, "https://yaml.invalid/v1")
        self.assertNotIn(config.api_key, repr(config))

    def test_overrides_and_process_environment_isolation(self) -> None:
        """Propagate file values despite conflicting process and SDK variables.

        The process mapping is restored by patch.dict, even on assertion failure.
        """
        self.yaml_path.write_text(
            self.yaml_path.read_text(encoding="utf-8")
            .replace("timeout: 60", "timeout: 1.5")
            .replace("max_retries: 2", "max_retries: 0"), encoding="utf-8",
        )
        self.env_path.write_text(
            "export LLM_API_KEY='offline-test-key' # local only\n"
            "LLM_MODEL_NAME=gpt-4o\nLLM_BASE_URL=https://local.invalid/v1/\n",
            encoding="utf-8",
        )
        conflicts = {key: "conflicting-value" for key in (
            "LLM_MODEL_NAME", "LLM_API_KEY", "LLM_BASE_URL", "LLM_TIMEOUT",
            "LLM_MAX_RETRIES", "OPENAI_API_KEY", "OPENAI_BASE_URL",
            "OPENAI_ORG_ID", "OPENAI_PROJECT_ID", "OPENAI_CUSTOM_HEADERS",
            "HTTP_PROXY", "HTTPS_PROXY", "REQUESTS_CA_BUNDLE",
        )}
        with patch.dict(os.environ, conflicts), patch("requests.Session") as factory:
            before = dict(os.environ)
            client = LLMClient(self.load(), self.usage_path)
            self.assertEqual(client.config.max_retries, 0)
            response = factory.return_value.post.return_value.__enter__.return_value
            response.status_code = 200
            response.is_redirect = False
            response.json.return_value = {"choices": [], "usage": {"total_tokens": 4}}
            messages = [{"role": "user", "content": "Offline test"}]
            self.assertEqual(client.complete(messages), response.json.return_value)
            self.assertFalse(client.session.trust_env)
            client.session.post.assert_called_once_with(
                "https://local.invalid/v1/chat/completions",
                headers={"Authorization": "Bearer offline-test-key"},
                json={"model": "gpt-4o", "messages": messages},
                timeout=1.5, allow_redirects=False,
            )
            client.close()
            client.session.close.assert_called_once()
            self.assertEqual(dict(os.environ), before)

    def test_missing_key_does_not_fall_back(self) -> None:
        """Reject absent local credentials even if YAML and the process hold keys.

        Missing dotenv files behave like empty files, never like OS lookups.
        """
        with self.yaml_path.open("a", encoding="utf-8") as stream:
            stream.write("  api_key: yaml-test-key\n")
        self.env_path.unlink()
        with patch.dict(os.environ, {"OPENAI_API_KEY": "process-test-key"}):
            with self.assertRaisesRegex(ValueError, "LLM_API_KEY"):
                self.load()

    def test_real_session_prepares_explicit_request(self) -> None:
        """Inspect the real HTTP session while replacing only its network adapter.

        Conflicting proxy and credential settings must not enter the prepared
        request or adapter options. No connection to the endpoint is opened.
        """
        client = LLMClient(self.load(), self.usage_path)
        self.addCleanup(client.close)
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"choices": []}'
        conflicts = {"HTTPS_PROXY": "http://proxy.invalid:9999",
                     "REQUESTS_CA_BUNDLE": "nonexistent-ca-file",
                     "NETRC": "nonexistent-netrc-file",
                     "OPENAI_API_KEY": "process-test-key"}
        with patch.dict(os.environ, conflicts), patch(
            "requests.adapters.HTTPAdapter.send", return_value=response,
        ) as send:
            self.assertEqual(client.complete([]), {"choices": []})
        prepared = send.call_args.args[0]
        self.assertEqual(prepared.url, "https://yaml.invalid/v1/chat/completions")
        self.assertEqual(prepared.headers["Authorization"], "Bearer offline-test-key")
        self.assertIn(b'"model": "gpt-4o-mini"', prepared.body)
        self.assertEqual(dict(send.call_args.kwargs["proxies"]), {})
        self.assertEqual(send.call_args.kwargs["timeout"], 60)
        self.assertIs(send.call_args.kwargs["verify"], True)

    def test_invalid_configuration(self) -> None:
        """Reject missing models, invalid numeric bounds and ambiguous endpoints.

        Numeric booleans, NaN, infinity and fractional retries are not accepted.
        """
        config = self.load()
        cases = {
            "model_name": (None, "", "unsupported"),
            "api_key": (None, "", " "),
            "timeout": (None, True, 0, -1, float("nan"), float("inf")),
            "max_retries": (None, True, -1, 1.5),
            "base_url": (None, "", "relative/path", "https://user:pass@host/v1",
                         "https://host/v1?key=value", "https://[", "https://host:bad"),
        }
        for key, values in cases.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    replace(config, **{key: value})
        original_yaml = self.yaml_path.read_text(encoding="utf-8")
        for field, old, value in (("max_retries", "2", "1.5"), ("timeout", "60", "abc")):
            self.yaml_path.write_text(
                original_yaml.replace(f"{field}: {old}", f"{field}: {value}"),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                self.load()
        self.yaml_path.write_text("llm: []\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.load()

    def test_dotenv_rejects_expansion_and_malformed_lines(self) -> None:
        """Fail on shell expansion and malformed syntax without leaking values.

        No assignment is executed or interpolated into another configuration field.
        """
        for line in ("LLM_API_KEY=${OPENAI_API_KEY}", "bad line", "A='unterminated",
                     "A=two words", "A=`command`"):
            self.env_path.write_text(line, encoding="utf-8")
            with self.subTest(line=line), self.assertRaises(ValueError):
                read_dotenv(self.env_path)

    @patch("src.agent.llm_client.time.sleep")
    def test_retry_budget_and_terminal_errors(self, sleep) -> None:
        """Bound extra attempts and avoid retrying permanent or malformed responses.

        Args:
            sleep: Patched backoff timer so retry tests run without delay.
        """
        client = LLMClient(replace(self.load(), max_retries=1), self.usage_path)
        self.addCleanup(client.close)
        response = MagicMock()
        response.__enter__.return_value = response
        response.status_code = 200
        response.is_redirect = False
        response.json.return_value = {"choices": []}
        with patch.object(client.session, "post") as post:
            post.side_effect = [requests.Timeout(), response]
            self.assertEqual(client.complete([]), {"choices": []})
            self.assertEqual(post.call_count, 2)
            for status in (429, 500):
                post.reset_mock(side_effect=True)
                response.status_code = status
                response.raise_for_status.side_effect = requests.HTTPError(response=response)
                post.return_value = response
                with self.assertRaises(requests.HTTPError):
                    client.complete([])
                self.assertEqual(post.call_count, 2)
            post.reset_mock(side_effect=True)
            response.status_code = 401
            post.return_value = response
            with self.assertRaises(requests.HTTPError):
                client.complete([])
            self.assertEqual(post.call_count, 1)
            post.reset_mock()
            response.status_code = 200
            response.raise_for_status.side_effect = None
            response.json.side_effect = ValueError("invalid JSON")
            with self.assertRaises(ValueError):
                client.complete([])
            self.assertEqual(post.call_count, 1)
        self.assertTrue(sleep.called)
