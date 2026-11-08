"""Send explicit OpenAI-compatible Chat Completions requests.

The transport disables environment-derived proxies, credentials and CA settings.
No vendor SDK or process environment fallback participates in configuration.
"""

from collections.abc import Mapping, Sequence
import time

import requests

from src.agent.llm_config import LLMConfig


class LLMClient:
    """Own a synchronous HTTP session for one validated LLM configuration.

    Call close after use. Each completion permits at most max_retries additional
    attempts for timeouts, connection failures, HTTP 429 and HTTP 5xx responses.
    Other HTTP errors and invalid JSON propagate without retrying.
    """

    def __init__(self, config: LLMConfig) -> None:
        """Create a session without making a network request.

        Args:
            config: Validated model, endpoint, credentials and attempt limits.
        """
        self.config = config
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
                with self.session.post(
                    self.config.base_url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": "Bearer " + self.config.api_key},
                    json={"model": self.config.model_name, "messages": list(messages)},
                    timeout=self.config.timeout,
                    allow_redirects=False,
                ) as response:
                    retryable = response.status_code == 429 or response.status_code >= 500
                    if retryable and attempt < self.config.max_retries:
                        pass
                    else:
                        response.raise_for_status()
                        if response.is_redirect:
                            raise requests.HTTPError("LLM endpoint redirected the request")
                        result = response.json()
                        if not isinstance(result, dict):
                            raise ValueError("LLM response must be a JSON object")
                        return result
            except (requests.Timeout, requests.ConnectionError):
                if attempt == self.config.max_retries:
                    raise
            time.sleep(min(2 ** attempt, 8))
        raise RuntimeError("LLM retry loop exhausted unexpectedly")

    def close(self) -> None:
        """Release pooled HTTP connections after the caller finishes.

        Closing the client does not modify its immutable configuration.
        """
        self.session.close()
