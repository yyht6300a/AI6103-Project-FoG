"""Check fixed diagnostics through the public MSM reporting boundary.

All completions are synthetic; no credentials, HTTP calls or paid usage occur.
"""

import json
import unittest
from unittest.mock import Mock

from scripts import validate_part_d as validation
from src.agent.memory_state import QuestionMemory
from src.agent.validation_error import failure_diagnostics


class ValidationDiagnosticsTests(unittest.TestCase):
    """Distinguish malformed replies while preserving empty question state.

    Reports must contain only allowlisted metadata for failed responses.
    """

    def test_errors_have_distinct_safe_codes(self):
        """Exercise JSON, field, selection, answer and question failures.

        Each failed public call leaves its original snapshot unchanged and does
        not copy the sensitive marker from invalid content into the report.
        """
        valid = dict(selected_triplets=[], predict_answer=[], subsequent_question=['Who wrote it?'])
        cases = [
            ('sensitive-marker', 'json', 'invalid_json'),
            ('{"x":1,"x":2}', 'json', 'duplicate_fields'),
            ('{"x":NaN}', 'json', 'nonstandard_json'),
            ('{}', 'fields', 'required_fields'),
            (json.dumps(dict(valid, selected_triplets='sensitive-marker')), 'selection', 'selected_array'),
            (json.dumps(dict(valid, selected_triplets=[['bad']])), 'selection', 'selected_shape'),
            (json.dumps(dict(valid, selected_triplets=[['a', 'r', 'b']])), 'selection', 'selected_membership'),
            (json.dumps(dict(valid, predict_answer='sensitive-marker')), 'answers', 'answer_array'),
            (json.dumps(dict(valid, predict_answer=['sensitive-marker'])), 'answers', 'answer_membership'),
            (json.dumps(dict(valid, subsequent_question=[None])), 'questions', 'question_array'),
            (json.dumps(dict(valid, subsequent_question=['q'] * 4)), 'questions', 'question_limit'),
            (json.dumps(dict(valid, subsequent_question=[])), 'questions', 'questions_required'),
            (None, 'content', 'content_type'),
        ]
        for content, stage, code in cases:
            with self.subTest(code=code):
                client = Mock()
                client.complete.return_value = {'choices': [{'finish_reason': 'stop', 'message': {'content': content}}]}
                state = QuestionMemory(validation.BRANCH_QUESTION)
                report = {'plan': validation.case_plan('empty_diagnostic'), 'results': []}
                with self.assertRaises(ValueError):
                    validation.memory_round(client, report, 'empty_diagnostic.probe', state, [], [], ())
                failed = report['results'][0]
                self.assertEqual((failed['failure_stage'], failed['error_code']), (stage, code))
                self.assertEqual(failed['state_before'], failed['state_after'])
                self.assertNotIn('sensitive-marker', json.dumps(report))
                client.complete.assert_called_once()

    def test_envelope_failures_and_unknown_errors(self):
        """Classify envelope checks and redact arbitrary exception messages.

        Refusal and unfinished completions remain separate from missing fields.
        """
        for body, code in [({}, 'completion_envelope'),
                           ({'choices': [{'finish_reason': 'length', 'message': {}}]}, 'completion_unfinished'),
                           ({'choices': [{'finish_reason': 'stop', 'message': {'refusal': 'private'}}]}, 'completion_refused')]:
            client = Mock()
            client.complete.return_value = body
            report = {'plan': validation.case_plan('empty_diagnostic'), 'results': []}
            with self.assertRaises(ValueError):
                validation.memory_round(client, report, 'empty_diagnostic.probe',
                                        QuestionMemory(validation.BRANCH_QUESTION), [], [], ())
            self.assertEqual(report['results'][0]['error_code'], code)
        self.assertEqual(failure_diagnostics(ValueError('private')),
                         {'failure_stage': 'unclassified', 'error_code': 'unclassified'})
