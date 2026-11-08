"""Send explicit OpenAI-compatible Chat Completions requests.

The transport disables environment-derived proxies, credentials and CA settings.
No vendor SDK or process environment fallback participates in configuration.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
import time

import requests

from src.agent.llm_config import LLMConfig
from src.agent.llm_usage import USAGE_PATH, UsageLog


class LLMClient:
    """Own a synchronous HTTP session for one validated LLM configuration.

    Call close after use. Each completion permits at most max_retries additional
    attempts for timeouts, connection failures, HTTP 429 and HTTP 5xx responses.
    Other HTTP errors and invalid JSON propagate without retrying.
    """

    def __init__(self, config: LLMConfig, usage_path: Path = USAGE_PATH) -> None:
        """Create a session without making a network request.

        Args:
            config: Validated model, endpoint, credentials and attempt limits.
            usage_path: Shared accounting CSV; override only for isolated tests.
        """
        self.config = config
        self.usage_log = UsageLog(usage_path)
        self.session = requests.Session()
        self.session.trust_env = False

    def complete(self, messages: Sequence[Mapping[str, str]]) -> dict:
        """Submit messages and return the service's decoded response.

        Args:
            messages: Chat messages containing role and content fields.

        Returns:
            Unmodified response object, including any returned usage metadata.

        Raises:
            requests.RequestException: A terminal HTTP or transport failure occurs.
            ValueError: The response is not a JSON object.
        """
        for attempt in range(self.config.max_retries + 1):
            try:
                return self._attempt(messages)
            except (requests.Timeout, requests.ConnectionError):
                if attempt == self.config.max_retries:
                    raise
            except requests.HTTPError as error:
                status = error.response.status_code if error.response is not None else 0
                if attempt == self.config.max_retries or not (status == 429 or status >= 500):
                    raise
            time.sleep(min(2 ** attempt, 8))
        raise RuntimeError("LLM retry loop exhausted unexpectedly")

    def _attempt(self, messages: Sequence[Mapping[str, str]]) -> dict:
        """Send one HTTP request and account for its outcome before returning.

        Args:
            messages: Explicit chat payload supplied by the caller.

        Returns:
            Decoded JSON object on HTTP success.

        Raises:
            requests.RequestException: Transport or HTTP failure.
            ValueError: Successful response is not a JSON object.
            OSError: Accounting cannot be persisted; no automatic retry follows.
        """
        payload = {"model": self.config.model_name, "messages": list(messages)}
        timestamp = datetime.now(timezone.utc).isoformat()
        result = None
        request_id = ""
        error_type = ""
        try:
            with self.session.post(
                self.config.base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + self.config.api_key},
                json=payload, timeout=self.config.timeout, allow_redirects=False,
            ) as response:
                request_id = response.headers.get("x-request-id", "")
                # Parse even error bodies for returned usage, but preserve HTTP errors.
                try:
                    result = response.json()
                except ValueError:
                    response.raise_for_status()
                    raise
                response.raise_for_status()
                if response.is_redirect:
                    raise requests.HTTPError(
                        "LLM endpoint redirected the request", response=response,
                    )
                if not isinstance(result, dict):
                    raise ValueError("LLM response must be a JSON object")
                return result
        except Exception as error:
            # Only the class is public; error text can contain credentials or payloads.
            error_type = type(error).__name__
            raise
        finally:
            self.usage_log.record(
                timestamp, self.config.model_name, result, request_id, error_type,
            )

    def close(self) -> None:
        """Release pooled HTTP connections after the caller finishes.

        Closing the client does not modify its immutable configuration.
        """
        self.session.close()
