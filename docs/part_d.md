# Member D modules

[English](part_d.md) | [简体中文](part_d.zh-CN.md)

This document summarizes implemented member D modules and their interfaces. Setup and test commands remain in the [README](../README.md#llm-configuration). Potential-subgraph filtering, question-local snapshots, LLM-guided memory replacement, permanent rejection tracking and expansion exclusions are implemented. Wiring the retriever and inference loop remains integration work.

## LLM configuration and requests

For the unified C/E entry point, see [Scored-round MSM interface](#scored-round-msm-interface).

[`llm_config.py`](../src/agent/llm_config.py) merges literal private `.env` values with shared YAML defaults and validates the effective configuration. [`llm_client.py`](../src/agent/llm_client.py) exposes `LLMClient.complete(messages)` for OpenAI-compatible Chat Completions. Requests receive settings explicitly and do not inherit process configuration. The caller owns and closes the client; timeout, connection failures, HTTP $429$ and HTTP $5xx$ follow its bounded retry policy.

## Live acceptance runner

TODO 2.3.1 provides [`scripts/validate_part_d.py`](../scripts/validate_part_d.py). It loads private `.env` and shared YAML through `load_llm_config`, creates a real `LLMClient`, calls public `decompose_question` and `manage_memory`, and closes the client in `finally`. Runtime HTTP and replies are real; only C/E retrieval and scores are controlled fixtures. It does not repair output or add application retries. The strict potential cutoff is fixed at $0.7$.

Run from the repository root with the project Python environment:

```text
python scripts/validate_part_d.py --list-cases
python scripts/validate_part_d.py --suite single
python scripts/validate_part_d.py --suite multi
python scripts/validate_part_d.py --suite removal
python scripts/validate_part_d.py --suite ranking
python scripts/validate_part_d.py --suite all
```

`--list-cases` is free and does not read private configuration; other commands incur API usage. Every memory round uses default `manage_memory`, adding a separate ranking request whenever multiple answers are returned. There is no business option to disable ranking; zero or one answer skips it. `single` has four cases including decomposition, permitting at most $7$ logical calls; four `multi` rounds permit $8$, three `removal` rounds permit $6$, and two `ranking` cases permit $4$. Default `all` has $13$ cases and permits $25$ logical calls. Bounds allow multiple answers in every memory round rather than assuming the model returns the expected count; actual skip paths use fewer calls. With YAML retries, the HTTP bound is the logical-call limit times `(max_retries + 1)`, currently $21$, $24$, $18$, $12$ and $75$. Reports distinguish case counts from actual started logical calls. Any terminal error in either stage stops the suite.

Each paid run writes a unique private `tmp/tmp_part_d_validation_<UTC timestamp>.json` with expectations, controlled triples and aligned scores, actual queries and validated outputs, submitted graphs, before/after state, exception classes and state-rule checks. It excludes keys, failed raw replies and exception messages. Actual attempts still append to `logs/llm_usage.csv`; reconcile returned model and usage metadata during TODO 2.3.6. Reports are private review material and are not automatically committed.

MSM output failures now expose `failure_stage` and `error_code` from the closed registry in [`validation_error.py`](../src/agent/validation_error.py), using a `ValueError` subclass. Codes distinguish envelope, content, JSON, field, selection, answer and clarification checks; other exceptions use `unclassified` without inspecting their messages. Run `python scripts/validate_part_d.py --suite empty_diagnostic` for one conditional-question call with empty knowledge; it is excluded from default `all` and supports free `--list-cases` preview. The new GPT-4o-mini call passed strict parsing and requested the publisher, so it does not establish the cause of the old failure. Strict parsing, atomic state updates and retry policy remain unchanged.

Exit code $0$ means parsing and state checks passed; semantic review remains pending. Review query usefulness, evidence sufficiency, answer support and ranking, and clarification quality manually. Multi-round observations record actual retention, removal, rejection, recovery and isolation; `false` means unobserved coverage, which may require refining fixtures before live acceptance can pass. Do not force model choices. The [semantic review](#semantic-quality-review) records actual multiple-answer coverage and unresolved findings. Final acceptance requires GPT-4o-mini and matching OpenAI settings under TODO 2.3.5. Runner preparation does not complete TODO 2.3.2-2.3.6 or C/E integration.

`tests/test_part_d_validation.py` checks free preview, bounded full execution through public parsing and isolated usage accounting, fail-fast behavior, old-state preservation, cleanup and sensitive-error suppression offline. Synthetic transport is confined to tests. No paid calls were made for TODO 2.3.1.

### Single-round live result

TODO 2.3.2 passed on 2026/11/09 using `python scripts/validate_part_d.py --suite single` with the unchanged private DeepSeek configuration. All $4$ logical calls returned `deepseek-flash`, one HTTP attempt each, without manual response edits, parser bypass, output repair or retries. Decomposition returned “Who is the author of The Glass Orchard?” and “In which city was the author of The Glass Orchard born?”. The second question retains an unresolved author reference; retrieval usefulness needs the broader TODO 2.3.4 review.

| Case | Validated output and state |
| --- | --- |
| Sufficient evidence | Both author and birthplace triples selected; answer `Linden City`; no subsequent questions; both triples in expansion exclusions |
| Insufficient evidence | Author triple retained; no answer; clarification `Which city was Mira Vale born in?`; author triple in expansion exclusions |
| Empty potential graph and memory | Empty selection, answer, memory and exclusions; clarification `Who is the author of The Glass Orchard?` |

All three MSM calls preserved original snapshots, produced no permanent rejections and satisfied memory/rejection/exclusion invariants. The four appended rows in [`logs/llm_usage.csv`](../logs/llm_usage.csv), starting at 2026/11/09 00:31:25 (UTC+08:00), report $860$ input and $851$ output tokens, all successful; request IDs were not supplied and remain blank. Full outputs and states remain in private `tmp/tmp_part_d_validation_20261108T163125709480Z.json`; its automatic semantic-review flag remains pending. These fixtures do not exercise rejection transitions or multi-answer ranking. TODO 2.3.3-2.3.6, including broader semantic review and final GPT-4o-mini acceptance, remain pending; C/E integration is also unverified.

### Multi-round live result

TODO 2.3.3 passed on 2026/11/09 with the unchanged private DeepSeek configuration. Run `python scripts/validate_part_d.py --suite multi` for the original four calls and `python scripts/validate_part_d.py --suite removal` for the additional three calls. All seven returned `deepseek-flash` and passed strict parsing and state checks, one HTTP attempt each, with no output edits or parser bypass. Reports record actual previous questions and memory at each handoff; retrieval and scores remain controlled inputs.

| Round | Actual selection and state transition |
| --- | --- |
| `multi.partial` | Retained `AUTHOR`; omitted submitted `DISTRACTOR`, making it permanently rejected; `BIRTH` at score $0.7$ was unsubmitted and remained eligible; asked `What city was Mira Vale born in?` |
| `multi.recovery` | Used that query; retained `AUTHOR`, selected `BIRTH` at score $0.95$, and answered `Linden City`; rejected distractor absent from submission |
| `multi.exclusion_probe` | Retained both path triples and `Linden City`; high-score distractor remained masked; no old memory removal observed |
| `multi.independent` | Fresh empty memory and rejection state selected the same `DISTRACTOR` and answered `Nolan Reed`; earlier question's rejection did not transfer |
| `removal.unresolved` | Retained Mira Vale's birthplace as potentially useful, gave no answer, and asked whether Silver Press published the book and where Nolan Reed was born |
| `removal.resolved` | Used those queries with Bronze Press publisher and Nolan Reed birthplace evidence; selected both new triples, answered `Cedar City`, and removed and permanently rejected Mira Vale's old birthplace triple |
| `removal.exclusion_probe` | Removed triple at score $0.95$ was absent from submission; rejection persisted, with the two selected branch triples and `Cedar City` retained |

All seven old snapshots remained unchanged and all memory, rejection and exclusion invariants passed. `multi_observations.old_memory_removal` remains honestly `false` in the original report; all four additional `removal_observations` are `true`. Empty returned questions do not stop forced probes. The conditional fixture assumes one publisher and resolves the otherwise branch from a different publisher; this is controlled fixture semantics, not a general proof of negative facts in an open-world graph. Broader semantic quality, multiple-answer ranking and GPT-4o-mini acceptance remain TODO 2.3.4 and 2.3.5; C/E integration remains unverified.

Seven new rows in [`logs/llm_usage.csv`](../logs/llm_usage.csv), starting at 2026/11/09 00:35:53 and 00:37:47 (UTC+08:00), report $1866$ input and $3259$ output tokens, all successful. Request IDs were not supplied and remain blank. Private reports are `tmp/tmp_part_d_validation_20261108T163553245365Z.json` and `tmp/tmp_part_d_validation_20261108T163747907687Z.json`; automatic semantic-review flags remain pending. No production module or private configuration changed. Extended offline runner checks cover call bounds, parsing, query handoff, removal and repeated exclusion; all $89$ offline tests pass. TODO 2.3.6 will consolidate accounting and handoff.

### Semantic quality review

TODO 2.3.4 review completed on 2026/11/09 with findings; this is not an overall semantic acceptance pass. Reviewed all $11$ earlier real outputs in the three private reports above and $2$ additional real `deepseek-flash` calls from `python scripts/validate_part_d.py --suite ranking`, using unchanged private settings and strict production parsing. The new report is `tmp/tmp_part_d_validation_20261108T164441286155Z.json`. Original reports retain their automatic `semantic_review: pending` flags; this section is the authoritative manual assessment, without rewriting model output.

| Aspect / cases | Assessment from actual output |
| --- | --- |
| Initial decomposition | Author lookup is useful; the birthplace query repeats the original dependency without resolving the author. It is understandable alone but not an explicit entity-level birthplace lookup. Recall with real retrieval remains unverified; later MSM correctly asks about `Mira Vale`. |
| Sufficient evidence and multi-round recovery/probe | Both edges of the book-author-city path remain selected and support `Linden City`; no irrelevant author/book enters the answer. Necessary path retention and rejection of the unrelated book are reasonable for these fixtures. |
| Insufficient, empty and partial evidence | No unsupported birthplace answer; author evidence is retained when available. Clarification targets the missing author or the named author's birthplace and can guide another retrieval round. |
| Independent question | `Nolan Reed` is supported by the selected authorship edge; earlier question-local rejection does not suppress it. |
| Conditional branch | Initially retains potentially relevant birthplace evidence, withholds an answer and asks about publisher and the other birthplace. Later `Cedar City` follows only under the fixture's single-publisher assumption. A different publisher alone does not exclude Silver Press in an open-world graph; the parser cannot detect this assumption. Removal is reasonable within the fixture, not a general negative-inference result. |
| `ranking.equal_support` | Selects both direct birthplace edges and answers `["Linden City", "Cedar City"]`, with no clarification. Both answers are supported; equal evidence allows either order and does not establish a strict ranking. |
| `ranking.unequal_support` | Selects `confirmed_birthplace -> Linden City` and `reported_possible_birthplace -> Cedar City`, excludes `lived_in -> Harbor City`, but answers only `["Cedar City"]` with no clarification. Entity membership passes, yet the stronger confirmed candidate is absent. Expected both candidates with confirmed first; unequal-support ranking remains unverified. |

The unequal-support fixture treats confirmed and possible birthplaces as candidate answers, with stronger qualitative support for confirmed evidence. The word “candidate” may instead have been interpreted as excluding a confirmed fact; the reply provides no explanation. Record this as an unresolved completeness/wording finding, not proof of the model's internal reason or a definitive ranking inversion. Equal retrieval scores deliberately convey no confidence difference, and the prompt sorts graph rows, so this single run cannot establish order robustness or calibrated likelihoods. Final GPT acceptance must explicitly disambiguate inclusion of confirmed and possible candidates if this finding recurs, retain the original failure evidence, and rerun affected cases and offline checks before delivery. No production heuristic or paper-prompt change is justified by this observation alone.

Both new calls preserved old snapshots and passed state invariants without retries, edited replies or parser bypass. No live transport or parsing failure occurred, so no paid fault was manufactured; failure localization and nonempty state preservation remain covered by TODO 2.2 and the runner's offline failure tests. Two new successful rows in [`logs/llm_usage.csv`](../logs/llm_usage.csv), starting at 2026/11/09 00:44:41 (UTC+08:00), report $542$ input and $1813$ output tokens; request IDs remain blank. All $89$ offline tests pass, including expanded runner assertions for multiple-answer order, isolated states and irrelevant-residence rejection. TODO 2.3.5 must run GPT-4o-mini on the single, multi, removal and ranking cases and resolve semantic findings before TODO 2.4 delivery; TODO 2.3.6 accounting consolidation and real C/E integration remain pending.

#### Follow-up diagnosis of candidate wording

On 2026/11/09, three additional real `deepseek-flash` calls varied question wording with identical triples, scores, serialization and Figure 8 prompt, each from empty state. Private entry `python tmp/tmp_diagnose_ranking.py` captured actual synthetic prompts and allowlisted returned content before production parsing; evidence is `tmp/tmp_ranking_diagnosis.json`. Both temporary artifacts remain excluded from Git; the earlier report is preserved.

| Question control | Actual model output |
| --- | --- |
| Original “candidate birthplaces” repeated | Selected only the possible-birthplace edge and answered only `Cedar City`; unlike the earlier run, confirmed evidence was also omitted from memory |
| Explicitly include confirmed and possible birthplaces | Selected both birthplace edges, answered `["Linden City", "Cedar City"]`, excluded residence |
| Same inclusion plus confirmed-first instruction | Same selection and answer order as the preceding control |

All three raw answer arrays exactly equaled parsed answers, completions ended with `stop`, and old states stayed empty. Code inspection confirms all input edges pass the threshold and reach the request, `CASES` expectations are report-only, and no parser/report truncation occurs. The observed repeated single answer therefore originates in model output. Question-scope ambiguity is the leading explanation supported by this small comparison: “candidate” may exclude confirmed facts. Selection also varied, so this is not proof of internal reasoning or a statistical causal estimate. Membership validation cannot detect semantic incompleteness. The original repeat permanently rejects omitted confirmed evidence under the configured omission policy, illustrating a downstream risk from semantic selection despite valid state updates.

Maintained `RANKING_QUESTION` now uses only the explicit-inclusion wording, without the confirmed-first instruction or changes to the paper prompt. The second control already exercised this exact question through public MSM. Preserve the original anomaly as a fixture-wording finding, not a demonstrated ranking defect. Confirmed-first order was observed once without the added instruction, but confirmed evidence is also first after graph sorting; order robustness and GPT quality remain unverified. Three successful calls without retries append $878$ input and $1423$ output tokens, starting at 2026/11/09 00:55:51 (UTC+08:00); request IDs remain blank. TODO 2.3.5 must still validate the clarified fixture on GPT-4o-mini before delivery.

### GPT-4o-mini acceptance attempt

TODO 2.3.5 was executed on 2026/11/09 with the user's private OpenAI configuration, verified before requests as `gpt-4o-mini` at `https://api.openai.com/v1`. The delivery gate **did not pass**. No production prompt, parser, rejection policy or private configuration was changed. The following reports preserve actual outputs; their automatic semantic-review flags remain pending, and this section supplies the manual assessment.

| Run / private report | Result |
| --- | --- |
| `python scripts/validate_part_d.py --suite all`; `tmp/tmp_part_d_validation_20261108T170609405988Z.json` | Started $11$ calls, stopped on `ValueError` in `removal.exclusion_probe`; ranking had not run. The preceding ten replies passed strict parsing and state checks. |
| `python scripts/validate_part_d.py --suite removal`; `tmp/tmp_part_d_validation_20261108T170726337215Z.json` | After clarifying the conditional question, all $3$ replies passed parsing and state checks, but semantic expectations and old-memory-removal coverage failed. |
| `python scripts/validate_part_d.py --suite ranking`; `tmp/tmp_part_d_validation_20261108T170732865045Z.json` | Both replies passed parsing, state and fixture semantic review. |

Single-round sufficient evidence retained both path edges and answered `Linden City`. Insufficient evidence retained authorship, withheld the answer and asked `Where was Mira Vale born?`. Empty input withheld the answer, but its three questions included the tangential `What is The Glass Orchard about?` and a generic request for birthplace information. Initial decomposition returned `Who is the author of The Glass Orchard?` and `In which city was this author born?`; the latter is context-dependent and falls short of the paper's standalone-question instruction. Real retrieval usefulness remains unverified.

The four basic multi-round cases passed: threshold-hidden evidence remained eligible and recovered at $0.95$, useful memory persisted, the unrelated submitted triple was permanently rejected and stayed excluded, and an independent question selected that same triple. No old-memory removal occurred in this suite, as expected from its actual selections.

The original conditional fixture answered `Linden City` before publisher evidence was available. After receiving Bronze Press and Nolan Reed's birthplace, it selected nothing, permanently rejected all three submitted edges, and asked whether Silver Press published the book. The forced next probe therefore submitted an empty graph and failed strict parsing with `ValueError`. Its prior empty-memory, three-rejection snapshot remained unchanged. The report intentionally stores neither failed raw output nor exception text, so the precise validation rule that failed cannot be reconstructed from this artifact; the HTTP accounting row remains successful.

The maintained `BRANCH_QUESTION` now explicitly states that the book has exactly one publisher and requires publisher clarification before answering when unknown. This exposes the fixture assumption to the model and removes the earlier open-world ambiguity; it does not supply a desired answer or alter Figure 8. Retesting still answered `Linden City` prematurely. With publisher evidence present, it then retained all three edges, withheld an answer, and repeatedly asked who the publisher was. All snapshots and state rules passed, but no removal or permanent exclusion of removed history was observed. Wording clarification therefore **did not resolve** GPT's conditional reasoning failure. Original and clarified reports are retained; structural success must not be treated as semantic acceptance.

Both ranking cases returned `["Linden City", "Cedar City"]`. Equal-support answers were supported by both selected birthplace edges. Unequal support retained confirmed and possible birthplace edges, omitted and permanently rejected residence, and placed confirmed first without an added ordering instruction. This resolves the earlier missing-confirmed-answer observation for this clarified fixture on this run, but graph sorting also places confirmed evidence first; order robustness and calibrated ranking remain unproven.

All $16$ actual HTTP attempts returned `gpt-4o-mini-2024-07-18`, with no transport retries, manual response repair or parser bypass. The $16$ new rows in [`logs/llm_usage.csv`](../logs/llm_usage.csv) report $3523$ input and $772$ output tokens, HTTP success and nonempty request IDs; the final application-level failure in the original run does not change HTTP accounting. The paper prompt and strict validation remain intact. TODO 2.3.5 stays unchecked, and TODO 2.4 delivery remains gated. Next work must address conditional semantic behavior without hiding these failures, rerun the affected live cases and offline regression, and assess the decomposition and clarification limitations above. A production prompt extension would need to be identified as a departure from the paper template rather than silently presented as exact reproduction. TODO 2.3.6's full historical accounting consolidation and C/E integration remain separate pending work.

## Per-attempt usage accounting

[`llm_usage.py`](../src/agent/llm_usage.py) appends one UTF-8 CSV row per actual HTTP attempt, including retries and failures. Metadata records request start time, model, returned token counts, outcome, exception class and request ID. Unknown values remain blank. Credentials and request/response content are excluded. A later application validation failure does not change a successful HTTP accounting row. Configuration and accounting tests are in `tests/test_llm_configuration.py` and `tests/test_llm_usage.py`.

## Initial question decomposition

[`question_decomposition.py`](../src/agent/question_decomposition.py) implements the initial $Q_{\mathrm{sub}}$ from FoG §3.1 and Appendix C, Figure 7 (paper page 21). Both `decompose_question` and `build_decomposition_prompt` now accept the keyword-only `prompt_variant`, defaulting to `"explicit_references"`. This implementation enhancement requires independently submitted questions to repeat known entity names and full descriptions of unresolved entities, without assuming earlier answers or inventing names. Use `prompt_variant="paper"` for the unchanged paper wording with instruction numbering corrected to begin at `1`. Both variants share existing parsing, retries and accounting; neither rewrites responses or uses a pronoun blacklist.

On 2026/11/09, `python scripts/validate_part_d.py --suite reference_diagnostic` made three real calls using the unchanged GPT-4o-mini/OpenAI configuration. The paper baseline returned `In which city was this author born?`; the enhancement returned `In which city was the author of The Glass Orchard born?`. Both first asked `Who is the author of The Glass Orchard?`. Given controlled authorship evidence, unchanged MSM retained it, withheld an answer and asked `Where was Mira Vale born?`, preserving the prior state and passing state invariants. This addresses the observed reference failure; one fixture does not establish general semantic reliability. Real C/E retrieval is absent from this repository, so isolated-query recall remains an integration gate and the supplied authorship relation is not a retrieval result. Figure 8 and other semantic issues remain unchanged; TODO 2.3.5 remains pending.

The diagnostic is excluded from `all`; preview with `--suite reference_diagnostic --list-cases`. With default ranking, its conservative ceiling is four logical calls and twelve HTTP attempts under current retries. All three actual calls returned `gpt-4o-mini-2024-07-18` without retries, recording $569$ input and $87$ output tokens in three successful usage rows. Private report: `tmp/tmp_part_d_validation_20261108T174923559860Z.json`; its automatic pending-review marker is preserved, with manual findings recorded here.

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

`build_memory_prompt`, `update_memory` and `manage_memory` accept keyword-only `prompt_variant`. The default `retrieval_questions` inserts retrieval instructions before the real input; `paper` uses the unchanged Figure 8 baseline. Unknown variants raise `ValueError` before a request. The enhancement asks for missing relations along the original question's path, starting from a known entity and using named intermediate entities once established. It discourages information-availability questions and repeated known facts, and requests no further questions when evidence fully answers the query. This is an implementation enhancement, not paper text or a semantic/recall guarantee. Serialization, parsing, permanent rejection and transport retries retain their existing contracts.

Opt-in `prompt_variant="answer_ranking"` adds support comparison, presentation-order independence and answer completeness instructions to `retrieval_questions`, without changing defaults or adding a model stage. This single-call experiment failed screening and remains a comparison option. The supported enhancement is [separate answer ranking](#separate-answer-ranking), which does not require this variant.

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

For tentative answering (TODO 2.1.8), Figure 8 instructs the LLM to use only selected triples and rank multiple answers by likelihood from that evidence. The parser preserves the returned ranking, duplicates and literal entity strings, including across selected multi-hop paths and historical/new evidence. It does not sort by graph order, alphabet or triple counts. Selected-entity membership is a necessary constraint, not proof that an entity answers the question or that the ranking is correct; semantic entailment, sufficiency and relative support remain LLM responsibilities. The parser itself makes no model call; the recommended facade adds [separate answer ranking](#separate-answer-ranking).

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

### Controlled rejection and recovery validation

TODO 2.2.2 is covered by `tests/test_rejection_rounds.py` through public `manage_memory` and real `LLMClient` parsing, mocking only HTTP transport. At both default and custom cutoffs, six rounds keep below-cutoff and equal-cutoff triples unsubmitted for two rounds, then select them after their scores rise above the unchanged cutoff. A submitted omission stays rejected despite repeated high-scoring input; later removal of historical memory adds permanent rejections, which survive memory clearing and another all-blocked round. Every round independently checks the actual prompt union, exact memory, cumulative rejection and expansion masks, eligible retrieval candidates, ordered output and unchanged earlier snapshots. Each cutoff produces exactly six successful simulated accounting rows. Run `python -m unittest discover -s tests -p test_rejection_rounds.py -v` using the project environment. This controlled check makes no paid requests; real-model quality and C/E integration remain unverified.

### Controlled question-isolation validation

TODO 2.2.3 is covered by `tests/test_question_isolation.py` through public `manage_memory` and one shared real `LLMClient`, mocking only HTTP transport. Nine interleaved calls run with different query texts, then repeat with identical texts. Two runs select overlapping evidence differently, accumulate independent rejections and resume after the other run changes state. A third run created after both have nonempty memory and masks starts empty and selects triples rejected by the older runs. Every call checks the submitted evidence union, original question, ordered output, all active runs' memory, rejection and expansion masks, eligible retrieval candidates and every historical snapshot. Each scenario produces exactly nine successful simulated accounting rows. Run `python -m unittest discover -s tests -p test_question_isolation.py -v` using the project environment. Isolation requires callers to retain separate snapshots for independent runs; concurrent execution, real C/E wiring and live-model quality are not validated. No paid requests are made.

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

## Separate answer ranking

The paper requires multiple answers to be ranked by evidence support, so ranking is a default module responsibility rather than an optional caller feature. Public [`manage_memory`](../src/agent/memory_management.py) performs evidence selection, tentative answering and follow-up generation, then ranks at least two candidates using [`answer_ranking.py`](../src/agent/answer_ranking.py). To improve correctness, ranking and presentation order use a separate LLM call, supplied only the original question, actual selected triples and actual candidates. Zero or one answer skips the comparison stage.

There is no business option to disable ranking. `prompt_variant` selects only the first-stage prompt; even `paper` still ranks multiple answers separately. The old name `manage_memory_ranked` remains a compatibility alias for `manage_memory`, with identical behavior. Lower-level `update_memory` handles only first-stage prompting, parsing and state replacement for internal orchestration or prompt comparisons; it is not an alternative complete MSM interface.

This is an implementation enhancement beyond the paper's single joint MSM call. The screened instructions are preserved verbatim; tuning has stopped. Standard JSON must contain exactly `predict_answer`, with identical candidate strings and multiplicities. Only answer order may change: evidence, questions, memory and permanent rejections remain unchanged. No answer or missing path is repaired, and no gold labels, retrieval scores, relation-weight dictionary or deterministic sorting are supplied.

Both stages share the caller-owned `LLMClient`, including existing transport retries and one CSV row per attempt. Multiple candidates add one logical request, at most `2 * (max_retries + 1)` HTTP attempts per transaction, currently $6$; zero or one candidate permits at most $3$. Any stage's transport, accounting or validation error propagates without a partial `MSMResult`, business retry or fallback. Publish `result.state` only after the whole call succeeds; the caller still closes the client.

On 2026/11/09, the $14$-transaction screen passed, followed by $70$ independent real transactions and $136$ GPT-4o-mini requests without retries or exceptions. Direct cases passed all $36$ permutations/repetitions, equal-support $6/6$ and single-answer $2/2$. Two-hop ranking improved from $12/24$ upstream to $23/24$ after ranking; separate upstream omissions retained complete paths in only $17/24$. Empty-graph clarification also produced an unresolved author reference. The one ranking failure shared an identical stage-two prompt with four successes. These are retained semantic capability and stability limitations under the current model and prompt design; omissions and clarification defects are not proven to be random variation.

The user accepted these engineering results and remaining semantic limitations, stopped tuning, and authorized shared delivery and closure of the ranking investigation. The earlier zero-failure criterion is no longer required for closing this item. This does not guarantee correct ranking on arbitrary graphs or change permanent rejection. Conditional reasoning, correct old-memory removal and the overall TODO 2.3.5 gate remain separate pending work.

The default implementation replayed all $70$ recorded real transactions and $136$ stages offline, matching exact prompts, literal answers, selected evidence, questions and snapshots. Replay establishes migration parity, not fresh model-quality acceptance. `tests/test_answer_ranking.py` covers default ranking, the compatibility alias, variants, custom thresholds, skips, duplicates and literal strings, evidence isolation, strict multisets, atomic failure and mocked HTTP retry/accounting through the real client. Runner tests verify default routing for every memory round and its $25$-request ceiling; controlled expected replies actually use $15$ requests. All $100$ offline tests passed. Making ranking the default incurred no new API usage or private configuration changes. Detailed retained reports, actual usage and cost assumptions are recorded in [独立答案排序](part_d.zh-CN.md#独立答案排序); historical comparisons remain in Git history and private reports.

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

The retrieval/scoring names above illustrate caller dependencies, not implemented functions. Calling the entry point incurs LLM usage, including for empty input or an empty potential graph. `manage_memory` includes the ranking stage by default, with unchanged parameters and `MSMResult` return type; callers need no alternate entry point or ranking switch. Transport retries and accounting remain owned by `LLMClient`.

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

TODO 2.2.4 additionally validates six success/failure/recovery sequences in the same test file: timeout and HTTP 429 exhaustion, malformed outer and content JSON, an invented triple and an unknown answer entity. Each sequence first establishes memory and permanent rejection through `manage_memory`, then checks that a failed caller assignment retains the identical snapshot and retrieval eligibility. An explicit recovery call selects the failed round's candidate and newly qualifying threshold-hidden evidence, preserves prior rejection and rejects removed history only on success. Assertions check exact request payloads and timeouts, bounded retries, ordered outputs, unchanged historical snapshots and one metadata-only CSV row per attempt. Run `python -m unittest discover -s tests -p test_memory_failures.py -v` using the project environment. All scenarios are offline; real-model validation remains a separate delivery gate.

## Evidence-retention diagnostic follow-up

On 2026/11/09, $24$ real GPT-4o-mini calls isolated the clarification-array contract before testing cross-round retention. Three unchanged-prompt controls failed strict parsing with nested question arrays; explicitly requiring strings passed all three independent controls and eighteen subsequent chain transactions. Both format-only and format-plus-retention arms preserved the first-round birthplace across all three repeated chains. Neither arm answered after publisher evidence arrived: both retained all three facts and repeatedly asked for the known publisher. The retention instruction showed no additional benefit in this sample. No new rejections occurred, so the third-round high-score probe did not reproduce permanent rejection loss or establish correct old-memory removal.

All $24$ transactions replayed offline with exact prompts, errors, outputs and snapshots; CSV metadata reconciled with real responses. Actual model `gpt-4o-mini-2024-07-18`, no transport retries or ranking calls, $12870$ input and $1387$ output tokens; all $100$ offline tests passed. Production prompts, strict parsing, permanent rejection and private configuration remain unchanged. Full evidence and private artifact paths are maintained in [the diagnostic record](part_d.zh-CN.md#字符串数组对照与跨轮保留复验).

The planned $18$ independent condition-evaluation comparisons completed on 2026/11/09. Both arms kept the string-array clarification fixed; one additionally instructed publisher identification followed by branch evaluation. Each arm repeated unknown, Silver Press and Bronze Press evidence three times. All transactions passed strict parsing and state invariants. Both arms retained both birthplaces and asked only for the unknown publisher; with Silver Press both answered `Linden City` without questions; with Bronze Press both retained all three facts, gave no answer and asked again for the known publisher. The added instruction showed no benefit. Known cases retained necessary evidence plus the irrelevant birthplace; no permanent rejection occurred. Publisher selection does not establish correct condition recognition, and the internal failing step remains unknown.

All $18$ transactions replayed with exact prompts, outcomes and snapshots, with per-response CSV reconciliation: `gpt-4o-mini-2024-07-18`, $10044$ input and $1215$ output tokens, no retries or ranking calls. All $100$ offline tests passed; production and private configuration were unchanged. The independent gate failed, so sequential removal/rejection probes and instruction promotion remain deferred. The next bounded diagnostic isolates publisher extraction, requested-person identification and direct named-person birthplace lookup on both known-publisher graphs. Its precise $18$-transaction plan, conservative ranking/retry ceiling, criteria and private artifacts are maintained in [the condition comparison record](part_d.zh-CN.md#独立条件判断对照). TODO 2.3.5 remains open.

The follow-up $18$ isolated probes completed on 2026/11/09. Publisher extraction and direct named-person birthplace lookup passed $12/12$ across both known-publisher graphs. Conditional person identification passed $0/6$: Silver cases answered the city `Linden City` instead of the requested person `Mira Vale`; Bronze cases gave no answer and asked again for the known publisher. All eighteen passed strict parsing and state invariants. Person probes kept all three facts without new rejection; twelve direct-fact probes each rejected the two facts irrelevant to that independent question. Those rejections do not demonstrate harmful omission in the full conditional query or old-memory removal. The person question still mentioned birthplace goals and unknown-publisher clarification, so wording effects and internal branch evaluation remain unresolved.

Exact replay and per-response usage reconciliation passed: `gpt-4o-mini-2024-07-18`, eighteen successful HTTP requests, $9141$ input and $890$ output tokens, no retries or ranking calls. All $100$ offline tests passed; production and private configuration remain unchanged. Next, compare the current person question against a pure person-selection formulation on both graphs, three repetitions per arm and graph, before considering an explicit equality instruction or sequential removal tests. Exact wording, the twelve-transaction budget, private artifacts and acceptance criteria are maintained in [the isolated probe record](part_d.zh-CN.md#出版社回答人物与出生地隔离诊断). TODO 2.3.5 remains open.

The twelve person-target wording comparisons completed on 2026/11/09. Old wording passed semantic acceptance $0/6$: Silver answered the city, and Bronze repeatedly asked for its known publisher. Pure-person wording produced `Mira Vale` without questions in all three raw Silver replies, but two selected only the publisher and failed `answer_membership`; only one complete transaction passed. All three pure Bronze replies selected only the publisher, gave no answer and repeated the publisher question, permanently rejecting both birthplace facts within each independent person query. These omissions do not establish harmful loss in the full city question or old-memory removal. Answer membership requires entities in selected evidence, not merely submitted evidence; rule-based person selection exposes a diagnostic compatibility limit. Failed raw selections were never published or used to infer rejection.

Strict transactions passed $10/12$, with both failures preserving their empty snapshots. Exact offline replay and per-response usage reconciliation passed: twelve successful HTTP requests using `gpt-4o-mini-2024-07-18`, $6414$ input and $691$ output tokens, no retries or ranking calls. All $100$ offline tests passed; production and private configuration are unchanged. The gate remains open. Next, isolate otherwise versus explicit inequality in the same pure-person question, separately tracking raw person correctness and complete strict transaction success. Exact wording, twelve-transaction budget, interpretation limits and private artifacts are maintained in [the person-target wording record](part_d.zh-CN.md#人物目标措辞对照); sequential removal and instruction promotion remain deferred.

The twelve otherwise-versus-explicit-inequality comparisons completed on 2026/11/09, changing only the previously specified clause in the pure-person question. Each arm repeated both complete graphs three times from empty state. All six Silver raw replies answered `Mira Vale` without questions, but selected only the publisher and failed `answer_membership`; no selection or rejection was published. All six Bronze replies selected only their publisher, gave no answer and asked again about the known publisher, including Silver-specific comparison questions in two explicit-inequality cases. Each successful Bronze transaction rejected both birthplaces within its independent person query. Raw person correctness without questions was $3/6$ per arm, complete semantic acceptance $0/6$ per arm, and strict validation $6/12$ overall. All prior snapshots and successful-state invariants held. Explicit inequality showed no improvement, and internal comparison versus answer-decision failure remains unresolved. Person selection from a question rule also remains incompatible with publisher-only selected evidence under the existing answer-source contract; do not relax production validation or force unrelated birthplaces to make this probe pass.

Exact replay of all twelve transactions and per-response CSV reconciliation passed: `gpt-4o-mini-2024-07-18`, twelve successful HTTP attempts, $6348$ input and $510$ output tokens, no retries, output repair or ranking calls. All $100$ offline tests passed. Production implementation, rejection policy and private configuration are unchanged; the evidence-loss issue and TODO 2.3.5 remain open. Next, compare direct publisher extraction with conditional publisher answering, using answers supported by the selected publisher triple, before further person-branch probes. Passing that diagnostic cannot prove internal comparison because copying the publisher also solves it. Exact wording, interpretation limits, twelve-transaction budget and private artifacts are maintained in [the inequality comparison record](part_d.zh-CN.md#otherwise-与显式不等于对照). Sequential removal, unmasking and instruction promotion remain deferred.

The twelve Silver-first versus Bronze-first person-rule comparisons completed on 2026/11/09. Both complete graphs repeated each question three times from independent empty states, with all other diagnostic inputs fixed. Matching positive conditions produced the correct raw person without questions in all six calls: Silver-first/Silver answered `Mira Vale`, and Bronze-first/Bronze answered `Nolan Reed`. All selected only the publisher and failed `answer_membership`, preserving empty old states without publishing selection or rejection. Both otherwise groups gave empty answers and asked again for the known publisher in all six calls; those transactions passed structural validation and each rejected the two birthplaces irrelevant to its independent person question. Raw correctness without questions was $6/12$, strict transactions passed $6/12$, and complete semantic acceptance was $0/12$. No nonempty answer had the wrong entity type; all old snapshots and successful-state invariants held.

The raw failure moved from Bronze to Silver with the otherwise branch, while Bronze as the positive condition returned the correct person. This is an association with branch position in this sample, not proof of an internal comparison failure or a unique cause: the positive publisher and branch order changed together. Publisher-only evidence remains incompatible with the person-answer contract even when the raw person is correct. Do not relax validation or force unrelated birthplace facts. These independent rejections do not establish harmful omission in the full city query or correct old-memory removal. The evidence-loss issue and TODO 2.3.5 remain open.

All twelve transactions replayed exactly, including six validation failures; per-response CSV reconciliation passed. Actual model `gpt-4o-mini-2024-07-18`, twelve successful HTTP attempts, $6294$ input and $495$ output tokens, no retries, output repair or ranking calls. All $100$ offline tests passed; production and private configuration are unchanged. Next, compare the fixed Silver-first baseline against a version replacing only otherwise with an explicit Bronze positive condition, on both graphs with three repetitions per arm. Raw correctness and strict publication remain separate criteria. Exact wording, the twelve-transaction budget, interpretation limits and private artifacts are maintained in [the branch exchange record](part_d.zh-CN.md#silver-与-bronze-正向条件交换对照). Sequential removal, unmasking and instruction promotion remain deferred.

## Subsequent sub-questions

For TODO 2.1.9 (FoG §3.3, Eq. 11 and Appendix C), Figure 8 asks the LLM to generate one to three clarification questions when the selected evidence cannot determine the answer. `update_memory` returns these as `response.subsequent_question`, the ordered tuple representing $Q'_{\mathrm{sub}}$ for the caller's next retrieval round. Pass these strings to retrieval instead of rerunning initial question decomposition; keep `state.original_question` as the original query for subsequent memory-management calls. The retriever and inference loop remain integration work.

The parser treats empty `predict_answer` as the model declaring the answer undetermined and requires one to three questions, even with no input or selected triples. With tentative answers, zero to three questions are accepted: partial answers may need further evidence, so the fields are not mutually exclusive. This is an explicit validation convention, not a semantic sufficiency test. The LLM determines missing evidence and question wording; code does not prove relevance, infer sufficiency from graph size, invent replacement questions or make a second LLM call.

On 2026/11/09, `python scripts/validate_part_d.py --suite clarification_diagnostic` compared `paper` and default `retrieval_questions` through public MSM on empty knowledge, authorship alone and the complete authorship/birthplace path. Each of the six independent calls used identical controlled evidence at score $0.95$ and cutoff $0.7$. This diagnostic is excluded from `all`; preview it for free with `--list-cases --suite clarification_diagnostic`. With default ranking, the conservative ceiling is twelve logical calls and thirty-six HTTP attempts under current retries.

| Evidence | Paper baseline | Retrieval enhancement |
| --- | --- | --- |
| Empty | Asked for the author, publication year and whether birthplace information exists | Only `What is the name of the author of The Glass Orchard?`; empty selection and answers |
| Authorship only | Retained authorship; `Where was Mira Vale born?` | Retained authorship; `What city was Mira Vale born in?`; no repeated authorship query or answer |
| Complete path | Kept both triples; answered `Linden City`; no questions | Kept both triples; answered `Linden City`; no questions |

All six calls passed strict parsing and state invariants, preserved old snapshots and introduced no permanent rejection. Actual model `gpt-4o-mini-2024-07-18`, no retries, six successful usage rows totaling $1862$ input and $298$ output tokens. Private configuration was unchanged. Private report `tmp/tmp_part_d_validation_20261108T180609850934Z.json` retains its automatic pending-review flag; the table records manual review. All three stages passed Member D's current validation scope: simulated data and retrieval/scoring inputs with real LLM calls. The empty-graph clarification issue is resolved within that scope and removed from the issue list. C/E entry points are not implemented yet; post-merge retrieval and overall pipeline validation belong to Member E and are not prerequisites for closing this item. These results do not establish real retrieval gains or stability on other questions. Conditional reasoning and TODO 2.3.5 remain unresolved.

Question strings, whitespace, Unicode, order and duplicates are preserved. These are natural-language retrieval queries, so they need not match input entity strings or end with a particular punctuation mark. Blank/non-string items, more than three questions, or unanswered output with no questions raise `ValueError` before any new snapshot is published. Old memory and permanent rejections remain intact, with no application retry. Tests cover count boundaries with and without answers, literal output preservation, invalid-output state preservation and a controlled two-round handoff through mocked retrieval to a final answer. No live-model question quality is claimed by these offline checks.
