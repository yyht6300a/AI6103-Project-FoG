"""Parse the structured Figure 8 output against the submitted knowledge graph.

This offline boundary validates syntax, field types and literal input membership,
including answer membership in selected evidence. Semantic sufficiency and ranking
remain LLM responsibilities; parsing neither calls the LLM nor publishes updates.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import json

from src.agent.validation_error import MemoryValidationError
from src.agent.memory_state import _freeze_triplets
from src.agent.potential_subgraph import Triple


@dataclass(frozen=True)
class MemoryResponse:
    """Carry the three output fields as immutable, ordered values.

    Use parse_memory_response to obtain a validated instance. Direct construction
    only stores values; it does not validate against a submitted input graph.

    Attributes:
        selected_triplets: Exact head/relation/tail tuples in model order.
        predict_answer: Literal selected-evidence entities in model ranking order.
        subsequent_question: Ordered next-round queries; one to three if unanswered.
    """

    selected_triplets: tuple[Triple, ...]
    predict_answer: tuple[str, ...]
    subsequent_question: tuple[str, ...]


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Decode JSON objects while rejecting ambiguous duplicate field names.

    Args:
        pairs: Object entries supplied by the JSON decoder in source order.

    Returns:
        Object with one value per field name, without copying nested values.

    Raises:
        ValueError: A field name occurs more than once; content is not disclosed.
    """
    result = {}
    for key, value in pairs:
        if key in result:
            raise MemoryValidationError("duplicate_fields")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    """Reject nonstandard JSON numbers accepted by Python's default decoder.

    Args:
        value: Decoder token for NaN or positive/negative Infinity, not logged.

    Raises:
        ValueError: Always, because Figure 8 requires standard JSON.
    """
    raise MemoryValidationError("nonstandard_json")


def _string_array(value: object, code: str) -> tuple[str, ...]:
    """Validate an output string array without normalizing its values.

    Args:
        value: Decoded answer or clarification field; an empty array is valid.
        code: Fixed field-specific diagnostic identifier.

    Returns:
        Immutable strings preserving order, whitespace and duplicates.

    Raises:
        ValueError: The field is not an array of nonblank strings.
    """
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise MemoryValidationError(code)
    return tuple(value)


def parse_memory_response(
    content: str,
    memory_triplets: Iterable[Sequence[str]],
    potential_triplets: Iterable[Sequence[str]],
) -> MemoryResponse:
    """Validate assistant content against the exact graph supplied to Figure 8.

    Args:
        content: Entire assistant text containing one JSON object with exactly
            selected_triplets, predict_answer and subsequent_question arrays.
        memory_triplets: Historical graph actually submitted to the LLM, with
            three nonblank strings per head/relation/tail sequence.
        potential_triplets: Potential graph actually submitted in the same call.
            Either graph may be empty or a one-shot iterable.

    Returns:
        Detached immutable output preserving model order, duplicates and literal
        strings. Selected triples must occur in the submitted graph union; answers
        must equal a selected triple's head or tail, not merely a relation or
        substring. Input entities occurring only in unselected triples fail.
        Clarification count is zero to three, but an empty answer array requires
        at least one question. This treats no answer as declared undetermined;
        nonempty tentative answers may still accompany clarification questions.
        Figure 8 instructs evidence-based ranking; the parser preserves that
        order without claiming to verify entailment or relative support.
        Triple identity compares decoded strings at fixed head/relation/tail
        positions, without trimming, case folding or Unicode normalization.
        Equivalent JSON escapes are valid; selected row order is unrestricted.
        External reasoning that returns an otherwise valid input entity cannot
        be detected by membership checks; Figure 8 supplies that instruction.

    Raises:
        ValueError: Inputs, JSON, field names, types or membership are invalid.
            Prose, fences, duplicate fields, nonstandard numbers and trailing
            values are rejected without repair or disclosure of input/output
            text. No API request or memory/rejection mutation occurs. Supplied
            one-shot iterators are consumed, even if output validation fails.
    """
    knowledge = _freeze_triplets(memory_triplets) | _freeze_triplets(potential_triplets)
    if not isinstance(content, str):
        raise MemoryValidationError("content_type")
    try:
        output = json.loads(content, object_pairs_hook=_unique_object,
                            parse_constant=_reject_constant)
    except MemoryValidationError:
        raise
    except (ValueError, RecursionError):
        raise MemoryValidationError("invalid_json") from None
    fields = {"selected_triplets", "predict_answer", "subsequent_question"}
    if not isinstance(output, dict) or set(output) != fields:
        raise MemoryValidationError("required_fields")

    selected = output["selected_triplets"]
    if not isinstance(selected, list):
        raise MemoryValidationError("selected_array")
    # Reuse graph validation without losing the model's row order or duplicates.
    try:
        selected_graph = _freeze_triplets(selected)
    except ValueError:
        raise MemoryValidationError("selected_shape") from None
    if not selected_graph <= knowledge:
        raise MemoryValidationError("selected_membership")
    answers = _string_array(output["predict_answer"], "answer_array")
    entities = {entity for head, _, tail in selected_graph for entity in (head, tail)}
    if any(answer not in entities for answer in answers):
        raise MemoryValidationError("answer_membership")
    questions = _string_array(output["subsequent_question"], "question_array")
    if len(questions) > 3:
        raise MemoryValidationError("question_limit")
    if not answers and not questions:
        raise MemoryValidationError("questions_required")
    return MemoryResponse(tuple(tuple(triple) for triple in selected), answers, questions)
