"""Coordinate LLM-guided memory replacement from FoG Section 3.3, Eq. 12.

Each round submits historical evidence alongside the eligible potential graph.
Only a completely validated completion produces a new question-local snapshot;
the caller retains its old snapshot if any step raises.
"""

from collections.abc import Iterable, Sequence

from src.agent.validation_error import MemoryValidationError
from src.agent.llm_client import LLMClient
from src.agent.memory_prompt import build_memory_prompt
from src.agent.memory_response import MemoryResponse, parse_memory_response
from src.agent.memory_state import QuestionMemory, _freeze_triplets


def update_memory(
    state: QuestionMemory,
    potential_triplets: Iterable[Sequence[str]],
    client: LLMClient,
    *,
    prompt_variant: str = "retrieval_questions",
) -> tuple[QuestionMemory, MemoryResponse]:
    """Request a high-recall selection and strictly replace historical memory.

    Args:
        state: Immutable snapshot belonging to the current question run.
        potential_triplets: Eligible new graph, with three nonblank strings per
            head/relation/tail sequence. Empty graphs and generators are valid;
            previously rejected triples must already be excluded by the caller.
        client: Caller-owned configured client, including transport retries and
            per-attempt usage accounting. This function does not close it.
        prompt_variant: Retrieval-question enhancement by default; ``paper``
            retains Figure 8 for controlled comparisons without extra requests.

    Returns:
        New snapshot and validated structured response. The new evidence set is
        exactly the selected triples, deduplicated by set identity, even if empty.
        Old evidence survives only when selected again. The original query and
        existing permanent rejection set are preserved. Submitted triples absent
        from the validated selection are permanently rejected for this run,
        including omitted historical memory. Unsubmitted evidence is unaffected.
        result.subsequent_question carries ordered next-round retrieval queries;
        unanswered output requires one to three. Retrieval remains caller-owned.

    Raises:
        ValueError: State, potential graph, completion envelope or any structured
            output field is invalid. Rejected potential input fails before a
            request. Refused and unfinished completions fail without repair.
        requests.RequestException: The shared client exhausts transport retries
            or encounters a nonretryable HTTP error.
        OSError: Usage accounting fails. All failures preserve the old snapshot;
            application validation adds no retry or provider fallback. One-shot
            potential iterators are consumed even if a later step fails.
    """
    if not isinstance(state, QuestionMemory):
        raise ValueError("State must be a QuestionMemory snapshot")
    # Materialize once so the prompt and parser see identical submitted evidence.
    potential = _freeze_triplets(potential_triplets)
    if potential & state.rejected_triplets:
        raise ValueError("Potential graph must exclude permanently rejected triples")
    prompt = build_memory_prompt(
        state.original_question, state.memory_triplets, potential, prompt_variant=prompt_variant,
    )
    completion = client.complete([{"role": "user", "content": prompt}])
    try:
        choice = completion["choices"][0]
        message = choice["message"]
        if message.get("refusal"):
            raise MemoryValidationError("completion_refused")
        if choice["finish_reason"] != "stop":
            raise MemoryValidationError("completion_unfinished")
        content = message["content"]
    except (KeyError, IndexError, TypeError, AttributeError):
        raise MemoryValidationError("completion_envelope") from None
    result = parse_memory_response(content, state.memory_triplets, potential)
    # User-approved omission criterion: only evidence actually submitted counts.
    selected = frozenset(result.selected_triplets)
    rejected = state.rejected_triplets | ((state.memory_triplets | potential) - selected)
    updated = QuestionMemory(
        state.original_question, selected, rejected,
    )
    return updated, result
