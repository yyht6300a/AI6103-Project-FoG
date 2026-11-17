"""Verify the private LLM configuration with one short live completion.

Run python -m src.agent.llm_smoke from the repository root. This incurs API usage;
every attempt is recorded in logs/llm_usage.csv by the normal client.
"""

import json

from src.agent.llm_client import LLMClient
from src.agent.llm_config import load_llm_config


def main() -> None:
    """Call the configured model and print only a safe verification summary.

    Response content is checked in memory and never saved or printed. Exceptions
    are reported by class only to avoid exposing request or authentication data.

    Raises:
        SystemExit: Configuration, transport or response validation fails.
    """
    client = None
    try:
        config = load_llm_config()
        print(json.dumps({
            "requested_model": config.model_name, "base_url": config.base_url,
            "timeout": config.timeout, "max_retries": config.max_retries,
        }), flush=True)
        client = LLMClient(config)
        result = client.complete([{"role": "user", "content": "Reply with OK."}])
        content = result["choices"][0]["message"]["content"]
        if not isinstance(content, str) or content.strip() not in ("OK", "OK."):
            raise ValueError("Unexpected smoke response")
        print(json.dumps({
            "response_valid": True, "returned_model": result.get("model"),
            "usage": result.get("usage"), "usage_csv": "logs/llm_usage.csv",
        }))
    except Exception as error:
        print(json.dumps({"response_valid": False, "error_type": type(error).__name__}))
        raise SystemExit(1) from None
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    main()
