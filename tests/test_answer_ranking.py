"""Verify the separate ranking transaction without real API usage.

Exercise exact candidate preservation, evidence isolation, atomic failures and
real transport accounting with synthetic replies and private temporary files.
"""

import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

from src.agent.answer_ranking import RANKING_INSTRUCTIONS, parse_ranking
from src.agent.llm_client import LLMClient
from src.agent.llm_config import LLMConfig, ROOT
from src.agent.memory_management import manage_memory, manage_memory_ranked
from src.agent.memory_prompt import build_memory_prompt
from src.agent.memory_state import QuestionMemory


A = ('Person', 'link', 'Alpha')
B = ('Person', 'link', 'Beta')
REJECTED = ('Person', 'unrelated', 'Other')


def completion(value):
    """Build a synthetic complete, non-refused Chat Completions envelope.

    Args:
        value: Literal content or structured JSON response data.

    Returns:
        Envelope accepted by the production completion parsers.
    """
    return {'choices': [{'finish_reason': 'stop', 'message': {
        'content': value if isinstance(value, str) else json.dumps(value)}}]}


def upstream(answers):
    """Build valid stage-one evidence with a clarification for empty answers.

    Args:
        answers: Actual candidate order and multiplicity for a synthetic call.

    Returns:
        Three-field memory response selecting both relevant triples.
    """
    return completion(dict(selected_triplets=[B, A], predict_answer=answers,
                           subsequent_question=[] if answers else ['Which place?']))


class AnswerRankingTests(unittest.TestCase):
    """Check transaction contracts independently of live semantic correctness.

    Mocks and a real client with mocked HTTP transport never load private keys.
    """

    def test_success_skip_and_option_forwarding(self):
        """Keep snapshots and evidence intact for every variant and answer count.

        The ranking request sees only actual selected evidence and candidates;
        unselected and permanently rejected facts cannot enter its prompt.
        """
        self.assertIs(manage_memory_ranked, manage_memory)
        state = QuestionMemory('Where?', [A], [REJECTED])
        for variant in ('paper', 'retrieval_questions', 'answer_ranking'):
            for answers in ([], ['Alpha'], ['Beta', 'Alpha'], ['Beta', 'Alpha', 'Beta']):
                with self.subTest(variant=variant, answers=answers):
                    client = Mock(spec=LLMClient)
                    client.complete.side_effect = [upstream(answers), completion({
                        'predict_answer': list(reversed(answers))})]
                    result = manage_memory(state, [B, REJECTED], [0.8, 1],
                        client, 0.75, prompt_variant=variant)
                    self.assertEqual(result.response.predict_answer, tuple(reversed(answers)))
                    self.assertEqual(result.response.selected_triplets, (B, A))
                    self.assertEqual(result.response.subsequent_question,
                                     () if answers else ('Which place?',))
                    self.assertEqual(result.state.memory_triplets, {A, B})
                    self.assertEqual(result.state.rejected_triplets, {REJECTED})
                    self.assertEqual(state.memory_triplets, {A})
                    self.assertEqual(state.rejected_triplets, {REJECTED})
                    self.assertEqual(client.complete.call_count, 2 if len(answers) > 1 else 1)
                    self.assertEqual(client.complete.call_args_list[0].args[0], [{
                        'role': 'user', 'content': build_memory_prompt('Where?', [A], [B],
                            prompt_variant=variant)}])
                    client.close.assert_not_called()
                    if len(answers) > 1:
                        prompt = client.complete.call_args.args[0][0]['content']
                        self.assertTrue(prompt.startswith(RANKING_INSTRUCTIONS))
                        data = json.loads(prompt.removeprefix(RANKING_INSTRUCTIONS))
                        self.assertEqual(data, dict(question='Where?', selected_triplets=[list(B), list(A)],
                                                    candidate_answers=answers))

    def test_invalid_rankings_and_atomic_failures(self):
        """Reject malformed replies and never publish provisional memory.

        Structural, multiset, transport and accounting failures stop at the
        second logical call with no repair, fallback or business retry.
        """
        failures = [completion(value) for value in (
            '{}', '[]', '{"predict_answer":["Alpha","Beta"],"extra":[]}',
            '{"predict_answer":["Alpha","Beta"],"predict_answer":["Beta","Alpha"]}',
            '{"predict_answer":[NaN,"Beta"]}', '```json\n{}\n```',
            '{"predict_answer":["Alpha","Beta"]} trailing',
            *[dict(predict_answer=value) for value in (
                [], ['Alpha'], ['Alpha', 'Alpha'], ['Alpha', 'Other'],
                ['alpha', 'Beta'], ['Alpha ', 'Beta'], ['Alpha', 1],
                ['Alpha', 'Beta', 'Beta'], 'Alpha', None)],
        )]
        failures += [{}, {'choices': []}, completion(None),
            {'choices': [{'finish_reason': 'length', 'message': {'content': '{}'}}]},
            {'choices': [{'finish_reason': 'stop', 'message': {'content': '{}', 'refusal': 'refused'}}]},
            requests.Timeout(), OSError()]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                state = QuestionMemory('Where?', [A], [REJECTED])
                published = state
                client = Mock(spec=LLMClient)
                client.complete.side_effect = [upstream(['Beta', 'Alpha']), failure]
                error_type = type(failure) if isinstance(failure, Exception) else ValueError
                with self.assertRaises(error_type):
                    published = manage_memory(state, [B], [0.95], client).state
                self.assertIs(published, state)
                self.assertEqual(state.memory_triplets, {A})
                self.assertEqual(state.rejected_triplets, {REJECTED})
                self.assertEqual(client.complete.call_count, 2)
                client.close.assert_not_called()

    def test_upstream_failure_and_exact_multiset(self):
        """Reject stage-one errors before ranking and preserve literal strings.

        Duplicate candidates may only move; exact whitespace and Unicode
        strings cannot be normalized, renamed or deduplicated.
        """
        client = Mock(spec=LLMClient)
        client.complete.return_value = completion({})
        with self.assertRaises(ValueError):
            manage_memory(QuestionMemory('Where?'), [B], [0.95], client)
        client.complete.assert_called_once()
        candidates = [' Alpha ', '城市', ' Alpha ']
        self.assertEqual(parse_ranking(completion(dict(predict_answer=candidates[::-1])),
                                       candidates), tuple(candidates[::-1]))
        with self.assertRaises(ValueError):
            parse_ranking(completion(dict(predict_answer=['Alpha', 'Beta'])),
                          ['Alpha', 'Beta', 'Beta'])

    def test_real_client_accounts_both_stages_and_ranking_retry(self):
        """Use production HTTP parsing and log every stage and retry separately.

        Only the second stage receives a transient 429; its retry preserves
        the exact input and does not rerun upstream selection or publish state.
        """
        config = LLMConfig('gpt-4o-mini', 'offline-secret', 'https://test.invalid/v1', 2, 1)
        replies = [upstream(['Beta', 'Alpha']), {'error': {}},
                   completion(dict(predict_answer=['Alpha', 'Beta']))]
        responses = []
        for index, body in enumerate(replies):
            response = requests.Response()
            response.status_code = 429 if index == 1 else 200
            response._content = json.dumps(body).encode('utf-8')
            responses.append(response)
        (ROOT / 'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp', prefix='tmp_ranking_') as directory:
            path = Path(directory) / 'tmp_usage.csv'
            client = LLMClient(config, path)
            try:
                with patch('requests.adapters.HTTPAdapter.send', side_effect=responses) as send, patch(
                    'src.agent.llm_client.time.sleep',
                ):
                    result = manage_memory(QuestionMemory('Where?', [A]), [B], [0.95], client)
                    self.assertEqual(result.response.predict_answer, ('Alpha', 'Beta'))
                    self.assertEqual(send.call_count, 3)
                    self.assertEqual(send.call_args_list[1].args[0].body,
                                     send.call_args_list[2].args[0].body)
                with path.open(encoding='utf-8', newline='') as stream:
                    rows = list(csv.DictReader(stream))
                self.assertEqual([row['status'] for row in rows], ['success', 'error', 'success'])
                self.assertNotIn(config.api_key, path.read_text(encoding='utf-8'))
            finally:
                client.close()
