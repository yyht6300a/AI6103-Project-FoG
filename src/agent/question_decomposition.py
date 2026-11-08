"""Produce the initial Q_sub for FoG retrieval using the paper's Figure 7.

The caller owns the configured LLMClient and its lifetime. This module validates
the model's JSON list without repairing responses or changing retrieval state.
"""

import json

from src.agent.llm_client import LLMClient


# Figure 7, Appendix C, paper page 21. Preserve the printed numbering (2, 3).
QUESTION_DECOMPOSITION_PROMPT = """### Task

Given a complex question, break it into a set of standalone sub-questions whose answers together resolve the original question.
2. Do not assume missing facts; add clarification sub-questions only if needed.
3. Output only sub-questions, no explanations.

### Output Format
["Sub question 1", ...]

### Real Input
Question: {question}"""


def build_decomposition_prompt(question: str) -> str:
    """Insert the original question into Figure 7 without rewriting it.

    Args:
        question: Nonblank original natural-language question.

    Returns:
        Prompt text with the question inserted once, preserving its characters.

    Raises:
        ValueError: The input is not a nonblank string.
    """
    if not isinstance(question, str) or not question.strip():
        raise ValueError("Question must be a nonblank string")
    return QUESTION_DECOMPOSITION_PROMPT.format(question=question)


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


def decompose_question(question: str, client: LLMClient) -> list[str]:
    """Request and validate the initial sub-questions through the shared client.

    Args:
        question: Nonblank original question, validated before any API request.
        client: Caller-owned client with explicit configuration and per-attempt
            usage logging. Transport retries follow its existing policy.

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
    prompt = build_decomposition_prompt(question)
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
