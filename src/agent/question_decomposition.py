"""Produce initial Q_sub using Figure 7 or an explicit reference enhancement.

The caller owns the configured LLMClient and its lifetime. This module validates
the model's JSON list without repairing responses or changing retrieval state.
"""

import json

from src.agent.llm_client import LLMClient


# Figure 7, Appendix C, paper page 21, with instruction numbering corrected.
QUESTION_DECOMPOSITION_PROMPT = """### Task

Given a complex question, break it into a set of standalone sub-questions whose answers together resolve the original question.
1. Do not assume missing facts; add clarification sub-questions only if needed.
2. Output only sub-questions, no explanations.

### Output Format
["Sub question 1", ...]

### Real Input
Question: {question}"""


# Keep the paper template intact for reproduction and controlled comparisons.
REFERENCE_INSTRUCTIONS = """### Standalone Entity References (implementation enhancement)
Each sub-question will be sent to retrieval independently, without the other
sub-questions or their answers. Make every entity reference understandable from
that sub-question alone. Repeat the relevant entity names and full relational
descriptions from the original question instead of referring to a previous
sub-question with phrases such as "this author", "he", or "that person".
When an entity's name is unknown, use its full description anchored to a known
entity; do not invent its name or assume an earlier sub-question has been answered.
For example, for "Which city was the author of The Glass Orchard born in?", ask
"Who is the author of The Glass Orchard?" and "In which city was the author of
The Glass Orchard born?". The latter identifies the author descriptively; the
actual author name must still be resolved from retrieved evidence.

"""


def build_decomposition_prompt(question: str, *, prompt_variant: str = "explicit_references") -> str:
    """Insert the original question into the selected decomposition template.

    Args:
        question: Nonblank original natural-language question.
        prompt_variant: ``explicit_references`` adds independent-reference
            instructions by default; ``paper`` retains the original Figure 7.

    Returns:
        Prompt text with the question inserted once, preserving its characters.

    Raises:
        ValueError: The input is not a nonblank string or the variant is unknown.
    """
    if not isinstance(question, str) or not question.strip():
        raise ValueError("Question must be a nonblank string")
    if prompt_variant not in ("paper", "explicit_references"):
        raise ValueError("Unknown decomposition prompt variant")
    template = QUESTION_DECOMPOSITION_PROMPT
    if prompt_variant == "explicit_references":
        template = template.replace("### Real Input", REFERENCE_INSTRUCTIONS + "### Real Input")
    return template.format(question=question)


def parse_sub_questions(content: str) -> list[str]:
    """Validate the model's entire response as a JSON string list.

    Args:
        content: Assistant content containing only a nonempty JSON array of
            nonblank strings; surrounding JSON whitespace is permitted.

    Returns:
        Initial sub-questions in model order, preserving strings and duplicates.
        No upper limit is imposed: Figure 7 specifies none.

    Raises:
        ValueError: Content is malformed JSON, wrapped in prose or code fences,
            empty, or contains nonstring or blank entries. Response text is
            deliberately excluded from validation errors.
    """
    if not isinstance(content, str):
        raise ValueError("Sub-question content must be a string")
    try:
        questions = json.loads(content)
    except ValueError:
        raise ValueError("Sub-questions must be a JSON string list") from None
    if not isinstance(questions, list) or not questions:
        raise ValueError("Sub-questions must be a nonempty JSON string list")
    if any(not isinstance(item, str) or not item.strip() for item in questions):
        raise ValueError("Each sub-question must be a nonblank string")
    return questions


def decompose_question(
    question: str, client: LLMClient, *, prompt_variant: str = "explicit_references",
) -> list[str]:
    """Request and validate the initial sub-questions through the shared client.

    Args:
        question: Nonblank original question, validated before any API request.
        client: Caller-owned client with explicit configuration and per-attempt
            usage logging. Transport retries follow its existing policy.
        prompt_variant: Default independent-reference enhancement or ``paper``
            for the unchanged Figure 7 baseline. Selection adds no API calls.

    Returns:
        Validated initial Q_sub as an ordered list suitable for retrieval.

    Raises:
        ValueError: Question, completion envelope or JSON list is invalid.
            Refused and unfinished completions are rejected. Application
            validation never triggers extra requests or a fallback question.
        requests.RequestException: The client's transport retries are exhausted
            or a nonretryable HTTP failure occurs.
        OSError: The client's usage accounting cannot be persisted.
    """
    prompt = build_decomposition_prompt(question, prompt_variant=prompt_variant)
    response = client.complete([{"role": "user", "content": prompt}])
    try:
        choice = response["choices"][0]
        message = choice["message"]
        if choice["finish_reason"] != "stop" or message.get("refusal"):
            raise ValueError("Decomposition completion was refused or unfinished")
        content = message["content"]
    except (KeyError, IndexError, TypeError, AttributeError):
        raise ValueError("Invalid decomposition completion envelope") from None
    return parse_sub_questions(content)
