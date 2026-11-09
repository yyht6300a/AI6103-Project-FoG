"""Check query constraints at the public atomic publication boundary.

Saved historical replies are injected without adapting their final text.
"""

import copy
import json
import unittest
from unittest.mock import Mock

from src.agent.evidence_decision import missing_relation_queries, decision_response_format
from src.agent.memory_response import MEMORY_RESPONSE_FORMAT
from src.agent.memory_management import manage_memory
from src.agent.memory_state import QuestionMemory
from src.agent.validation_error import MemoryValidationError
from test_evidence_decision import description, envelope, LEFT, RIGHT, QUESTION
from test_memory_update import completion


class GapQueryTests(unittest.TestCase):
    """Verify deterministic candidates and rejection without state publication.

    Fake transport intentionally ignores schema so local enforcement is exercised.
    """

    def test_query_generation_and_schema(self):
        """Preserve direction, escaping, entity identity and shared defaults.

        Duplicate gaps collapse while distinct entity/relation pairs survive.
        """
        gaps = [dict(target='A"B', relation='r', direction='outgoing'),
                dict(target='A"B', relation='r', direction='incoming'),
                dict(target='C', relation='r', direction='outgoing')]
        queries = missing_relation_queries(gaps + gaps)
        self.assertEqual(len(queries), 3)
        self.assertEqual(queries[0], 'Which entity does "A\\"B" connect to through relation "r"?')
        self.assertEqual(queries[1], 'Which entity connects to "A\\"B" through relation "r"?')
        original = copy.deepcopy(MEMORY_RESPONSE_FORMAT)
        schema = decision_response_format({'missing_relations': gaps}, MEMORY_RESPONSE_FORMAT)
        self.assertEqual(schema['json_schema']['schema']['properties']['subsequent_question']['items']['enum'], queries)
        self.assertEqual(MEMORY_RESPONSE_FORMAT, original)
        self.assertIs(decision_response_format({'missing_relations': []}, MEMORY_RESPONSE_FORMAT), MEMORY_RESPONSE_FORMAT)

    def test_public_query_gate(self):
        """Reject wrong targets, directions, extras, duplicates and empty queries.

        A nonempty prior memory and rejection set survive each failure unchanged.
        """
        correct = 'Which entity does "Book" connect to through relation "publisher"?'
        state = QuestionMemory(QUESTION, [LEFT], [('Other', 'r', 'Value')])
        for questions in ([correct], ['Where was Book published?'],
                          ['Was Book published by Expected?'], [correct, 'Where was Person A born?'],
                          ['Which entity connects to "Book" through relation "publisher"?'],
                          [correct, correct], []):
            client = Mock()
            client.complete.side_effect = [envelope(description(), [LEFT, RIGHT]),
                                          completion([LEFT, RIGHT], [], questions)]
            if questions == [correct]:
                result = manage_memory(state, [RIGHT], [0.95], client, prompt_variant='evidence_decision')
                self.assertEqual(result.state.memory_triplets, {LEFT, RIGHT})
                self.assertEqual(result.state.rejected_triplets, state.rejected_triplets)
            else:
                with self.assertRaises(MemoryValidationError):
                    manage_memory(state, [RIGHT], [0.95], client, prompt_variant='evidence_decision')
            self.assertEqual(state, QuestionMemory(QUESTION, [LEFT], [('Other', 'r', 'Value')]))
            self.assertEqual(client.complete.call_count, 2)

