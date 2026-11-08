# Member D modules

This document summarizes implemented member D modules and their interfaces. Setup and test commands remain in the [README](../README.md#llm-configuration). Memory subgraph management is not implemented yet.

## LLM configuration and requests

[`llm_config.py`](../src/agent/llm_config.py) merges literal private `.env` values with shared YAML defaults and validates the effective configuration. [`llm_client.py`](../src/agent/llm_client.py) exposes `LLMClient.complete(messages)` for OpenAI-compatible Chat Completions. Requests receive settings explicitly and do not inherit process configuration. The caller owns and closes the client; timeout, connection failures, HTTP $429$ and HTTP $5xx$ follow its bounded retry policy.

## Per-attempt usage accounting

[`llm_usage.py`](../src/agent/llm_usage.py) appends one UTF-8 CSV row per actual HTTP attempt, including retries and failures. Metadata records request start time, model, returned token counts, outcome, exception class and request ID. Unknown values remain blank. Credentials and request/response content are excluded. A later application validation failure does not change a successful HTTP accounting row. Configuration and accounting tests are in `tests/test_llm_configuration.py` and `tests/test_llm_usage.py`.

## Initial question decomposition

[`question_decomposition.py`](../src/agent/question_decomposition.py) implements the initial $Q_{\mathrm{sub}}$ from FoG §3.1 and Appendix C, Figure 7 (paper page 21). The prompt retains the figure's wording and printed numbering beginning at `2`: produce standalone sub-questions that jointly resolve the original question, assume no missing facts and clarify only when necessary.

```python
from src.agent.llm_client import LLMClient
from src.agent.llm_config import load_llm_config
from src.agent.question_decomposition import decompose_question

client = LLMClient(load_llm_config())
try:
    sub_questions = decompose_question(
        "Which man leads the country that uses Libya, Libya, Libya as its national anthem?",
        client,
    )
finally:
    client.close()
```

This example makes a paid request. `decompose_question(question, client)` returns an ordered `list[str]` for retrieval/scoring without creating retrieval or memory state. `build_decomposition_prompt(question)` and `parse_sub_questions(content)` also work independently without API calls.

The original question must be a nonblank string and is inserted unchanged. A completed (`finish_reason: stop`), nonrefused response must contain only a nonempty JSON array of nonblank strings. Markdown fences, prose and malformed lists are rejected. Strings, order and duplicates are preserved. Figure 7 sets no question-count limit; Figure 8's one-to-three limit concerns subsequent memory-management questions. These checks validate structure, not semantic correctness.

Invalid inputs and outputs raise `ValueError` without exposing their text in validation messages. Transport and accounting errors propagate from `LLMClient`; output validation adds no retry, fallback or substitute question.

`tests/test_question_decomposition.py` covers prompt constraints, literal input insertion, JSON validation, response envelopes, refusals, truncation, transport failures, request payloads, retries and accounting. All HTTP calls in these tests are mocked.
