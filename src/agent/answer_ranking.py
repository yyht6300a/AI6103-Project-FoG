"""Rank existing answers using fixed selected evidence in a separate LLM call.

This implementation enhancement retains the screened prompt verbatim. It only
reorders existing candidate strings; evidence selection remains upstream.
"""

from collections import Counter
from collections.abc import Sequence
import json

from src.agent.memory_response import _unique_object, _reject_constant


RANKING_INSTRUCTIONS = (
    '### Fixed Evidence Answer Ranking (implementation enhancement)\n'
    'Using only the selected triples, order the supplied candidate answers from '
    'stronger to weaker support for the original question. Compare their support '
    'before ordering. Evidence row order, candidate order, spelling and alphabetical '
    'order are not evidence of likelihood. If support is equal, either relative '
    'order is acceptable. Return exactly a JSON object with one field '
    '"predict_answer": an array containing every supplied candidate string exactly '
    'as supplied, with exactly the same multiplicity. Only reorder candidates; '
    'do not add, omit, rename or deduplicate answers, modify evidence, select '
    'triples or generate questions. Return no explanation or Markdown.\n'
)


def build_ranking_prompt(
    question: str, selected: Sequence[Sequence[str]], answers: Sequence[str],
) -> str:
    """Serialize only the actual upstream selection and candidate answers.

    Args:
        question: Original question without rewriting.
        selected: Validated selected triples in the model's returned row order.
        answers: Validated candidate strings, including their order and duplicates.

    Returns:
        Frozen instructions followed by JSON, without gold labels or scores.
    """
    return RANKING_INSTRUCTIONS + json.dumps(dict(question=question,
        selected_triplets=selected, candidate_answers=answers), ensure_ascii=False)


def parse_ranking(completion: dict, answers: Sequence[str]) -> tuple[str, ...]:
    """Validate ranking output without repairing strings or answer multiplicity.

    Args:
        completion: Unmodified Chat Completions envelope from the shared client.
        answers: Actual upstream candidates, including any duplicate strings.

    Returns:
        Model-ordered immutable candidates with the identical input multiset.

    Raises:
        ValueError: Completion is refused, unfinished, malformed, nonstandard
            JSON, has extra fields, or changes any candidate or its multiplicity.
    """
    try:
        choice = completion['choices'][0]
        message = choice['message']
        if choice['finish_reason'] != 'stop' or message.get('refusal'):
            raise ValueError('Ranking completion refused or unfinished')
        value = json.loads(message['content'], object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant)
    except (KeyError, IndexError, TypeError, AttributeError, RecursionError):
        raise ValueError('Invalid ranking completion') from None
    if not isinstance(value, dict) or set(value) != {'predict_answer'}:
        raise ValueError('Invalid ranking fields')
    ranked = value['predict_answer']
    if (not isinstance(ranked, list)
            or any(not isinstance(item, str) or not item.strip() for item in ranked)
            or Counter(ranked) != Counter(answers)):
        raise ValueError('Invalid ranking candidate multiset')
    return tuple(ranked)
