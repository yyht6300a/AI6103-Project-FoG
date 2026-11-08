"""Append public, content-free accounting metadata for each HTTP attempt.

The shared CSV is an audit trail, not a bill: missing service usage stays blank.
Writers are synchronous; concurrent processes must serialize access externally.
"""

import csv
from pathlib import Path

from src.agent.llm_config import ROOT


USAGE_PATH = ROOT / "logs/llm_usage.csv"
FIELDS = (
    "timestamp", "model", "input_tokens", "output_tokens", "status",
    "error_type", "request_id",
)


class UsageLog:
    """Append one row per attempt without recording payloads or exception text.

    The default path is shared by every client regardless of working directory.
    Tests may inject a private destination to keep synthetic usage out of it.
    """

    def __init__(self, path: Path = USAGE_PATH) -> None:
        """Prepare a writable CSV before allowing any paid request.

        Args:
            path: UTF-8 CSV destination; existing headers must match the schema.

        Raises:
            ValueError: An existing nonempty file has an incompatible header.
            OSError: The directory or file cannot be created or opened.
        """
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+", encoding="utf-8", newline="") as stream:
            stream.seek(0)
            header = next(csv.reader(stream), None)
            if header is None:
                csv.writer(stream).writerow(FIELDS)
            elif header != list(FIELDS):
                raise ValueError("LLM usage CSV header does not match the schema")

    def record(
        self, timestamp: str, model: str, result: object,
        request_id: object, error_type: str,
    ) -> None:
        """Persist safe metadata returned by the service or attempt handler.

        Args:
            timestamp: Timezone-aware ISO timestamp captured before the attempt.
            model: Requested model, used when the service returns no model.
            result: Decoded JSON, if available; payload content is never saved.
            request_id: Service x-request-id header, not the completion ID.
            error_type: Exception class name, or empty on success.

        Raises:
            OSError: Appending fails; callers must surface this accounting failure.
        """
        body = result if isinstance(result, dict) else {}
        usage = body.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        row = {
            "timestamp": timestamp,
            "model": body.get("model") if isinstance(body.get("model"), str) else model,
            "status": "error" if error_type else "success",
            "error_type": error_type,
            "request_id": request_id if isinstance(request_id, str) else "",
        }
        for field, key in (("input_tokens", "prompt_tokens"),
                           ("output_tokens", "completion_tokens")):
            value = usage.get(key)
            row[field] = value if type(value) is int and value >= 0 else ""
        with self.path.open("a", encoding="utf-8", newline="") as stream:
            csv.DictWriter(stream, fieldnames=FIELDS).writerow(row)
