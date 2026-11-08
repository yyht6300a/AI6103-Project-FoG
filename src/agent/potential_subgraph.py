"""Construct FoG's potential subgraph from post-feedback triple scores.

This stateless boundary implements Section 3.3, Eq. 10. It consumes member C's
aligned triples and refined scores; it neither calls an LLM nor changes memory
or retrieval exclusions. Callers supply scores on the intended threshold scale.
"""

from collections.abc import Sequence
import math
from numbers import Real


Triple = tuple[str, str, str]


def _validate_number(value: Real, label: str) -> None:
    """Reject nonnumeric and nonfinite values before threshold comparisons.

    Args:
        value: Real scalar representable as a finite Python float; booleans
            are excluded despite being integers in Python.
        label: Fixed field name for a content-free validation error.

    Raises:
        ValueError: The value is not a supported finite real scalar.
    """
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{label} must be a finite real number")
    try:
        finite = math.isfinite(value)
    except (OverflowError, ValueError):
        finite = False
    if not finite:
        raise ValueError(f"{label} must be a finite real number")


def build_potential_subgraph(
    triples: Sequence[Sequence[str]],
    scores: Sequence[Real],
    potential_threshold: Real = 0.7,
) -> list[Triple]:
    """Keep exactly the triples whose refined scores exceed the threshold.

    Args:
        triples: Expanded-subgraph triples in score order. Each triple contains
            three nonblank strings (head, relation, tail), preserved verbatim.
        scores: One finite real scalar per triple, after member C's feedback
            propagation and re-scoring. Tensor callers must explicitly convert
            to a one-dimensional Python list before calling this boundary.
        potential_threshold: Finite real cutoff, default 0.7, on the same scale
            as scores. No sigmoid, clipping or normalization is applied.

    Returns:
        A new list of immutable triples, in first qualifying occurrence order.
        Duplicate qualifying triples occur once, matching graph-set semantics.
        Equality with the threshold is excluded; empty results are valid.
        Inputs are unchanged, and filtered triples acquire no rejection state.

    Raises:
        ValueError: Inputs are not sequences, lengths differ, a triple is
            malformed, or a score or threshold is not a finite real scalar.
            Even rows below the threshold are validated. Error messages exclude
            triple contents. No partial result is returned on invalid input.
    """
    _validate_number(potential_threshold, "Potential threshold")
    for values in (triples, scores):
        if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
            raise ValueError("Triples and scores must be sequences")
    if len(triples) != len(scores):
        raise ValueError("Triples and scores must have equal lengths")

    selected = []
    seen = set()
    for triple, score in zip(triples, scores):
        if (isinstance(triple, (str, bytes))
                or not isinstance(triple, Sequence) or len(triple) != 3
                or any(not isinstance(part, str) or not part.strip() for part in triple)):
            raise ValueError("Each triple must contain three nonblank strings")
        _validate_number(score, "Score")
        # Compare the supplied score directly: Eq. 10 specifies a strict cutoff.
        if score > potential_threshold:
            edge = tuple(triple)
            if edge not in seen:
                selected.append(edge)
                seen.add(edge)
    return selected
