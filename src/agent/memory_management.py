"""Expose one scored-round MSM boundary for members C and E.

The caller owns retrieval, scoring, client lifetime and snapshot publication.
This entry point composes existing threshold, exclusion and LLM update rules;
it does not load configuration, run a retrieval loop or mutate question state.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from numbers import Real

from src.agent.llm_client import LLMClient
from src.agent.answer_ranking import build_ranking_prompt, parse_ranking
from src.agent.memory_response import MemoryResponse
from src.agent.memory_state import QuestionMemory
from src.agent.memory_update import update_memory
from src.agent.potential_subgraph import build_potential_subgraph


@dataclass(frozen=True)
class MSMResult:
    """Group a validated round's next snapshot and ordered model output.

    Instances returned by manage_memory contain immutable nested values.
    Direct construction is a data-container operation, not output validation.

    Attributes:
        state: Next QuestionMemory, exposing memory_triplets, rejected_triplets
            and excluded_triplets for the next retrieval/expansion round.
        response: Validated selected_triplets, ranked predict_answer and ordered
            subsequent_question tuples, preserving model order and duplicates.
    """

    state: QuestionMemory
    response: MemoryResponse


def _manage_memory_once(
    state: QuestionMemory,
    triples: Sequence[Sequence[str]],
    scores: Sequence[Real],
    client: LLMClient,
    potential_threshold: Real = 0.7,
    *,
    prompt_variant: str = "retrieval_questions",
) -> MSMResult:
    """Build the internal single-call selection result before answer ranking.

    Args:
        state: Current question-local immutable snapshot. Create a fresh
            QuestionMemory for each independent question run.
        triples: Expanded head/relation/tail rows with three nonblank strings.
            Empty sequences are valid; all literal characters are retained.
        scores: Positionally aligned finite real scalars after C's feedback
            propagation. Convert tensors explicitly to one-dimensional lists.
        client: Caller-owned LLMClient; configuration and closing stay external.
        potential_threshold: Finite real strict cutoff, default 0.7. Callers
            loading YAML must pass its potential_threshold explicitly.
        prompt_variant: Default ``retrieval_questions``, ``paper`` baseline,
            or opt-in ``answer_ranking``, forwarded unchanged to the builder.

    Returns:
        New state and structured output after complete validation. All input
        rows and scores are validated before exclusions, without normalization.
        Exact duplicate rows qualify if any occurrence exceeds the cutoff.
        Current memory and rejected rows are removed from new potential input;
        historical memory still reaches the LLM independently of current scores.
        Only submitted omissions gain permanent rejection. The caller publishes
        result.state on success and passes response.subsequent_question onward.

    Raises:
        ValueError: Invalid state, aligned input, threshold or LLM output.
        requests.RequestException: Transport retries are exhausted or an HTTP
            error is nonretryable, as defined by LLMClient.
        OSError: Usage accounting fails. Every failure leaves the input snapshot
            unchanged and returns no result. No application retry or provider
            fallback is added; a valid empty graph still triggers an LLM call.
    """
    if not isinstance(state, QuestionMemory):
        raise ValueError("State must be a QuestionMemory snapshot")
    # Validate aligned pairs before filtering; never deduplicate scored inputs.
    potential = build_potential_subgraph(triples, scores, potential_threshold)
    eligible = state.filter_expansion_triplets(potential)
    updated, response = update_memory(state, eligible, client, prompt_variant=prompt_variant)
    return MSMResult(updated, response)


def manage_memory(
    state: QuestionMemory,
    triples: Sequence[Sequence[str]],
    scores: Sequence[Real],
    client: LLMClient,
    potential_threshold: Real = 0.7,
    *,
    prompt_variant: str = "retrieval_questions",
) -> MSMResult:
    """Complete an atomic memory round with a separate answer-ranking request.

    Answer ranking is required by the paper and always enabled here. To improve
    correctness, ranking uses a separate request after selection and answering.
    Zero or one candidate skips that request because its order is unambiguous.

    Args:
        state: Immutable question-local snapshot, published by the caller only
            after the entire transaction succeeds.
        triples: Expanded head/relation/tail rows passed unchanged to MSM.
        scores: Positionally aligned finite refined scores passed to MSM.
        client: Caller-owned client shared by both stages; never closed here.
        potential_threshold: Strict cutoff forwarded unchanged to MSM.
        prompt_variant: Stage-one prompt variant; even ``paper`` retains the
            mandatory separate ranking stage for multiple candidate answers.

    Returns:
        MSMResult with stage-one state, selected evidence and questions unchanged.
        Only answer order may change; literal strings and multiplicity survive.
        No answer, path or rejection is repaired by the ranking stage.

    Raises:
        ValueError: Either stage fails input, envelope or structured validation.
        requests.RequestException: Transport retries are exhausted.
        OSError: Usage accounting fails. Any failure returns no partial result
            and preserves the original snapshot. There is no business retry;
            each stage uses the client's existing transport and logging policy.
    """
    provisional = _manage_memory_once(state, triples, scores, client,
        potential_threshold, prompt_variant=prompt_variant)
    response = provisional.response
    if len(response.predict_answer) < 2:
        return provisional
    prompt = build_ranking_prompt(state.original_question, response.selected_triplets,
                                  response.predict_answer)
    ranked = parse_ranking(client.complete([{"role": "user", "content": prompt}]),
                           response.predict_answer)
    return MSMResult(provisional.state, replace(response, predict_answer=ranked))


# Compatibility name for callers of the earlier opt-in facade. Both names now
# execute mandatory ranking; this alias does not expose a behavior switch.
manage_memory_ranked = manage_memory
