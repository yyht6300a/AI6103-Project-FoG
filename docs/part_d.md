# Member D modules

[English](part_d.md) | [简体中文](part_d.zh-CN.md)

This document summarizes implemented member D modules and their interfaces. Setup and test commands remain in the [README](../README.md#llm-configuration). Potential-subgraph filtering and question-local memory snapshots are implemented; LLM-guided memory updates remain pending.

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

## Potential subgraph construction

[`potential_subgraph.py`](../src/agent/potential_subgraph.py) implements FoG §3.3, Eq. 10: $G^{\mathrm{pot}} = \{t \in G^{\mathrm{xpd}} \mid s_t > \tau_p\}$. Pass member C's refined scores after feedback propagation, aligned with the expanded triples. The default cutoff is $0.7$, matching `potential_threshold` in `config/config-dev.yml`; a caller loading YAML must pass that value explicitly.

```python
from src.agent.potential_subgraph import build_potential_subgraph

triples = [["Country A", "led_by", "Person B"], ["Country A", "anthem", "Song C"]]
refined_scores = [0.7, 0.85]
potential = build_potential_subgraph(triples, refined_scores)
# [("Country A", "anthem", "Song C")]
```

`build_potential_subgraph(triples, scores, potential_threshold=0.7)` accepts aligned sequences and returns a new `list[tuple[str, str, str]]`. Each triple contains three nonblank strings in head/relation/tail order. Their characters and direction are preserved; duplicate qualifying triples occur once, in first qualifying occurrence order. Inputs remain unchanged. Empty input and an empty result are valid. Equality with the cutoff is excluded without a tolerance.

Scores and the cutoff must be finite real scalars, excluding booleans. The function compares scores directly, without sigmoid, normalization, clipping or an assumed $[0, 1]$ range. Member C owns the score scale; the paper's training sigmoid does not introduce an additional transformation into Eq. 10 here. For a one-dimensional PyTorch score tensor, explicitly use `scores.detach().cpu().tolist()` at the caller boundary; multidimensional score lists fail scalar validation. The local member C modules are still placeholders, so their eventual pipeline adapter remains integration work.

Malformed triples, invalid numeric values and mismatched lengths raise `ValueError`, including invalid rows below the cutoff. Validation errors exclude triple text. Filtering makes no API calls and maintains no memory or exclusion state: a triple filtered in one round can qualify on a later call. Permanent rejection belongs to the later LLM memory-management step.

`tests/test_potential_subgraph.py` checks the strict floating-point boundary, YAML default agreement, custom score scales, graph identity and ordering, input preservation, empty results, later-round recovery and invalid inputs. All checks are offline.

## Question-local persistent memory

[`memory_state.py`](../src/agent/memory_state.py) implements the state foundation for FoG §3.3. `QuestionMemory(original_question)` initializes $G^{\mathrm{mem}}$ and the permanent rejection set as empty. Create a separate instance for each question run, including repeated runs with identical question text, and retain the snapshot across that run's reasoning rounds. Persistence here means retention within the run; there is no disk storage or global question cache.

```python
from src.agent.memory_state import QuestionMemory

first = QuestionMemory("Who leads Country A?")
second = QuestionMemory("Who leads Country B?")
assert first.memory_triplets == frozenset()
assert first.rejected_triplets == frozenset()
assert second.memory_triplets == frozenset()
```

`original_question` must be a nonblank string and is stored verbatim. The optional `memory_triplets` and `rejected_triplets` constructor arguments accept iterables of head/relation/tail sequences. Both are copied to `frozenset[tuple[str, str, str]]`: exact duplicates collapse, strings and direction remain unchanged, and iteration order is unspecified. Each triple must contain exactly three nonblank strings. Malformed inputs and overlapping memory/rejection sets raise `ValueError` without including query or triple text. Fields and nested values are immutable, so subsequent changes to supplied lists cannot affect the snapshot or another question.

Future update code can construct a new snapshot, or use `dataclasses.replace`, only after validating the complete LLM result. Keeping the old snapshot supports failure preservation; the later update policy must carry prior permanent rejections forward. This container stores explicit state only: it does not select evidence, infer rejection, call the LLM or filter retrieval. Threshold filtering does not populate either graph. Prompt construction, validated state transitions and retrieval exclusion remain subsequent tasks.

`tests/test_memory_state.py` verifies empty initialization, isolation across different questions and repeated query text, immutable detached snapshots, exact graph identity, invalid-input preservation and threshold filtering without rejection. All checks are offline.
