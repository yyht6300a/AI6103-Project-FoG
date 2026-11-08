# Member D modules

[English](part_d.md) | [简体中文](part_d.zh-CN.md)

This document summarizes implemented member D modules and their interfaces. Setup and test commands remain in the [README](../README.md#llm-configuration). Potential-subgraph filtering, question-local snapshots, LLM-guided memory replacement, permanent rejection tracking and expansion exclusions are implemented. Wiring the retriever and inference loop remains integration work.

## LLM configuration and requests

For the unified C/E entry point, see [Scored-round MSM interface](#scored-round-msm-interface).

[`llm_config.py`](../src/agent/llm_config.py) merges literal private `.env` values with shared YAML defaults and validates the effective configuration. [`llm_client.py`](../src/agent/llm_client.py) exposes `LLMClient.complete(messages)` for OpenAI-compatible Chat Completions. Requests receive settings explicitly and do not inherit process configuration. The caller owns and closes the client; timeout, connection failures, HTTP $429$ and HTTP $5xx$ follow its bounded retry policy.

## Per-attempt usage accounting

[`llm_usage.py`](../src/agent/llm_usage.py) appends one UTF-8 CSV row per actual HTTP attempt, including retries and failures. Metadata records request start time, model, returned token counts, outcome, exception class and request ID. Unknown values remain blank. Credentials and request/response content are excluded. A later application validation failure does not change a successful HTTP accounting row. Configuration and accounting tests are in `tests/test_llm_configuration.py` and `tests/test_llm_usage.py`.

## Initial question decomposition

[`question_decomposition.py`](../src/agent/question_decomposition.py) implements the initial $Q_{\mathrm{sub}}$ from FoG §3.1 and Appendix C, Figure 7 (paper page 21). The prompt retains the figure's wording with instruction numbering corrected to begin at `1`: produce standalone sub-questions that jointly resolve the original question, assume no missing facts and clarify only when necessary.

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

The update coordinator below constructs a new snapshot only after validating the complete LLM result and carries prior permanent rejections forward. Keeping the old snapshot supports failure preservation. This container stores explicit state and provides expansion filtering as described below; it does not select evidence, infer rejection or call the LLM. Threshold filtering does not populate either graph.

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

`selected_triplets` must contain arrays of exactly three nonblank strings, each matching a triple in the submitted graph union with identical head/relation/tail positions and characters. `predict_answer` must contain nonblank strings matching heads or tails in the selected triples exactly; entities occurring only in unselected input, relation-only strings, substrings, case changes and whitespace normalization are rejected. Empty selection requires empty answers. `subsequent_question` contains at most three nonblank strings and must be nonempty when answers are empty; see the clarification contract below. Figure 8's all-empty object remains a schema illustration in the prompt, but fails completed-response validation.

For tentative answering (TODO 2.1.8), Figure 8 instructs the LLM to use only selected triples and rank multiple answers by likelihood from that evidence. The parser preserves the returned ranking, duplicates and literal entity strings, including across selected multi-hop paths and historical/new evidence. It does not sort by graph order, alphabet or triple counts. Selected-entity membership is a necessary constraint, not proof that an entity answers the question or that the ranking is correct; semantic entailment, sufficiency and relative support remain LLM responsibilities. No additional model call or heuristic verifier is introduced.

The frozen `MemoryResponse` result contains tuple fields named `selected_triplets`, `predict_answer` and `subsequent_question`; selected rows are tuples too. Model order, answer ranking, duplicates and all string characters are preserved. Use the parser for validation: direct construction of the data container does not validate graph membership. Input graphs accept the same iterable contract as the prompt builder. Materialize one-shot iterators before using them for both prompt building and parsing; each call consumes them.

Malformed graphs or outputs raise `ValueError` with content-free messages. Missing, extra or duplicate fields, prose, code fences, trailing values, nonstandard JSON numbers, malformed array items and out-of-range triples/entities are rejected without repair. Duplicate-key rejection and exact field sets are explicit parsing choices to avoid ambiguous responses. The parser makes no API calls, adds no retries and changes neither memory nor permanent rejection state. The coordinator below handles completion envelopes and state updates; the state snapshot exposes expansion exclusions.

`tests/test_memory_response.py` covers literal strings, ordering, immutability, empty fields, question-count boundaries, strict JSON and schema errors, graph/entity membership, threshold-hidden and rejected evidence, prompt composition and failure preservation. All checks are offline.

### Exact-match constraints

TODO 2.1.10 uses Figure 8's prohibition of external knowledge, fabrication and triple rewriting/creation/reordering/merging/splitting. The parser enforces exact membership of each complete decoded head/relation/tail tuple in the submitted union. It rejects component permutations, cross-row recombination, compressed paths, split or merged names, case/whitespace changes and Unicode normalization unless the resulting triple itself was submitted. It does not repair output. Equivalent JSON escapes represent the same decoded strings and are accepted. Reordering intact rows in `selected_triplets` is allowed; the component positions within each triple remain fixed.

Answers must exactly match selected endpoint strings as described above. These checks reject fabricated triples and out-of-range entities, but cannot detect external reasoning that returns an otherwise valid input entity. Evidence-only reasoning remains an LLM instruction. Clarification questions are natural-language retrieval queries and are not subject to triple membership. Invalid selection or answers fail before publication, preserving both old sets without an application retry. Regression tests cover all nonidentity component permutations, rewriting/merging/splitting, equivalent JSON escapes, selected row order and coordinator state preservation.

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

Potential input follows the prompt's graph contract, including empty graphs and generators. The coordinator materializes it once so prompt construction and parsing use identical evidence. Invalid state/graphs and potential triples overlapping prior permanent rejections raise `ValueError` before any request; the caller must supply eligible evidence. After complete validation, omitted submitted triples join the permanent rejection set according to the exploration policy below.

Only completed (`finish_reason: stop`), nonrefused responses with all three valid output fields produce a new snapshot. Bad envelopes, malformed JSON and out-of-range selections or answers raise `ValueError`, including answers found only in omitted input triples; transport and accounting failures propagate. No path mutates the input snapshot. The caller owns client lifetime and assigns the returned state only on success. Transport retries and per-attempt logging remain in `LLMClient`; output validation adds no retry or fallback. Tentative-answer validation and its semantic limits are described above.

`tests/test_memory_update.py` covers controlled multi-round retention, removal and empty replacement, literal generator evidence, ordered outputs, preserved rejection state, invalid-input prevention, full-output validation, refused/truncated completions, failure preservation, question isolation and mocked HTTP retry/accounting integration. All checks are offline.

### Controlled multi-round validation

TODO 2.2.1 is covered by `tests/test_memory_rounds.py` through `manage_memory` and the real `LLMClient`, with only the HTTP adapter mocked. A five-round script accumulates one, two and three triples, removes an old triple with no new candidates, then clears memory with an empty selection. Each round checks the actual submitted evidence union, unchanged original question, exact selected memory, ordered answers and follow-up questions, distinct returned snapshots and preservation of every earlier snapshot. Five successful accounting rows establish one HTTP attempt per round. Run this check with `python -m unittest discover -s tests -p test_memory_rounds.py -v` using the project environment. It validates controlled state transitions without paid requests; real-model selection quality and C/E pipeline integration remain unverified.

## Exploration efficiency

TODO 2.1.11 implements the two exclusion rules in FoG §3.3. `state.excluded_triplets` is the immutable union of current memory and cumulative permanent rejections. Call `state.filter_expansion_triplets(candidates)` at each retrieval/expansion boundary, before scoring or further expansion. It returns an ordered list of eligible tuples, preserving first occurrences and literal strings while removing exact duplicates. It validates every row, including excluded candidates, consumes generators once and leaves state and input containers unchanged. Filtering is by complete triple identity; sharing an entity or reversing an edge does not by itself exclude another triple.

```python
# candidates are triples returned by caller-owned retrieval.
eligible = state.filter_expansion_triplets(candidates)
# Score eligible only; scores must stay positionally aligned with eligible.
potential = build_potential_subgraph(eligible, refined_scores)
state, response = update_memory(state, potential, client)
excluded_for_next_round = state.excluded_triplets
```

The caller must apply the exclusion at every expansion step; this module does not implement a retriever or modify member C's scoring. Already absorbed memory is excluded from repeated expansion, but is still submitted from `state.memory_triplets` in every memory-management prompt. Supplying overlapping memory in a potential graph is harmless: the prompt deduplicates it. Supplying permanently rejected evidence to `update_memory` raises before an API request, guarding against a missed upstream filter.

For a fully valid LLM response, let $U = G^{\mathrm{mem}} \cup G^{\mathrm{pot}}$ be the exact submitted union and $S$ the selected set. The new memory is $S$, and permanent rejections become $R' = R \cup (U \setminus S)$. This includes both unselected new evidence and omitted historical memory. Mapping all omitted submitted triples to “explicit rejection” is a user-approved reproduction assumption; the paper does not explicitly define this mapping. No decision is inferred from malformed, refused, unfinished or failed calls. Rejections persist for that question run, including after memory is emptied; fresh runs have independent masks even with identical question text. Threshold-hidden triples never submitted in that call are outside $U$ and acquire no rejection; later retrieval can recover them.

`tests/test_exploration_efficiency.py` covers candidate order and exact identity, validation, cumulative multi-round masks, historical prompt retention, rejected-input prevention, threshold-hidden recovery, failed-response preservation and question isolation. Existing update tests also verify rejection of removed historical memory and empty selections. All checks are offline; actual retrieval-loop wiring remains integration work.

## Fault tolerance for threshold-hidden evidence

TODO 2.1.12 composes the existing potential-subgraph, expansion-filter and memory-update boundaries. A triple with refined score $s_t \leq \tau_p$ is absent from that round's potential graph. If it is also absent from historical memory, it is not submitted to the LLM and gains no permanent rejection. Temporary hiding creates no separate state or persistent candidate blacklist. Historical memory still reaches the LLM regardless of current retrieval scores; its omission from a valid selection does create permanent rejection under the policy above.

For the next round, pass `response.subsequent_question` to retrieval, apply the current state's expansion exclusions, obtain new positionally aligned refined scores, and call `build_potential_subgraph` again. Previously hidden evidence remains eligible even after several low-score rounds; it can enter the potential graph once its new score strictly exceeds the same cutoff and enter memory if selected by the LLM. Recovery requires rediscovery and re-scoring; it is not automatic reinsertion, a threshold reduction or a retry of the previous LLM request. A submitted, rejected triple remains excluded even if later retrieval would assign it a higher score.

An empty potential graph is valid: memory management still submits historical memory, or an empty Knowledge array when memory is also empty. An undetermined response must supply clarification questions under the parser contract. `tests/test_fault_tolerance.py` verifies clarification-driven retrieval and re-scoring, below/equal cutoffs at default and custom thresholds, recovery after an initially empty graph, repeated hiding, retained history and permanent-rejection controls. Prompt assertions check exactly which evidence was submitted each round. These are controlled offline integration tests of Member D boundaries; real C/E retrieval and scoring integration and live-model quality remain unverified.

## Scored-round MSM interface

[`memory_management.py`](../src/agent/memory_management.py) exposes `manage_memory(state, triples, scores, client, potential_threshold=0.7)` for TODO 2.1.13. Member C supplies positionally aligned expanded triples and refined scores after feedback propagation; member E owns the question snapshot, retrieval loop and client lifetime. Input types, finite-number checks, exact string handling, strict threshold and explicit tensor conversion follow [potential subgraph construction](#potential-subgraph-construction). Pass a YAML-loaded cutoff explicitly; this function does not read files or configuration.

```python
from src.agent.memory_management import manage_memory
from src.agent.memory_state import QuestionMemory

state = QuestionMemory(original_question)  # Once per independent question run.
# Repeat these steps in E's loop; retrieval and C's scorer are caller supplied.
candidates = state.filter_expansion_triplets(retrieve(sub_questions))
scores = refine_scores(candidates)  # Aligned 1-D Python scalar sequence.
result = manage_memory(state, candidates, scores, client, potential_threshold=0.7)
state = result.state  # Publish only when the entire call succeeds.
sub_questions = result.response.subsequent_question
```

The retrieval/scoring names above illustrate caller dependencies, not implemented functions. Calling the entry point incurs LLM usage, including for empty input or an empty potential graph. It invokes the existing update coordinator once, with transport retries and accounting still owned by `LLMClient`.

| Returned field | C/E use |
| --- | --- |
| `result.state.memory_triplets` | Updated immutable evidence graph |
| `result.response.predict_answer` | Tentative answer tuple in model ranking order |
| `result.response.subsequent_question` | Ordered next-round retrieval queries |
| `result.state.excluded_triplets` | Current memory plus permanent rejections; exclude at each expansion boundary |
| `result.state.rejected_triplets` | Permanent question-local mask |
| `result.response.selected_triplets` | Validated selection retaining model order and duplicates |

`MSMResult` is a frozen container with `state: QuestionMemory` and `response: MemoryResponse`; it reuses existing immutable contracts rather than maintaining duplicate state. All rows and scores are validated before filtering, including stale memory/rejected rows and low scores. Thresholding operates on aligned pairs before exact deduplication: a duplicate qualifies if any occurrence exceeds the cutoff. The facade defensively removes current memory and permanent rejections from new potential input; historical memory independently reaches the prompt even when absent or low-scoring now. This differs from lower-level `update_memory`, which rejects permanently rejected potential input. E must still filter at retrieval/expansion boundaries to avoid wasted expansion and scoring; filtering an already scored list without filtering its matching scores would break alignment.

Only fully validated output yields a new result. `ValueError` reports invalid input or output; transport and accounting errors propagate unchanged. No input snapshot is mutated, no application retry/fallback is added and no client is closed. E retains its prior snapshot on exceptions and decides stopping conditions and round limits; neither a tentative answer nor an empty question tuple automatically terminates a loop here. Permanent omission and temporary threshold hiding follow the policies above. `tests/test_memory_management.py` covers aligned duplicate rows, stale exclusions, all output fields, empty/custom-threshold inputs, multi-round recovery/removal, question isolation and failed-call preservation. Real C/E wiring and live-model quality remain integration work.

## LLM failures and state preservation

TODO 2.1.14 uses the existing `LLMClient` retry policy and atomic `manage_memory` / `update_memory` boundary. Each request has the configured connect/read timeout; timeouts, connection failures, HTTP 429 and HTTP 5xx allow at most `max_retries` additional attempts, with delays of $1$, $2$, $4$, then at most $8$ seconds. Exhaustion propagates the transport exception (`requests.Timeout`, `requests.ConnectionError` or `requests.HTTPError`). Other HTTP errors, malformed HTTP JSON and invalid completion envelopes or structured output propagate without a business retry, JSON repair or provider fallback. Invalid JSON and output validation raise `ValueError`; this includes triples outside the submitted union and answers outside the selected triples' exact head/tail entity strings.

No failure returns an `MSMResult` or changes the input snapshot's memory, permanent rejections or expansion exclusions. Submitted omissions acquire rejection only after the entire response passes validation. E should assign `state = result.state` only on success; after an exception it retains the old snapshot and decides whether to stop or explicitly call again. Retaining state does not roll back API usage: every transport attempt has its own CSV row. Malformed HTTP JSON is an accounting error, while a valid HTTP JSON object whose model content later fails validation keeps its HTTP success row and returned usage metadata. Logging failures propagate as `OSError` without an automatic retry.

`tests/test_memory_failures.py` exercises real HTTP response decoding and the scored-round entry point with only the network adapter and retry clock replaced. It verifies zero/two extra retry budgets for timeout and rate-limit exhaustion, identical evidence across timeout/429 recovery, per-attempt accounting, malformed outer and inner JSON, invalid envelopes, invented/hidden/rejected selections, unknown and unselected-history answers, unchanged nonempty state and a successful explicit call after validation failures. These tests use synthetic credentials and private temporary CSV files; they make no paid requests and do not validate live-model quality or E's exception policy.

## Subsequent sub-questions

For TODO 2.1.9 (FoG §3.3, Eq. 11 and Appendix C), Figure 8 asks the LLM to generate one to three clarification questions when the selected evidence cannot determine the answer. `update_memory` returns these as `response.subsequent_question`, the ordered tuple representing $Q'_{\mathrm{sub}}$ for the caller's next retrieval round. Pass these strings to retrieval instead of rerunning initial question decomposition; keep `state.original_question` as the original query for subsequent memory-management calls. The retriever and inference loop remain integration work.

The parser treats empty `predict_answer` as the model declaring the answer undetermined and requires one to three questions, even with no input or selected triples. With tentative answers, zero to three questions are accepted: partial answers may need further evidence, so the fields are not mutually exclusive. This is an explicit validation convention, not a semantic sufficiency test. The LLM determines missing evidence and question wording; code does not prove relevance, infer sufficiency from graph size, invent replacement questions or make a second LLM call.

Question strings, whitespace, Unicode, order and duplicates are preserved. These are natural-language retrieval queries, so they need not match input entity strings or end with a particular punctuation mark. Blank/non-string items, more than three questions, or unanswered output with no questions raise `ValueError` before any new snapshot is published. Old memory and permanent rejections remain intact, with no application retry. Tests cover count boundaries with and without answers, literal output preservation, invalid-output state preservation and a controlled two-round handoff through mocked retrieval to a final answer. No live-model question quality is claimed by these offline checks.
