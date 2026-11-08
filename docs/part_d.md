# Member D modules

[English](part_d.md) | [简体中文](part_d.zh-CN.md)

This document summarizes implemented member D modules and their interfaces. Setup and test commands remain in the [README](../README.md#llm-configuration). Potential-subgraph filtering, question-local snapshots and LLM-guided memory replacement are implemented; new rejection tracking and retrieval exclusions remain pending.

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

The update coordinator below constructs a new snapshot only after validating the complete LLM result and carries prior permanent rejections forward. Keeping the old snapshot supports failure preservation. This container stores explicit state only: it does not select evidence, infer rejection, call the LLM or filter retrieval. Threshold filtering does not populate either graph. Retrieval exclusion remains a subsequent task.

`tests/test_memory_state.py` verifies empty initialization, isolation across different questions and repeated query text, immutable detached snapshots, exact graph identity, invalid-input preservation and threshold filtering without rejection. All checks are offline.

## Memory-management prompt

[`memory_prompt.py`](../src/agent/memory_prompt.py) implements `build_memory_prompt(question, memory_triplets, potential_triplets)` from FoG §3.3 and Appendix C, Figure 8 (paper page 21). The template retains the figure's wording, high-recall selection principle, evidence-only answering, exact triple/entity copying constraints, conditional one-to-three clarification questions and the three-field JSON output example.

```python
from src.agent.memory_prompt import build_memory_prompt
from src.agent.memory_state import QuestionMemory

state = QuestionMemory("Who leads the country with Song C?", [
    ("Country A", "anthem", "Song C"),
])
potential = [("Country A", "led_by", "Person B")]
prompt = build_memory_prompt(state.original_question, state.memory_triplets, potential)
```

The original question must be a nonblank string and is inserted verbatim. Both graphs accept iterables of head/relation/tail sequences with exactly three nonblank strings each, including empty graphs, unordered snapshots and generators. Figure 8 has one `Knowledge` field: it receives the union of historical and potential triples, deduplicated by exact equality and sorted lexicographically by tuple for stable presentation. JSON array serialization preserves Unicode and round-trips quotes, newlines, backslashes and all original string values. Sorting graph rows does not change positions within a triple. The graph union and serialization are reproduction choices; the paper does not specify an input ordering or serialization encoding.

Malformed inputs raise `ValueError` without exposing their text. Mutable input containers and memory snapshots remain unchanged; one-shot iterators are consumed. This function only builds text: it makes no API call, validates no model output and performs no memory or rejection update. The caller supplies the eligible potential graph; the builder does not implicitly access rejection sets or retrieval results. The structured-output parser below enforces field types and exact input membership; semantic evidence checks remain pending.

`tests/test_memory_prompt.py` checks the paper instructions and JSON example, graph union and stable ordering, exact string and direction preservation, empty graphs, invalid-input handling and composition with existing memory/potential modules. All checks are offline.

## Structured memory output

[`memory_response.py`](../src/agent/memory_response.py) implements `parse_memory_response(content, memory_triplets, potential_triplets)` for FoG §3.3, Eq. 11 and Appendix C, Figure 8. Pass the assistant content string and the same historical/potential graphs submitted in that call's prompt. The parser accepts exactly one standard JSON object with exactly the three required fields; every field must be an array.

```python
from src.agent.memory_response import parse_memory_response

memory = [("Country A", "anthem", "Song C")]
potential = [("Country A", "led_by", "Person B")]
content = '''{
  "selected_triplets": [["Country A", "led_by", "Person B"]],
  "predict_answer": ["Person B"],
  "subsequent_question": []
}'''
result = parse_memory_response(content, memory, potential)
assert result.predict_answer == ("Person B",)
```

`selected_triplets` must contain arrays of exactly three nonblank strings, each matching a triple in the submitted graph union with identical head/relation/tail positions and characters. `predict_answer` must contain nonblank strings matching heads or tails in the selected triples exactly; entities occurring only in unselected input, relation-only strings, substrings, case changes and whitespace normalization are rejected. `subsequent_question` must contain zero to three nonblank strings. Empty arrays remain valid, including the figure's all-empty schema example; empty selection requires empty answers. Conditional clarification generation remains a subsequent task. No mutual exclusion between answers and questions is imposed.

For tentative answering (TODO 2.1.8), Figure 8 instructs the LLM to use only selected triples and rank multiple answers by likelihood from that evidence. The parser preserves the returned ranking, duplicates and literal entity strings, including across selected multi-hop paths and historical/new evidence. It does not sort by graph order, alphabet or triple counts. Selected-entity membership is a necessary constraint, not proof that an entity answers the question or that the ranking is correct; semantic entailment, sufficiency and relative support remain LLM responsibilities. No additional model call or heuristic verifier is introduced.

The frozen `MemoryResponse` result contains tuple fields named `selected_triplets`, `predict_answer` and `subsequent_question`; selected rows are tuples too. Model order, answer ranking, duplicates and all string characters are preserved. Use the parser for validation: direct construction of the data container does not validate graph membership. Input graphs accept the same iterable contract as the prompt builder. Materialize one-shot iterators before using them for both prompt building and parsing; each call consumes them.

Malformed graphs or outputs raise `ValueError` with content-free messages. Missing, extra or duplicate fields, prose, code fences, trailing values, nonstandard JSON numbers, malformed array items and out-of-range triples/entities are rejected without repair. Duplicate-key rejection and exact field sets are explicit parsing choices to avoid ambiguous responses. The parser makes no API calls, adds no retries and changes neither memory nor permanent rejection state. The coordinator below handles completion envelopes and state updates; retrieval exclusions remain pending.

`tests/test_memory_response.py` covers literal strings, ordering, immutability, empty fields, question-count boundaries, strict JSON and schema errors, graph/entity membership, threshold-hidden and rejected evidence, prompt composition and failure preservation. All checks are offline.

## LLM-guided memory replacement

[`memory_update.py`](../src/agent/memory_update.py) implements `update_memory(state, potential_triplets, client)` for FoG §3.3, Eqs. 11–12. It submits the original question and the exact union of historical memory and eligible new evidence through Figure 8. Its high-recall instruction keeps triples unless the LLM is confident they are irrelevant; this is a prompt policy, not a measured recall guarantee.

```python
from src.agent.memory_update import update_memory

# state is a QuestionMemory; potential contains eligible triples.
# client is a caller-owned LLMClient. This call incurs API usage.
updated_state, response = update_memory(state, potential, client)
state = updated_state  # Publish only after the whole call succeeds.
```

The returned pair contains a new `QuestionMemory` and the parsed `MemoryResponse`. Memory becomes exactly the set of `response.selected_triplets`, rather than a union with old memory. Historical evidence can survive multiple rounds when selected again, disappear when omitted, or be cleared by an empty selection. Response order, duplicates, answer ranking and questions remain available in the response; memory stores an unordered deduplicated graph. The original question and prior permanent rejections are preserved.

Potential input follows the prompt's graph contract, including empty graphs and generators. The coordinator materializes it once so prompt construction and parsing use identical evidence. Invalid state/graphs and potential triples overlapping prior permanent rejections raise `ValueError` before any request; the caller must supply eligible evidence. No new rejection decisions or retrieval filters are implemented yet. Until that policy is connected, omitted triples leave memory without being added to the rejection set.

Only completed (`finish_reason: stop`), nonrefused responses with all three valid output fields produce a new snapshot. Bad envelopes, malformed JSON and out-of-range selections or answers raise `ValueError`, including answers found only in omitted input triples; transport and accounting failures propagate. No path mutates the input snapshot. The caller owns client lifetime and assigns the returned state only on success. Transport retries and per-attempt logging remain in `LLMClient`; output validation adds no retry or fallback. Conditional clarification remains a subsequent task; tentative-answer validation and its semantic limits are described above.

`tests/test_memory_update.py` covers controlled multi-round retention, removal and empty replacement, literal generator evidence, ordered outputs, preserved rejection state, invalid-input prevention, full-output validation, refused/truncated completions, failure preservation, question isolation and mocked HTTP retry/accounting integration. All checks are offline.
