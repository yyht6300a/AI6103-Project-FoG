"""Build FoG memory-management input from Appendix C, Figure 8.

This offline boundary combines historical evidence and the potential subgraph
into the figure's single Knowledge field. It neither calls the LLM nor changes
question-local memory; response validation and state transitions come later.
"""

from collections.abc import Iterable, Sequence
import json

from src.agent.memory_state import _freeze_triplets


# Figure 8, Appendix C, paper page 21. Double braces escape the JSON example.
MEMORY_MANAGEMENT_PROMPT = """### Task
Given a question and several knowledge triples ("subject","relation","object"):
1. Select relevant triples (high recall).
2. Using only selected triples, predict the answer; if undetermined, ask 1–3 clarification questions.

### Principles
1. Keep a triple unless you’re confident it’s irrelevant.
2. Use only supported evidence; if multiple answers, rank by likelihood from triples.

### Constraints
1. Select only input triples/entities.
2. No external knowledge; no fabrication; no rewriting/creating/reordering/merging/splitting triples.
3. Selected triples must match input exactly.
4. Answers must be entity strings appearing in input triples exactly as-is.

### Output Format
{{
  "selected_triplets":[],
  "predict_answer":[],
  "subsequent_question":[]
}}

### Real Input
Question: {question}
Knowledge: {triplets}"""


def build_memory_prompt(
    question: str,
    memory_triplets: Iterable[Sequence[str]],
    potential_triplets: Iterable[Sequence[str]],
) -> str:
    """Insert the original query and both evidence graphs into Figure 8.

    Args:
        question: Nonblank original question, preserved verbatim.
        memory_triplets: Historical memory as an iterable of head/relation/tail
            sequences containing exactly three nonblank strings each.
        potential_triplets: Newly discovered potential graph with the same
            contract. Either graph may be empty, unordered or a generator.

    Returns:
        Prompt with the exact graph union encoded as a Unicode JSON array of
        triples. Duplicate triples collapse; lexicographic tuple sorting makes
        serialization stable even for unordered snapshots. Sorting changes only
        presentation order, never head/relation/tail positions or string values.
        Mutable containers are not changed; one-shot iterators are consumed.

    Raises:
        ValueError: The question or either graph is malformed. Validation errors
            exclude input text. No API request or state mutation occurs.
    """
    if not isinstance(question, str) or not question.strip():
        raise ValueError("Original question must be a nonblank string")
    memory = _freeze_triplets(memory_triplets)
    potential = _freeze_triplets(potential_triplets)
    knowledge = json.dumps(sorted(memory | potential), ensure_ascii=False)
    return MEMORY_MANAGEMENT_PROMPT.format(question=question, triplets=knowledge)
