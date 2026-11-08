"""Expose one scored-round MSM boundary for members C and E.

The caller owns retrieval, scoring, client lifetime and snapshot publication.
This entry point composes existing threshold, exclusion and LLM update rules;
it does not load configuration, run a retrieval loop or mutate question state.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from numbers import Real

from src.agent.llm_client import LLMClient
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


def manage_memory(
    state: QuestionMemory,
    triples: Sequence[Sequence[str]],
    scores: Sequence[Real],
    client: LLMClient,
    potential_threshold: Real = 0.7,
    *,
    prompt_variant: str = "retrieval_questions",
) -> MSMResult:
    """Process member C's aligned refined triples in one memory round.

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
        prompt_variant: Default retrieval-question enhancement or ``paper``
            baseline, forwarded unchanged to the memory prompt builder.

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
