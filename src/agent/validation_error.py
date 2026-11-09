"""Expose fixed, payload-free diagnostics for memory output validation.

Codes and stages are allowlisted here so reports never copy arbitrary exception
messages or model output. The exception remains compatible with ValueError.
"""


class MemoryValidationError(ValueError):
    """Identify one strict validation failure without retaining response text.

    Attributes:
        code: Stable machine-readable validation identifier.
        stage: Fixed boundary at which validation failed.
    """

    def __init__(self, code: str) -> None:
        """Create an error from the closed diagnostic registry.

        Args:
            code: Registered identifier; unknown values are rejected.
        """
        self.stage = ERROR_STAGES[code]
        self.code = code
        messages = {
            "selected_membership": "Selected triplets must match submitted triples exactly",
            "questions_required": "Unanswered memory output requires subsequent questions",
        }
        super().__init__(messages.get(code, code))


ERROR_STAGES = {
    "decision_queries": "evidence_decision",
    "decision_query_coverage": "evidence_decision",
    "decision_condition": "evidence_decision",
    "decision_conflict": "evidence_decision",
    "decision_unsupported": "evidence_decision",
    "decision_evidence_id": "evidence_decision",
    "decision_targets": "evidence_decision",
    "decision_target_membership": "evidence_decision",
    "decision_completion": "evidence_decision",
    "decision_fields": "evidence_decision",
    "decision_status": "evidence_decision",
    "decision_triplets": "evidence_decision",
    "decision_membership": "evidence_decision",
    "decision_strings": "evidence_decision",
    "decision_condition_retention": "evidence_decision",
    "decision_required_omitted": "decision_consistency",
    "decision_unknown_answer": "decision_consistency",
    "decision_answer_required": "decision_consistency",
    "decision_unnecessary_questions": "decision_consistency",
    "content_type": "content",
    "duplicate_fields": "json",
    "nonstandard_json": "json",
    "invalid_json": "json",
    "required_fields": "fields",
    "selected_array": "selection",
    "selected_shape": "selection",
    "selected_membership": "selection",
    "answer_array": "answers",
    "answer_membership": "answers",
    "question_array": "questions",
    "question_limit": "questions",
    "questions_required": "questions",
    "completion_refused": "envelope",
    "completion_unfinished": "envelope",
    "completion_envelope": "envelope",
}


def failure_diagnostics(error: Exception) -> dict:
    """Return safe metadata only for recognized validation exceptions.

    Args:
        error: Terminal exception; arbitrary messages are never inspected.

    Returns:
        Fixed stage and code for known failures, or explicit unclassified values.
    """
    if type(error) is MemoryValidationError and error.code in ERROR_STAGES:
        return {"failure_stage": ERROR_STAGES[error.code], "error_code": error.code}
    return {"failure_stage": "unclassified", "error_code": "unclassified"}
