"""Hold question-local FoG memory across reasoning rounds without shared state.

Create one QuestionMemory per question run and retain its snapshot between
rounds. Persistence means in-process evidence retention, not disk storage.
LLM selection, rejection decisions and retrieval filtering belong to later
memory-management steps; this module only validates and owns their state.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from src.agent.potential_subgraph import Triple


def _freeze_triplets(triplets: Iterable[Sequence[str]]) -> frozenset[Triple]:
    """Copy a graph into immutable triples without changing literal strings.

    Args:
        triplets: Iterable of head/relation/tail sequences, each containing
            exactly three nonblank strings. Duplicate triples collapse.

    Returns:
        A detached, unordered graph snapshot preserving direction and text.

    Raises:
        ValueError: The graph or a triple is malformed. Messages omit content.
    """
    if isinstance(triplets, (str, bytes, Mapping)) or not isinstance(triplets, Iterable):
        raise ValueError("Triplets must be an iterable of triples")
    result = set()
    for triple in triplets:
        if (isinstance(triple, (str, bytes))
                or not isinstance(triple, Sequence) or len(triple) != 3
                or any(not isinstance(part, str) or not part.strip() for part in triple)):
            raise ValueError("Each triple must contain three nonblank strings")
        result.add(tuple(triple))
    return frozenset(result)


@dataclass(frozen=True)
class QuestionMemory:
    """Represent an immutable memory snapshot belonging to one question run.

    Each new instance starts empty unless explicit snapshots are supplied.
    Callers keep separate instances even for runs with identical question text;
    no global registry, question-text cache or mutable class state is used.
    Construct a new snapshot after a future validated LLM update, retaining the
    old one on failure. This container does not infer rejected triples.

    Attributes:
        original_question: Nonblank original query, preserved verbatim.
        memory_triplets: Current evidence graph, initially empty.
        rejected_triplets: Permanently masked triples for this question run,
            initially empty. Must not overlap the current evidence graph.
    """

    original_question: str
    memory_triplets: frozenset[Triple] = field(default_factory=frozenset)
    rejected_triplets: frozenset[Triple] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        """Validate and detach both graphs before publishing the snapshot.

        Constructor graph inputs may be iterables of mutable triple sequences;
        both fields become frozensets of tuples. Frozen fields and nested
        immutable values prevent accidental mutation through input aliases.

        Raises:
            ValueError: The query is blank or not a string, a graph is
                malformed, or a rejected triple is also present in memory.
        """
        if not isinstance(self.original_question, str) or not self.original_question.strip():
            raise ValueError("Original question must be a nonblank string")
        memory = _freeze_triplets(self.memory_triplets)
        rejected = _freeze_triplets(self.rejected_triplets)
        if memory & rejected:
            raise ValueError("Memory and rejected triplets must be disjoint")
        object.__setattr__(self, "memory_triplets", memory)
        object.__setattr__(self, "rejected_triplets", rejected)
