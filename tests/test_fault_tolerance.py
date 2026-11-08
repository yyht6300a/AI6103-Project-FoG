"""Verify recovery of threshold-hidden evidence through clarification rounds.

Controlled retrieval, refined scores and LLM completions exercise the existing
Member D boundaries together. No real retriever, model or paid API is invoked.
"""

import unittest
from unittest.mock import Mock, call

from src.agent.llm_client import LLMClient
from src.agent.memory_prompt import build_memory_prompt
from src.agent.memory_state import QuestionMemory
from src.agent.memory_update import update_memory
from src.agent.potential_subgraph import build_potential_subgraph
from test_memory_update import A, B, C, REJECTED, completion


class FaultToleranceTests(unittest.TestCase):
    """Distinguish temporary score filtering from permanent LLM rejection.

    Sub-questions drive fresh retrieval and scoring while the original question
    and historical memory continue to reach the memory-management prompt.
    """

    def test_empty_potential_recovers_below_and_equal_threshold_evidence(self) -> None:
        """Recover even when the first round submits no triples at all.

        Exercise strict cutoff equality and lower scores at default and custom
        thresholds. Empty potential must allow clarification without masking
        unseen evidence or lowering the threshold to force its admission.
        """
        for threshold in (0.7, 0.5):
            for score in (threshold - 0.1, threshold):
                with self.subTest(threshold=threshold, score=score):
                    original = "Where was Person B born?"
                    clarification = ("Which town is Person B's birthplace?",)
                    state = QuestionMemory(original)
                    retrieve = Mock(side_effect=[[C], [C]])
                    rescore = Mock(side_effect=[[score], [threshold + 0.1]])
                    client = Mock(spec=LLMClient)
                    client.complete.side_effect = [completion(questions=clarification),
                                                   completion([C], [C[2]])]

                    candidates = state.filter_expansion_triplets(retrieve((original,)))
                    potential = build_potential_subgraph(candidates, rescore(candidates), threshold)
                    self.assertEqual(potential, [])
                    first, response = update_memory(state, potential, client)
                    self.assertEqual(first.excluded_triplets, set())
                    self.assertEqual(client.complete.call_args.args[0][0]["content"],
                                     build_memory_prompt(original, [], []))

                    candidates = first.filter_expansion_triplets(
                        retrieve(response.subsequent_question))
                    self.assertEqual(candidates, [C])
                    potential = build_potential_subgraph(candidates, rescore(candidates), threshold)
                    second, response = update_memory(first, potential, client)
                    self.assertEqual(second.memory_triplets, {C})
                    self.assertEqual(second.rejected_triplets, set())
                    self.assertEqual(response.predict_answer, (C[2],))
                    self.assertEqual(response.subsequent_question, ())
                    self.assertEqual(client.complete.call_args.args[0][0]["content"],
                                     build_memory_prompt(original, [], [C]))
                    self.assertEqual(retrieve.call_args_list,
                                     [call((original,)), call(clarification)])
                    self.assertEqual(rescore.call_args_list, [call([C]), call([C])])
                    self.assertEqual(client.complete.call_count, 2)
                    self.assertEqual(state.excluded_triplets, set())

    def test_repeated_hiding_recovers_without_restoring_rejected_evidence(self) -> None:
        """Keep low-score evidence eligible across several clarification rounds.

        A submitted omission becomes permanent while unsubmitted evidence is
        repeatedly re-scored, then selected alongside retained historical memory.
        An empty new potential graph must not erase that historical evidence.
        """
        original = "Where was the leader of the country with Song C born?"
        first_questions = ("Who leads Country A?", "Where was Person B born?")
        next_questions = ("Which town is Person B's birthplace?",)
        state = QuestionMemory(original, rejected_triplets=[REJECTED])
        retrieve = Mock(side_effect=[[A, B, C, REJECTED],
                                     [B, A, C, REJECTED],
                                     [REJECTED, C, B, A]])
        rescore = Mock(side_effect=[[0.9, 0.8, 0.4], [0.7], [0.95]])
        client = Mock(spec=LLMClient)
        client.complete.side_effect = [completion([A], questions=first_questions),
                                       completion([A], questions=next_questions),
                                       completion([A, C], [C[2]])]

        candidates = state.filter_expansion_triplets(retrieve((original,)))
        potential = build_potential_subgraph(candidates, rescore(candidates))
        self.assertEqual(potential, [A, B])
        first, response = update_memory(state, potential, client)
        self.assertEqual(first.memory_triplets, {A})
        self.assertEqual(first.rejected_triplets, {B, REJECTED})
        self.assertEqual(client.complete.call_args.args[0][0]["content"],
                         build_memory_prompt(original, [], [A, B]))

        candidates = first.filter_expansion_triplets(retrieve(response.subsequent_question))
        self.assertEqual(candidates, [C])
        potential = build_potential_subgraph(candidates, rescore(candidates))
        self.assertEqual(potential, [])
        second, response = update_memory(first, potential, client)
        self.assertEqual(second.memory_triplets, {A})
        self.assertEqual(second.rejected_triplets, {B, REJECTED})
        self.assertEqual(client.complete.call_args.args[0][0]["content"],
                         build_memory_prompt(original, [A], []))

        candidates = second.filter_expansion_triplets(retrieve(response.subsequent_question))
        self.assertEqual(candidates, [C])
        potential = build_potential_subgraph(candidates, rescore(candidates))
        third, response = update_memory(second, potential, client)
        self.assertEqual(third.memory_triplets, {A, C})
        self.assertEqual(third.rejected_triplets, {B, REJECTED})
        self.assertEqual(response.predict_answer, (C[2],))
        self.assertEqual(third.filter_expansion_triplets([A, B, C, REJECTED]), [])
        self.assertEqual(client.complete.call_args.args[0][0]["content"],
                         build_memory_prompt(original, [A], [C]))
        self.assertEqual(retrieve.call_args_list,
                         [call((original,)), call(first_questions), call(next_questions)])
        self.assertEqual(rescore.call_args_list, [call([A, B, C]), call([C]), call([C])])
        self.assertEqual(client.complete.call_count, 3)
        self.assertEqual(state.excluded_triplets, {REJECTED})
