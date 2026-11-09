"""Load LLM settings exclusively from YAML and a literal local dotenv file.

Paths default to the repository root, independently of the working directory.
Loading never reads or modifies process environment variables.
"""

from dataclasses import dataclass, field
import math
from pathlib import Path
import re
import shlex
from urllib.parse import urlsplit

import yaml


ROOT = Path(__file__).resolve().parents[2]
ENV_KEYS = {
    "model_name": "LLM_MODEL_NAME",
    "api_key": "LLM_API_KEY",
    "base_url": "LLM_BASE_URL",
}


def read_dotenv(path: Path) -> dict[str, str]:
    """Parse single-line literal assignments without executing or expanding them.

    Args:
        path: UTF-8 file; a missing file supplies no overrides.

    Returns:
        Assignments supporting optional export prefixes, quotes and comments.

    Raises:
        ValueError: An assignment is malformed or requests interpolation.
    """
    if not path.exists():
        return {}
    values = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, raw = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", key.strip()):
            raise ValueError(f"Invalid dotenv assignment on line {number}")
        try:
            parts = shlex.split(raw, comments=True, posix=True)
        except ValueError:
            raise ValueError(f"Invalid dotenv quoting on line {number}") from None
        if len(parts) > 1 or "$" in raw or "`" in raw:
            raise ValueError(f"Expected a literal dotenv value on line {number}")
        values[key.strip()] = parts[0] if parts else ""
    return values


@dataclass(frozen=True)
class LLMConfig:
    """Hold validated connection settings without exposing the key in repr.

    Instances are immutable and validated before a client can use them.
    Timeout is measured in seconds; max_retries excludes the first attempt.
    """

    model_name: str
    api_key: str = field(repr=False)
    base_url: str
    timeout: float
    max_retries: int

    def __post_init__(self) -> None:
        """Reject incomplete or unsafe settings before any network operation.

        Raises:
            ValueError: A required value, supported model or numeric bound is invalid.
        """
        if self.model_name not in ("deepseek-flash", "gpt-4o-mini", "gpt-4", "gpt-4o"):
            raise ValueError(
                "LLM model_name must be deepseek-flash, gpt-4o-mini, gpt-4, or gpt-4o"
            )
        if not isinstance(self.api_key, str) or not self.api_key.strip():
            raise ValueError("Set LLM_API_KEY in the local .env file")
        if isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float)):
            raise ValueError("LLM timeout must be a finite number greater than zero")
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("LLM timeout must be a finite number greater than zero")
        if type(self.max_retries) is not int or self.max_retries < 0:
            raise ValueError("LLM max_retries must be a non-negative integer")
        if not isinstance(self.base_url, str):
            raise ValueError("LLM base_url must be an absolute HTTP(S) URL")
        try:
            url = urlsplit(self.base_url)
            valid = url.scheme in ("http", "https") and url.hostname and url.port != 0
        except ValueError:
            valid = False
        if not valid or url.username or url.password or url.query or url.fragment:
            raise ValueError("LLM base_url must be HTTP(S), without credentials, query or fragment")


def load_llm_config(
    yaml_path: Path = ROOT / "config/config-dev.yml",
    env_path: Path = ROOT / ".env",
) -> LLMConfig:
    """Merge local connection overrides with YAML-only request policy.

    Args:
        yaml_path: YAML llm mapping with defaults, timeout and max_retries.
        env_path: Literal model/key/URL overrides; the key is accepted only here.

    Returns:
        Settings ready for explicit transmission to an LLM client.

    Raises:
        ValueError: File structure or final settings are invalid.
        OSError: A configuration file cannot be read.
    """
    document = yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("llm"), dict):
        raise ValueError("YAML must contain an llm mapping")
    defaults = document["llm"]
    if defaults.get("provider", "openai") != "openai":
        raise ValueError("Only the OpenAI-compatible Chat Completions protocol is supported")
    overrides = read_dotenv(Path(env_path))
    merged = {
        key: overrides.get(env_key, "").strip() or defaults.get(key)
        for key, env_key in ENV_KEYS.items()
    }
    # Secrets must never be loaded from a shared YAML file.
    merged["api_key"] = overrides.get("LLM_API_KEY", "").strip()
    # Request policy belongs to the shared YAML, never to local overrides.
    merged["timeout"] = defaults.get("timeout")
    merged["max_retries"] = defaults.get("max_retries")
    for key, converter in (("timeout", float), ("max_retries", int)):
        value = merged[key]
        if isinstance(value, str):
            try:
                merged[key] = converter(value)
            except (ValueError, OverflowError):
                raise ValueError(f"LLM {key} has an invalid numeric value") from None
    return LLMConfig(**merged)
