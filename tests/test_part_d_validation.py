"""Check the paid acceptance runner without contacting a provider.

The runner uses production public APIs and parsing; only configuration, report
location and HTTP transport are substituted with isolated synthetic fixtures.
"""

from contextlib import redirect_stdout
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

from scripts import validate_part_d as validation
from src.agent.llm_client import LLMClient
from src.agent.llm_config import LLMConfig, ROOT
from test_memory_update import completion


class PartDValidationTests(unittest.TestCase):
    """Verify call bounds, safe reporting and cleanup at the executable boundary.

    Reports and usage rows stay inside private project tmp directories. No test
    loads the user's dotenv or writes to the shared usage CSV.
    """

    def ranking_envelopes(self, unequal=False):
        """Build synthetic independent requirements, selection and sorting.

        Args:
            unequal: Use one subject with two independently supported relations.

        Returns:
            Three mocked completions; these are not recovered live replies.
        """
        v = validation
        rows = [v.CONFIRMED_BIRTH, v.POSSIBLE_BIRTH] if unequal else [v.BIRTH, v.OTHER_BIRTH]
        graph = sorted(rows + ([v.RESIDENCE] if unequal else []))
        groups = []
        for entity in dict.fromkeys(t[0] for t in rows):
            groups.append({"entity": entity, "fact_queries": [
                {"relation": t[1], "binding": {"kind": "observed", "evidence_ids": [graph.index(t)]}}
                for t in rows if t[0] == entity]})
        first = completion()
        first["choices"][0]["message"]["content"] = json.dumps({"description": {
            "kind": "nonconditional", "branch": {"requirements": groups}}})
        answers = [v.BIRTH[2], v.OTHER_BIRTH[2]]
        last = completion()
        last["choices"][0]["message"]["content"] = json.dumps({"predict_answer": answers})
        return [first, completion(rows, answers, []), last]

    def conditional_chain_envelopes(self, value, active, direction="outgoing",
                                    condition_relation="published_by"):
        """Build the three conditional rounds for one publisher chain.

        Args:
            value: Publisher value submitted by this chain.
            active: Birthplace triple selected by the activated branch.
            direction: Direction declared for an absent birthplace fact.
            condition_relation: Condition relation declared by the extraction.

        Returns:
            Six mocked completions: extraction then selection for each of the
            unknown, resolved and probe rounds.
        """
        v = validation
        envelopes = []
        for stage in ("unknown", "resolved", "probe"):
            publisher = (*v.PUBLISHER[:2], value)
            graph = sorted([v.BIRTH, v.OTHER_BIRTH] if stage == "unknown"
                           else [publisher, active] if stage == "probe"
                           else [publisher, v.BIRTH, v.OTHER_BIRTH])
            branches = {}
            for case, birth in (("true", v.BIRTH), ("false", v.OTHER_BIRTH)):
                binding = ({"kind": "observed", "evidence_ids": [graph.index(birth)]}
                           if birth in graph else {"kind": "missing", "direction": direction})
                branches[case] = {"requirements": [{"entity": birth[0], "fact_queries": [
                    {"relation": "born_in", "binding": binding}]}]}
            extraction = completion()
            extraction["choices"][0]["message"]["content"] = json.dumps({"description": {
                "kind": "conditional_equals",
                "condition": {"subject": v.PUBLISHER[0], "relation": condition_relation,
                              "operator": "eq", "value": v.CONDITION_VALUE,
                              "uniqueness_quote": "The Glass Orchard has exactly one publisher.",
                              "condition_quote": "If it was published by Silver Press"},
                "branches": branches,
            }})
            selection = completion(
                [v.BIRTH, v.OTHER_BIRTH] if stage == "unknown" else [publisher, active],
                [] if stage == "unknown" else [active[2]],
                ['Which entity does "The Glass Orchard" connect to through relation "published_by"?']
                if stage == "unknown" else [])
            envelopes.extend([extraction, selection])
        return envelopes

    def conditional_record(self, submitted, branches, relation="published_by"):
        """Build a minimal successful conditional record for the extraction gate.

        Args:
            submitted: Positional graph rows defining the evidence identifiers.
            branches: True and false branch requirement groups.
            relation: Condition relation declared by the extraction.

        Returns:
            Record with only the first-stage completion and submitted graph used
            by the runner's independent gate.
        """
        v = validation
        extraction = completion()
        extraction["choices"][0]["message"]["content"] = json.dumps({"description": {
            "kind": "conditional_equals",
            "condition": {"subject": v.PUBLISHER[0], "relation": relation,
                          "operator": "eq", "value": v.CONDITION_VALUE,
                          "uniqueness_quote": "The Glass Orchard has exactly one publisher.",
                          "condition_quote": "If it was published by Silver Press"},
            "branches": {case: {"requirements": groups}
                         for case, groups in branches.items()},
        }})
        return {"stages": [{"completion": extraction}], "submitted": submitted}

    def test_ranking_complete_and_first_error_stop(self):
        """Require six synthetic completions and stop immediately on omission.

        Both fixtures use public production parsing with an isolated mock client.
        """
        client = Mock(spec=LLMClient)
        client.complete.side_effect = self.ranking_envelopes() + self.ranking_envelopes(True)
        report = {"plan": validation.case_plan("ranking"), "results": []}
        validation.run_cases(client, report, "ranking")
        self.assertEqual(client.complete.call_count, 6)
        self.assertTrue(all(all(r["controlled_checks"].values()) for r in report["results"]))
        for mode in ("extraction", "omission", "support"):
            with self.subTest(mode=mode):
                calls = self.ranking_envelopes()
                if mode == "extraction":
                    calls[0]["choices"][0]["message"]["content"] = "private-sentinel"
                if mode == "omission":
                    calls[1] = completion([validation.BIRTH, validation.OTHER_BIRTH], [validation.BIRTH[2]], [])
                if mode == "support":
                    calls += self.ranking_envelopes(True)
                    calls[-1]["choices"][0]["message"]["content"] = json.dumps({
                        "predict_answer": [validation.OTHER_BIRTH[2], validation.BIRTH[2]]})
                client.complete.side_effect = calls
                client.reset_mock()
                report = {"plan": validation.case_plan("ranking"), "results": []}
                with self.assertRaises((ValueError, RuntimeError)):
                    validation.run_cases(client, report, "ranking")
                self.assertEqual(client.complete.call_count, {"extraction": 1, "omission": 2, "support": 6}[mode])
                self.assertEqual(len(report["results"]), 2 if mode == "support" else 1)
                self.assertNotIn("private-sentinel", json.dumps(report))

    def test_ranking_failure_projection_preserves_original_exception(self):
        """Keep the exact original exception even if the entire projection fails.

        Each stage uses synthetic transport failures and immutable empty state.
        """
        from src.agent.memory_state import QuestionMemory
        for index in range(3):
            error = OSError("private-sentinel")
            client = Mock(spec=LLMClient)
            client.complete.side_effect = self.ranking_envelopes()[:index] + [error]
            report = {"plan": validation.case_plan("ranking"), "results": []}
            state = QuestionMemory("Which cities were Mira Vale and Nolan Reed born in?")
            with patch.object(validation, "conditional_failure_projection", side_effect=RuntimeError("private-sentinel")):
                with self.assertRaises(OSError) as caught:
                    validation.memory_round(client, report, "ranking.equal_support", state,
                        [validation.BIRTH, validation.OTHER_BIRTH], [0.95, 0.95], ())
            self.assertIs(caught.exception, error)
            self.assertEqual(client.complete.call_count, index + 1)
            self.assertEqual(report["results"][0]["logical_calls_received"], index)
            self.assertFalse(state.memory_triplets)
            self.assertNotIn("private-sentinel", json.dumps(report))

    def test_projection_rejects_unsafe_envelopes_and_json(self):
        """Reject refusal, truncation, duplicate fields and unknown vocabulary.

        A valid-looking body never overrides an invalid completion envelope.
        """
        from src.agent.memory_state import QuestionMemory
        state = QuestionMemory("Which cities were Mira Vale and Nolan Reed born in?")
        for mode in ("refusal", "length", "duplicate", "constant", "unknown"):
            call = self.ranking_envelopes()[0]
            choice = call["choices"][0]
            if mode == "refusal":
                choice["message"]["refusal"] = "private-sentinel"
            elif mode == "length":
                choice["finish_reason"] = "length"
            else:
                choice["message"]["content"] = {
                    "duplicate": '{"description":{},"description":{}}',
                    "constant": '{"description":NaN}',
                    "unknown": '{"description":{"kind":"private-sentinel"}}'}[mode]
            result = validation.extraction_projection({"completion": call}, state,
                {validation.BIRTH, validation.OTHER_BIRTH})
            self.assertEqual(result, {"status": "unavailable"})

    def test_ranking_safe_stage_projections_and_out_of_range_ids(self):
        """Inspect each synthetic failure stage without copying raw envelopes.

        Out-of-range IDs remain unchanged while successful evaluation is distinct.
        """
        from src.agent.memory_state import QuestionMemory
        state = QuestionMemory("Which cities were Mira Vale and Nolan Reed born in?")
        graph = {validation.BIRTH, validation.OTHER_BIRTH}
        envelopes = self.ranking_envelopes()
        calls = [{"completion": value} for value in envelopes]
        for count in range(4):
            result = validation.conditional_failure_projection(calls[:count], state, graph)
            self.assertEqual(result["evaluation_succeeded"], count > 0)
            self.assertEqual(result["final"].get("decision_consistent", False), count > 1)
            self.assertEqual(result["ranking"].get("multiset_matches", False), count > 2)
        payload = validation.safe_payload(calls[0])
        payload["description"]["branch"]["requirements"][0]["fact_queries"][0]["binding"]["evidence_ids"] = [999]
        envelopes[0]["choices"][0]["message"]["content"] = json.dumps(payload)
        result = validation.conditional_failure_projection(calls[:1], state, graph)
        self.assertEqual(result["extraction"]["out_of_range_ids"], [999])
        self.assertTrue(result["extraction_decodable"])
        self.assertFalse(result["evaluation_succeeded"])

    def test_ranking_competing_path_is_controlled_failure(self):
        """Reject a graph-valid bounce path that asks a different question.

        This is a synthetic adversarial path, not the missing real extraction.
        """
        envelopes = self.ranking_envelopes()
        envelopes[0]["choices"][0]["message"]["content"] = json.dumps({"description": {
            "kind": "nonconditional_path", "root_entity": "Mira Vale", "steps": [
                {"relation": "born_in", "binding": {"kind": "observed", "evidence_ids": [0]}},
                {"relation": "born_in", "binding": {"kind": "observed", "evidence_ids": [0]}}]}})
        envelopes[1] = completion([validation.BIRTH], ["Mira Vale"], [])
        report = {"plan": validation.case_plan("ranking"), "results": []}
        client = Mock(spec=LLMClient)
        client.complete.side_effect = envelopes
        with self.assertRaises(RuntimeError):
            validation.run_cases(client, report, "ranking")
        self.assertEqual(client.complete.call_count, 2)
        self.assertEqual(report["results"][0]["status"], "controlled_check_failure")

    def test_observed_unequal_residence_contamination_is_rejected(self):
        """Reject the observed duplicate extraction before selection begins.

        Public fixture fields transcribe the 2026/11/10 bounded acceptance reply;
        transport metadata and the private original report are not imported.
        """
        calls = self.ranking_envelopes() + self.ranking_envelopes(True)
        payload = json.loads(calls[3]["choices"][0]["message"]["content"])
        payload["description"]["branch"]["requirements"].append({
            "entity": "Mira Vale", "fact_queries": [{"relation": "lived_in",
                "binding": {"kind": "observed", "evidence_ids": [1]}}]})
        calls[3]["choices"][0]["message"]["content"] = json.dumps(payload)
        answers = ["Linden City", "Cedar City", "Harbor City"]
        calls[4] = completion([validation.CONFIRMED_BIRTH, validation.RESIDENCE,
                              validation.POSSIBLE_BIRTH], answers, [])
        calls[5]["choices"][0]["message"]["content"] = json.dumps({"predict_answer": answers})
        report = {"plan": validation.case_plan("ranking"), "results": []}
        client = Mock(spec=LLMClient)
        client.complete.side_effect = calls
        with self.assertRaises(ValueError) as caught:
            validation.run_cases(client, report, "ranking")
        self.assertEqual(str(caught.exception), "decision_queries")
        self.assertEqual(client.complete.call_count, 4)
        record = report["results"][-1]
        self.assertNotIn("stages", record)
        projection = record["failure_projection"]
        self.assertTrue(projection["extraction_decodable"])
        self.assertFalse(projection["evaluation_succeeded"])
        self.assertEqual(len(projection["extraction"]["description"]["branch"]["requirements"]), 2)

    def test_controlled_pollution_and_request_tampering(self):
        """Reject synthetic merged pollution and altered downstream requests.

        These counterfactuals exercise gates after production success and verify
        that arbitrary envelope metadata is absent from failed reports.
        """
        for mode in ("merged", "selection", "ranking", "derived"):
            with self.subTest(mode=mode):
                calls = self.ranking_envelopes() + self.ranking_envelopes(True)
                if mode == "merged":
                    payload = json.loads(calls[3]["choices"][0]["message"]["content"])
                    payload["description"]["branch"]["requirements"][0]["fact_queries"].append({
                        "relation": "lived_in", "binding": {"kind": "observed", "evidence_ids": [1]}})
                    calls[3]["choices"][0]["message"]["content"] = json.dumps(payload)
                    answers = ["Linden City", "Cedar City", "Harbor City"]
                    calls[4] = completion([validation.CONFIRMED_BIRTH, validation.RESIDENCE,
                                          validation.POSSIBLE_BIRTH], answers, [])
                    calls[5]["choices"][0]["message"]["content"] = json.dumps({"predict_answer": answers})
                for call in calls:
                    call["metadata"] = "private-sentinel"
                original = validation.check_ranking

                def controlled(record):
                    """Alter captured evidence only after production returns.

                    The first fixture remains an independent successful record.
                    """
                    if record["case"] == "ranking.unequal_support":
                        if mode in ("selection", "ranking"):
                            record["stages"][1 if mode == "selection" else 2]["messages"] = []
                        if mode == "derived":
                            with patch.object(validation, "derive_decision", return_value={
                                    "answer_candidates": [], "required_triplets": []}):
                                original(record)
                            return
                    original(record)

                client = Mock(spec=LLMClient)
                client.complete.side_effect = calls
                report = {"plan": validation.case_plan("ranking"), "results": []}
                with patch.object(validation, "check_ranking", side_effect=controlled):
                    with self.assertRaises(RuntimeError):
                        validation.run_cases(client, report, "ranking")
                record = report["results"][-1]
                self.assertEqual(client.complete.call_count, 6)
                self.assertNotIn("stages", record)
                self.assertNotIn("private-sentinel", json.dumps(record))
                self.assertIn("stages", report["results"][0])
                self.assertTrue(record["production_validated"])
                self.assertFalse(record["controlled_validated"])
                if mode == "merged":
                    self.assertTrue(record["controlled_checks"]["support_order"])

    def test_controlled_failure_projection_guard(self):
        """Preserve the exact controlled exception when projection also fails.

        Synthetic successful replies carry arbitrary metadata that must be
        removed even when bounded diagnostic construction is unavailable.
        """
        error = RuntimeError("private-sentinel")
        calls = self.ranking_envelopes()
        for call in calls:
            call["metadata"] = "private-sentinel"
        client = Mock(spec=LLMClient)
        client.complete.side_effect = calls
        report = {"plan": validation.case_plan("ranking"), "results": []}
        with patch.object(validation, "check_ranking", side_effect=error), patch.object(
                validation, "conditional_failure_projection", side_effect=RecursionError("private-sentinel")):
            with self.assertRaises(RuntimeError) as caught:
                validation.run_cases(client, report, "ranking")
        self.assertIs(caught.exception, error)
        self.assertNotIn("private-sentinel", json.dumps(report))
        self.assertNotIn("stages", report["results"][0])

    def test_projection_recursion_is_unavailable(self):
        """Bound deeply nested JSON before it can replace an original failure.

        The payload fits the byte limit but exceeds the JSON parser stack.
        """
        from src.agent.memory_state import QuestionMemory
        call = {"completion": completion()}
        call["completion"]["choices"][0]["message"]["content"] = '[' * 1100 + '0' + ']' * 1100
        result = validation.extraction_projection(call, QuestionMemory("Question"), set())
        self.assertEqual(result, {"status": "unavailable"})

    def test_selected_case_and_dependencies(self):
        """Execute only the chosen transaction and reject missing prior rounds.

        Invalid selection must fail before configuration or a paid client loads.
        """
        plan = validation.case_plan("multihop", cases=["single.sufficient"])
        report = {"plan": plan, "results": []}
        client = Mock(spec=LLMClient)
        client.complete.side_effect = self.path_envelopes()[:2]
        validation.run_cases(client, report, "multihop")
        self.assertEqual(client.complete.call_count, 2)
        self.assertEqual(len(report["results"]), 1)
        with patch.object(validation, "load_llm_config") as load:
            with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()):
                validation.main(["--suite", "multihop", "--case", "multi.recovery"])
            load.assert_not_called()
        self.assertEqual(len(validation.case_plan("conditional", cases=[
            "conditional.silver_full", "conditional.bronze_full"])), 2)

    def test_failed_extraction_projection_is_bounded_and_atomic(self):
        """Preserve useful fake-gap fields without retaining failed raw text.

        The real public entry must still fail once and preserve the old state.
        Unknown strings and malformed content cannot leak through the projection.
        """
        from src.agent.memory_state import QuestionMemory
        extraction = completion()
        extraction["choices"][0]["message"]["content"] = json.dumps({"description": {
            "kind": "nonconditional", "branch": {"requirements": [{
                "entity": validation.AUTHOR[0], "fact_queries": [{
                    "relation": "written_by", "binding": {
                        "kind": "missing", "direction": "outgoing"}}]}]}}})
        client = Mock(spec=LLMClient)
        client.complete.return_value = extraction
        state = QuestionMemory(validation.QUESTION)
        report = {"plan": validation.case_plan("multihop", cases=["single.sufficient"]), "results": []}
        with self.assertRaises(ValueError):
            validation.memory_round(client, report, "single.sufficient", state,
                                    [validation.AUTHOR, validation.BIRTH], [0.95, 0.95], ())
        record = report["results"][0]
        self.assertTrue(record["old_state_unchanged"])
        self.assertEqual(client.complete.call_count, 1)
        self.assertEqual(record["failed_extraction"]["status"], "validated_projection")
        self.assertEqual(record["failed_extraction"]["hits"][0]["graph_ids"], [1])
        for text in ("secret-key", '{"description":{"kind":"secret-key"}}'):
            extraction["choices"][0]["message"]["content"] = text
            result = validation.extraction_projection({"completion": extraction}, state, {validation.AUTHOR})
            self.assertEqual(result, {"status": "unavailable"})
            self.assertNotIn("secret-key", json.dumps(result))

    def test_observed_false_condition_and_binding_only_counterfactual(self):
        """Replay the observed conditional misrouting without a provider call.

        Fixing only the false-branch gap must still fail the controlled path
        gate, even when production returns the correct city and both edges.
        """
        from src.agent.memory_state import QuestionMemory
        for binding_only in (False, True):
            with self.subTest(binding_only=binding_only):
                observed = {"kind": "observed", "evidence_ids": [0]}
                description = {"kind": "conditional_equals", "condition": {
                    "subject": validation.AUTHOR[0], "relation": "written_by",
                    "operator": "eq", "value": validation.BIRTH[0],
                    "condition_quote": validation.QUESTION,
                    "uniqueness_quote": "the author of The Glass Orchard",
                }, "branches": {case: {"requirements": [{
                    "entity": validation.BIRTH[0], "fact_queries": [{
                        "relation": "born_in", "binding": observed if
                        case == "true" or binding_only else {
                            "kind": "missing", "direction": "outgoing"},
                    }],
                }]} for case in ("true", "false")}}
                extraction = completion()
                extraction["choices"][0]["message"]["content"] = json.dumps({
                    "description": description})
                client = Mock(spec=LLMClient)
                client.complete.side_effect = [extraction, completion(
                    [validation.AUTHOR, validation.BIRTH], [validation.BIRTH[2]], [])]
                state = QuestionMemory(validation.QUESTION)
                report = {"plan": validation.case_plan(
                    "multihop", cases=["single.sufficient"]), "results": []}
                with self.assertRaises((ValueError, RuntimeError)):
                    validation.memory_round(client, report, "single.sufficient", state,
                                            [validation.AUTHOR, validation.BIRTH], [0.95, 0.95], ())
                record = report["results"][0]
                self.assertTrue(record["old_state_unchanged"])
                self.assertFalse(state.memory_triplets)
                self.assertFalse(state.rejected_triplets)
                self.assertEqual(client.complete.call_count, 2 if binding_only else 1)
                if binding_only:
                    self.assertFalse(record["controlled_checks"]["two_hop_extraction"])
                else:
                    self.assertEqual(record["error_code"], "decision_conflict")
                    self.assertEqual(record["failed_extraction"]["description"], description)
                    self.assertTrue(any(hit["graph_ids"] == [0]
                                        for hit in record["failed_extraction"]["hits"]))

    def test_probe_direction_conflict_and_supported_reverse_fact(self):
        """Replay the failed probe and two direction counterfactuals.

        The seeded recovery snapshot retains two facts and one rejection.
        A reversed birth fact requires incoming; no automatic flip is allowed.
        """
        for reverse, direction in ((False, "incoming"), (False, "outgoing"),
                                   (True, "incoming")):
            with self.subTest(reverse=reverse, direction=direction):
                birth = (validation.BIRTH[2], validation.BIRTH[1], validation.BIRTH[0]) if reverse else validation.BIRTH
                state = validation.QuestionMemory(validation.QUESTION,
                    [validation.AUTHOR, birth], [validation.DISTRACTOR])
                before = validation.snapshot(state)
                extraction = self.path_envelopes()[0]
                description = json.loads(extraction["choices"][0]["message"]["content"])
                graph = sorted([validation.AUTHOR, birth])
                description["description"]["steps"][0]["binding"]["evidence_ids"] = [graph.index(validation.AUTHOR)]
                description["description"]["steps"][1]["binding"] = (
                    {"kind": "missing", "direction": direction} if direction == "incoming" and not reverse
                    else {"kind": "observed", "evidence_ids": [graph.index(birth)]})
                extraction["choices"][0]["message"]["content"] = json.dumps(description)
                client = Mock(spec=LLMClient)
                client.complete.side_effect = [extraction, completion(
                    [validation.AUTHOR, birth], [validation.BIRTH[2]], [])]
                report = {"plan": validation.case_plan("multihop"), "results": []}
                args = (client, report, "multi.exclusion_probe", state,
                        [validation.AUTHOR, birth, validation.DISTRACTOR], [0.95] * 3, ())
                if not reverse and direction == "incoming":
                    with self.assertRaises(ValueError):
                        validation.memory_round(*args)
                    record = report["results"][0]
                    self.assertEqual(client.complete.call_count, 1)
                    self.assertEqual(record["error_code"], "decision_path")
                    self.assertEqual(record["failure_stage"], "evidence_decision")
                    self.assertNotIn("description", record["failed_extraction"])
                else:
                    # The fixed acceptance fixture expects the original birth edge;
                    # reverse only that fixture gate, keeping production validation.
                    with patch.object(validation, "check_two_hop") if reverse else patch.object(
                        validation, "check_two_hop", wraps=validation.check_two_hop
                    ):
                        validation.memory_round(*args)
                    self.assertEqual(client.complete.call_count, 2)
                    self.assertEqual(report["results"][0]["predict_answer"], (validation.BIRTH[2],))
                    final = client.complete.call_args_list[1].args[0][0]["content"]
                    self.assertIn('"missing_relations": []', final)
                    self.assertIn('"answer_candidates": ["Linden City"]', final)
                self.assertEqual(validation.snapshot(state), before)
                self.assertEqual(set(report["results"][0]["submitted"]), {validation.AUTHOR, birth})

    def test_missing_direction_counterfactual(self):
        """Distinguish missing-edge semantics at the controlled acceptance gate.

        Legacy directional missing bindings are rejected before selection.
        Neither old direction can publish a candidate or reach a later round.
        """
        for direction in ("incoming", "outgoing"):
            with self.subTest(direction=direction):
                extraction, final = self.path_envelopes()[6:8]
                payload = json.loads(extraction["choices"][0]["message"]["content"])
                payload["description"]["steps"][1]["binding"]["direction"] = direction
                extraction["choices"][0]["message"]["content"] = json.dumps(payload)
                query = (f'Which entity connects to "{validation.BIRTH[0]}" through relation "born_in"?'
                         if direction == "incoming" else
                         f'Which entity does "{validation.BIRTH[0]}" connect to through relation "born_in"?')
                client = Mock(spec=LLMClient)
                client.complete.side_effect = [extraction, completion([validation.AUTHOR], [], [query])]
                state = validation.QuestionMemory(validation.QUESTION)
                before = validation.snapshot(state)
                report = {"plan": validation.case_plan("multihop"), "results": []}
                args = (client, report, "multi.partial", state,
                        [validation.AUTHOR, validation.BIRTH, validation.DISTRACTOR],
                        [0.95, 0.7, 0.95], ())
                with self.assertRaises(ValueError):
                    validation.memory_round(*args)
                self.assertEqual(client.complete.call_count, 1)
                self.assertEqual(validation.snapshot(state), before)
                self.assertNotIn(validation.BIRTH, report["results"][0]["submitted"])

    def path_envelopes(self, keep_distractor: bool = False) -> list:
        """Build six valid path transactions for real public-parser replay.

        Args:
            keep_distractor: Keep the optional edge to exercise honest reporting
                when the model never naturally rejects it.

        Returns:
            Twelve synthetic completions, ordered extraction then selection.
        """
        envelopes = []
        for index, selected in enumerate((
            [validation.AUTHOR, validation.BIRTH], [validation.AUTHOR], [],
            [validation.AUTHOR], [validation.AUTHOR, validation.BIRTH],
            [validation.AUTHOR, validation.BIRTH],
        )):
            if keep_distractor and index >= 3:
                selected = selected + [validation.DISTRACTOR]
            complete = index in (0, 4, 5)
            target, relation = ((validation.AUTHOR[0], "written_by") if index == 2
                                else (validation.BIRTH[0], "born_in"))
            questions = [] if complete else [
                f'Find triples involving "{target}" with relation "{relation}".']
            graph = ([] if index == 2 else [validation.AUTHOR, validation.BIRTH]
                     if complete else [validation.AUTHOR])
            if index == 3 or (keep_distractor and index >= 4):
                graph.append(validation.DISTRACTOR)
            extraction = completion()
            extraction["choices"][0]["message"]["content"] = json.dumps({"description": {
                "kind": "nonconditional_path", "root_entity": validation.AUTHOR[0],
                "steps": [{"relation": edge[1], "binding": (
                    {"kind": "observed", "evidence_ids": [sorted(graph).index(edge)]}
                    if edge in graph else {"kind": "missing"})}
                    for edge in (validation.AUTHOR, validation.BIRTH)]}})
            envelopes.extend([extraction, completion(
                selected, [validation.BIRTH[2]] if complete else [], questions)])
        return envelopes

    def test_multihop_reuses_six_cases_and_checks_actual_state_handoffs(self):
        """Replay twelve calls with and without natural distractor rejection.

        Actual public transactions feed each successor. Both model-stage graphs
        are checked, and an unobserved natural rejection remains explicitly so.
        """
        for keep in (False, True):
            with self.subTest(keep_distractor=keep):
                client = Mock(spec=LLMClient)
                client.complete.side_effect = self.path_envelopes(keep)
                report = {"plan": validation.case_plan("multihop"), "results": []}
                validation.run_cases(client, report, "multihop")
                records = report["results"]
                self.assertEqual({r["case"] for r in records}, validation.PATH_CASE_NAMES)
                self.assertEqual(client.complete.call_count, 12)
                self.assertTrue(all(all(r["controlled_checks"].values()) for r in records))
                for previous, current in zip(records[3:], records[4:]):
                    self.assertEqual(current["state_before"], previous["state_after"])
                    self.assertEqual(current["queries"], previous["subsequent_question"])
                self.assertNotIn(validation.BIRTH, records[3]["submitted"])
                self.assertNotIn(validation.BIRTH, records[3]["state_after"]["rejected"])
                self.assertEqual(report["multihop_observations"], {
                    "natural_distractor_rejection": not keep,
                    "rejected_distractor_filtering": "not_observed" if keep else "observed",
                })

    def test_multihop_rejects_semantically_flattened_extraction_before_next_case(self):
        """Stop a structurally valid one-hop answer that drops the author edge.

        Production endpoint checks permit this synthetic extraction, so only the
        runner's controlled two-hop gate distinguishes it from real path success.
        """
        extraction = completion()
        extraction["choices"][0]["message"]["content"] = json.dumps({"description": {
            "kind": "nonconditional", "branch": {"requirements": [{
                "entity": validation.BIRTH[0], "fact_queries": [{"relation": "born_in",
                    "binding": {"kind": "observed", "evidence_ids": [0]}}],
            }]},
        }})
        client = Mock(spec=LLMClient)
        client.complete.side_effect = [extraction, completion(
            [validation.BIRTH], [validation.BIRTH[2]], [])]
        report = {"plan": validation.case_plan("multihop"), "results": []}
        with self.assertRaises(RuntimeError):
            validation.run_cases(client, report, "multihop")
        self.assertEqual(client.complete.call_count, 2)
        self.assertEqual(len(report["results"]), 1)
        record = report["results"][0]
        self.assertEqual(record["status"], "controlled_check_failure")
        self.assertFalse(record["controlled_checks"]["two_hop_extraction"])
        self.assertFalse(record["controlled_checks"]["required_path"])
        self.assertTrue(record["old_state_unchanged"])
        # The CLI must propagate this gate as failure and close its client,
        # rather than merely reporting a false observation with exit status zero.
        (ROOT / "tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="tmp_path_gate_") as directory:
            client.reset_mock()
            client.complete.side_effect = [extraction, completion(
                [validation.BIRTH], [validation.BIRTH[2]], [])]
            config = LLMConfig("gpt-4o-mini", "offline-secret", "https://test.invalid/v1", 2, 2)
            with patch.object(validation, "ROOT", Path(directory)), patch.object(
                validation, "load_llm_config", return_value=config,
            ), patch.object(validation, "LLMClient", return_value=client), redirect_stdout(io.StringIO()):
                self.assertEqual(validation.main(["--suite", "multihop"]), 1)
            client.close.assert_called_once()
            self.assertEqual(client.complete.call_count, 2)
            saved = json.loads(next((Path(directory) / "tmp").glob("tmp_*.json")).read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "error")
            self.assertEqual(len(saved["results"]), 1)
            self.assertEqual(saved["results"][0]["status"], "controlled_check_failure")

    def test_existing_default_suites_share_the_path_gate(self):
        """Preserve decomposition and isolation while sharing six expectations.

        The focused suite only omits unrelated calls; default single and multi
        must invoke the identical checker on their corresponding path records.
        """
        decomp = completion()
        decomp["choices"][0]["message"]["content"] = json.dumps([validation.QUESTION])
        independent = completion()
        independent["choices"][0]["message"]["content"] = json.dumps({"description": {
            "kind": "nonconditional", "branch": {"requirements": [{
                "entity": validation.DISTRACTOR[0], "fact_queries": [{
                    "relation": validation.DISTRACTOR[1],
                    "binding": {"kind": "observed", "evidence_ids": [0]},
                }],
            }]},
        }})
        path = self.path_envelopes()
        for suite, envelopes in (
            ("single", [decomp] + path[:6]),
            ("multi", path[6:] + [independent, completion(
                [validation.DISTRACTOR], [validation.DISTRACTOR[2]], [])]),
        ):
            with self.subTest(suite=suite):
                client = Mock(spec=LLMClient)
                client.complete.side_effect = envelopes
                report = {"plan": validation.case_plan(suite), "results": []}
                with patch.object(validation, "check_two_hop", wraps=validation.check_two_hop) as gate:
                    validation.run_cases(client, report, suite)
                self.assertEqual(gate.call_count, 3)
                self.assertEqual(len(report["results"]), 4)
                self.assertEqual(client.complete.call_count, len(envelopes))

    def test_two_hop_checks_fail_independently_for_each_required_expectation(self):
        """Inject faults after valid replay to verify runner-only failure gates.

        These mutations model incorrectly permissive public results, including
        resubmission in an actual prompt despite a correct computed union.
        """
        client = Mock(spec=LLMClient)
        client.complete.side_effect = self.path_envelopes()
        report = {"plan": validation.case_plan("multihop"), "results": []}
        validation.run_cases(client, report, "multihop")
        faults = (
            (0, "selected_triplets", [validation.BIRTH], "required_path"),
            (0, "predict_answer", (validation.BIRTH[0],), "exact_answer"),
            (0, "subsequent_question", ("More evidence?",), "reachable_query"),
            (1, "predict_answer", (validation.BIRTH[2],), "exact_answer"),
            (1, "subsequent_question", ('Which entity does "Nolan Reed" connect to through relation "born_in"?',), "reachable_query"),
            (2, "selected_triplets", [validation.AUTHOR], "empty_selection"),
            (3, "submitted", [validation.AUTHOR, validation.BIRTH], "hidden_not_submitted_or_rejected"),
            (4, "submitted", [validation.AUTHOR, validation.BIRTH, validation.DISTRACTOR], "prior_rejections_filtered"),
        )
        for index, field, value, failed_check in faults:
            with self.subTest(check=failed_check, case=index):
                record = json.loads(json.dumps(report["results"][index]))
                record[field] = value
                with self.assertRaises(RuntimeError):
                    validation.check_two_hop(record)
                self.assertFalse(record["controlled_checks"][failed_check])
        record = json.loads(json.dumps(report["results"][4]))
        record["state_before"]["memory"] = []
        with self.assertRaises(RuntimeError):
            validation.check_two_hop(record)
        self.assertFalse(record["controlled_checks"]["recovery"])
        record = json.loads(json.dumps(report["results"][5]))
        # A syntactically valid request graph with the rejected edge injected.
        prompt, _ = record["stages"][0]["messages"][0]["content"].split("\nNumbered knowledge: ")
        graph = sorted([validation.AUTHOR, validation.BIRTH, validation.DISTRACTOR])
        record["stages"][0]["messages"][0]["content"] = prompt + "\nNumbered knowledge: " + json.dumps(
            [[i, list(t)] for i, t in enumerate(graph)])
        with self.assertRaises(RuntimeError):
            validation.check_two_hop(record)
        self.assertFalse(record["controlled_checks"]["both_stage_graphs"])

    def test_conditional_suite_uses_default_protocol_and_inherits_real_snapshots(self):
        """Replay complete graphs and two independent chains without a provider.

        Synthetic extractions pass the public parser; real filtering must remove
        the rejected birth before either stage sees the final high-score probe.
        Structured-output options and both stages must survive the counting facade.
        """
        envelopes = []
        for value, active in (("Silver Press", validation.BIRTH), ("Bronze Press", validation.OTHER_BIRTH)):
            publisher = (*validation.PUBLISHER[:2], value)
            for stage in ("full", "unknown", "resolved", "probe"):
                graph = sorted([validation.BIRTH, validation.OTHER_BIRTH] if stage == "unknown"
                               else [publisher, active] if stage == "probe"
                               else [publisher, validation.BIRTH, validation.OTHER_BIRTH])
                branches = {}
                for name, birth in (("true", validation.BIRTH), ("false", validation.OTHER_BIRTH)):
                    binding = ({"kind": "observed", "evidence_ids": [graph.index(birth)]}
                               if birth in graph else {"kind": "missing", "direction": "outgoing"})
                    branches[name] = {"requirements": [{"entity": birth[0], "fact_queries": [
                        {"relation": "born_in", "binding": binding}]}]}
                description = {"description": {
                    "kind": "conditional_equals",
                    "condition": {"subject": "The Glass Orchard", "relation": "published_by",
                                  "operator": "eq", "value": "Silver Press",
                                  "uniqueness_quote": "The Glass Orchard has exactly one publisher.",
                                  "condition_quote": "If it was published by Silver Press"},
                    "branches": branches,
                }}
                extraction = completion()
                extraction["choices"][0]["message"]["content"] = json.dumps(description)
                selection = completion(
                    [validation.BIRTH, validation.OTHER_BIRTH] if stage == "unknown" else [publisher, active],
                    [] if stage == "unknown" else [active[2]],
                    ['Which entity does "The Glass Orchard" connect to through relation "published_by"?']
                    if stage == "unknown" else [],
                )
                envelopes.extend([extraction, selection])
        client = Mock(spec=LLMClient)
        client.complete.side_effect = envelopes
        report = {"plan": validation.case_plan("conditional"), "results": []}
        validation.run_cases(client, report, "conditional")
        self.assertEqual(client.complete.call_count, 16)
        self.assertTrue(all(all(r["controlled_checks"].values()) for r in report["results"]))
        for record in report["results"]:
            self.assertEqual(record["prompt_variant"], "evidence_decision")
            self.assertEqual(record["logical_calls_started"], 2)
            self.assertEqual(len(record["stages"]), 2)
            self.assertTrue(all(call["options"]["response_format"]["json_schema"]["strict"]
                                for call in record["stages"]))
        for index in (3, 7):
            self.assertEqual(len(report["results"][index]["submitted"]), 2)
        self.assertEqual(report["results"][4]["state_before"]["memory"], [])
        self.assertEqual(report["results"][5]["state_before"]["rejected"], [])

        # The Bronze probe must reject a selected subject before returning state.
        from src.agent.memory_management import manage_memory
        from src.agent.validation_error import MemoryValidationError

        probe = report["results"][-1]
        before = probe["state_before"]
        state = validation.QuestionMemory(before["question"], before["memory"], before["rejected"])
        snapshot = validation.snapshot(state)
        for answer in (validation.OTHER_BIRTH[0], validation.OTHER_BIRTH[2]):
            client = Mock(spec=LLMClient)
            client.complete.side_effect = [envelopes[-2], completion(
                probe["state_after"]["memory"], [answer], [])]
            if answer == validation.OTHER_BIRTH[0]:
                with self.assertRaises(MemoryValidationError) as caught:
                    manage_memory(state, probe["triples"], probe["scores"], client)
                self.assertEqual(caught.exception.code, "decision_answer_candidate")
                self.assertEqual(caught.exception.stage, "decision_consistency")
                client.complete.side_effect = [envelopes[-2], completion(
                    probe["state_after"]["memory"], [answer], [])]
                failed_report = {"plan": validation.case_plan("conditional"), "results": []}
                with self.assertRaises(MemoryValidationError) as reported:
                    validation.memory_round(client, failed_report, "conditional.bronze_probe",
                                            state, probe["triples"], probe["scores"], ())
                self.assertEqual(reported.exception.code, "decision_answer_candidate")
                failed = failed_report["results"][0]
                projection = failed["failure_projection"]
                self.assertEqual(projection["decision"]["answer_candidates"], ["Cedar City"])
                self.assertEqual(projection["final"]["predict_answer"], ("Nolan Reed",))
                self.assertTrue(failed["old_state_unchanged"])
                self.assertNotIn("stages", failed)
                self.assertEqual(client.complete.call_count, 4)
                for unsafe in ("secret-key", json.dumps({"selected_triplets": [],
                               "predict_answer": [], "subsequent_question": ["secret-key"]})):
                    bad = completion()
                    bad["choices"][0]["message"]["content"] = unsafe
                    safe = validation.conditional_failure_projection(
                        [{"completion": envelopes[-2]}, {"completion": bad}], state,
                        set(map(tuple, probe["submitted"])))
                    self.assertEqual(safe["final"], {"status": "unavailable"})
                    self.assertNotIn("secret-key", json.dumps(safe))
            else:
                result = manage_memory(state, probe["triples"], probe["scores"], client)
                self.assertEqual(result.response.predict_answer, (answer,))
            self.assertEqual(client.complete.call_count, 4 if answer == validation.OTHER_BIRTH[0] else 2)
            self.assertEqual(validation.snapshot(state), snapshot)

        # A structurally valid full-graph answer retaining the irrelevant branch
        # must fail the controlled gate before an unknown-condition chain starts.
        client = Mock(spec=LLMClient)
        client.complete.side_effect = [envelopes[0], completion(
            [validation.BIRTH, validation.OTHER_BIRTH, (*validation.PUBLISHER[:2], "Silver Press")],
            [validation.BIRTH[2]], [],
        )]
        report = {"plan": validation.case_plan("conditional"), "results": []}
        with self.assertRaises(RuntimeError):
            validation.run_cases(client, report, "conditional")
        self.assertEqual(client.complete.call_count, 2)
        self.assertEqual(len(report["results"]), 1)
        self.assertEqual(report["results"][0]["status"], "controlled_check_failure")

    def test_conditional_extraction_gate_accepts_clean_rounds(self):
        """Pass the new gate on three clean rounds without changing any stage.

        The synthetic Bronze chain declares the question-grounded outgoing
        direction for each absent fact, so each round must still use exactly two
        logical calls and publish its retained snapshot.
        """
        v = validation
        cases = ["conditional.bronze_unknown", "conditional.bronze_resolved",
                 "conditional.bronze_probe"]
        client = Mock(spec=LLMClient)
        client.complete.side_effect = self.conditional_chain_envelopes(
            "Bronze Press", v.OTHER_BIRTH)
        report = {"plan": v.case_plan("conditional", cases=cases), "results": []}
        v.run_cases(client, report, "conditional")
        self.assertEqual(client.complete.call_count, 6)
        self.assertEqual(len(report["results"]), 3)
        for record in report["results"]:
            self.assertEqual(record["logical_calls_started"], 2)
            self.assertEqual(len(record["stages"]), 2)
            self.assertTrue(all(record["controlled_checks"].values()))
            self.assertTrue(record["controlled_validated"])
        # The gate is read-only: inspecting a retained record again changes no
        # stage, selection, answer or published state.
        record = report["results"][-1]
        keys = ("stages", "selected_triplets", "predict_answer", "state_after")
        before = json.dumps({key: record[key] for key in keys}, sort_keys=True)
        self.assertTrue(all(v.check_conditional_extraction(record).values()))
        after = json.dumps({key: record[key] for key in keys}, sort_keys=True)
        self.assertEqual(before, after)

    def test_conditional_extraction_gate_rejects_inactive_direction(self):
        """Reject an inactive incoming direction before the next round starts.

        Production still accepts the reply and returns the correct city, so only
        the independent extraction gate can stop the chain with the fixed error.
        """
        v = validation
        cases = ["conditional.bronze_unknown", "conditional.bronze_resolved",
                 "conditional.bronze_probe"]
        calls = self.conditional_chain_envelopes("Bronze Press", v.OTHER_BIRTH)
        payload = json.loads(calls[4]["choices"][0]["message"]["content"])
        binding = payload["description"]["branches"]["true"]["requirements"][0][
            "fact_queries"][0]["binding"]
        binding["direction"] = "incoming"
        calls[4]["choices"][0]["message"]["content"] = json.dumps(payload)
        client = Mock(spec=LLMClient)
        client.complete.side_effect = calls
        report = {"plan": v.case_plan("conditional", cases=cases), "results": []}
        with self.assertRaises(RuntimeError) as caught:
            v.run_cases(client, report, "conditional")
        self.assertEqual(str(caught.exception), "Conditional acceptance expectation failed")
        self.assertEqual(client.complete.call_count, 6)
        self.assertEqual(len(report["results"]), 3)
        record = report["results"][-1]
        self.assertEqual(record["status"], "controlled_check_failure")
        self.assertFalse(record["controlled_checks"]["conditional_true_branch"])
        self.assertTrue(record["controlled_checks"]["conditional_false_branch"])
        self.assertTrue(record["controlled_checks"]["answers"])
        self.assertTrue(record["controlled_checks"]["memory"])
        self.assertIn("stages", record)
        self.assertFalse(record["controlled_validated"])
        self.assertTrue(record["old_state_unchanged"])

    def test_conditional_extraction_gate_accepts_relation_alias(self):
        """Accept a declared birthplace alias that matches the submitted fact.

        The alias must still point to the outgoing person-to-city fact, so an
        alias is not a way to bypass the entity role check.
        """
        v = validation
        alias, other = ((v.BIRTH[0], "birthplace", v.BIRTH[2]),
                        (v.OTHER_BIRTH[0], "birthplace", v.OTHER_BIRTH[2]))
        groups = {
            "true": [{"entity": alias[0], "fact_queries": [{
                "relation": alias[1],
                "binding": {"kind": "observed", "evidence_ids": [0]}}]}],
            "false": [{"entity": other[0], "fact_queries": [{
                "relation": other[1],
                "binding": {"kind": "observed", "evidence_ids": [1]}}]}],
        }
        checks = v.check_conditional_extraction(
            self.conditional_record([alias, other], groups))
        self.assertTrue(all(checks.values()))
        # The same alias with the reversed city-as-head fact must fail.
        reversed_ = (v.BIRTH[2], "birthplace", v.BIRTH[0])
        checks = v.check_conditional_extraction(
            self.conditional_record([reversed_, other], groups))
        self.assertFalse(checks["conditional_true_branch"])

    def test_conditional_extraction_gate_accepts_both_condition_relations(self):
        """Accept the pre-resolution and post-resolution condition relation names."""
        v = validation
        groups = {
            "true": [{"entity": v.BIRTH[0], "fact_queries": [{
                "relation": v.BIRTH[1],
                "binding": {"kind": "observed", "evidence_ids": [0]}}]}],
            "false": [{"entity": v.OTHER_BIRTH[0], "fact_queries": [{
                "relation": v.OTHER_BIRTH[1],
                "binding": {"kind": "observed", "evidence_ids": [1]}}]}],
        }
        for relation in ("publisher", "published_by"):
            with self.subTest(relation=relation):
                checks = v.check_conditional_extraction(self.conditional_record(
                    sorted([v.BIRTH, v.OTHER_BIRTH]), groups, relation=relation))
                self.assertTrue(all(checks.values()))

    def test_unknown_memory_variant_fails_before_a_paid_request(self) -> None:
        """Reject configuration mistakes without publishing or sending anything.

        The public scored-round entry point must validate a variant even when
        its evidence is empty, preserving the caller's original snapshot.
        """
        client = Mock(spec=LLMClient)
        state = validation.QuestionMemory(validation.QUESTION)
        before = validation.snapshot(state)
        with self.assertRaises(ValueError):
            validation.manage_memory(state, [], [], client, prompt_variant="unknown")
        client.complete.assert_not_called()
        self.assertEqual(validation.snapshot(state), before)

    def test_clarification_comparison_routes_variants_without_changing_evidence(self) -> None:
        """Compare identical evidence stages through the public MSM boundary.

        Mocked model content verifies prompt routing, immutable states and the
        six-call budget; it makes no claim about real clarification quality.
        """
        client = Mock(spec=LLMClient)
        client.complete.side_effect = [
            envelope for _ in range(2) for envelope in (
                completion([], [], ["Who wrote The Glass Orchard?"]),
                completion([validation.AUTHOR], [], ["Where was Mira Vale born?"]),
                completion([validation.AUTHOR, validation.BIRTH], ["Linden City"], []),
            )
        ]
        report = {"plan": validation.case_plan("clarification_diagnostic"), "results": []}
        validation.run_cases(client, report, "clarification_diagnostic")
        self.assertEqual(client.complete.call_count, 6)
        records = report["results"]
        for index, record in enumerate(records):
            prompt = client.complete.call_args_list[index].args[0][0]["content"]
            self.assertEqual("### Retrieval Questions" in prompt, index >= 3)
            self.assertTrue(record["old_state_unchanged"])
            self.assertTrue(record["state_rules_match"])
        self.assertEqual([r["submitted"] for r in records[:3]],
                         [r["submitted"] for r in records[3:]])

    def test_reference_comparison_records_actual_queries_and_author_evidence(self) -> None:
        """Keep the comparison bounded and expose the actual MSM handoff.

        Synthetic completions verify orchestration, not semantic quality or
        retrieval recall. Both decomposition variants use the public parser.
        """
        client = Mock(spec=LLMClient)
        envelopes = []
        for questions in (["Who wrote it?"], ["Who wrote The Glass Orchard?"]):
            envelope = completion([], [], [])
            envelope["choices"][0]["message"]["content"] = json.dumps(questions)
            envelopes.append(envelope)
        envelopes.append(completion([validation.AUTHOR], [], ["Where was Mira Vale born?"]))
        client.complete.side_effect = envelopes
        report = {"plan": validation.case_plan("reference_diagnostic", "retrieval_questions"), "results": [], "prompt_variant": "retrieval_questions"}
        validation.run_cases(client, report, "reference_diagnostic")
        self.assertEqual(client.complete.call_count, 3)
        records = report["results"]
        self.assertEqual([item["prompt_variant"] for item in records[:2]],
                         ["paper", "explicit_references"])
        self.assertEqual(records[2]["queries"], records[1]["sub_questions"])
        self.assertEqual(records[2]["submitted"], [validation.AUTHOR])
        self.assertEqual(records[2]["subsequent_question"], ("Where was Mira Vale born?",))
        self.assertTrue(records[2]["state_rules_match"])

    def response(self, body: dict) -> requests.Response:
        """Wrap a synthetic envelope in a real requests response.

        Args:
            body: JSON object passed through real HTTP and business decoders.

        Returns:
            Successful buffered HTTP response with synthetic request metadata.
        """
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(body).encode("utf-8")
        response.headers["x-request-id"] = "req_acceptance_offline"
        return response

    def test_preview_never_loads_credentials_or_constructs_client(self) -> None:
        """Expose fixed case counts and expectations without a paid call.

        Each suite is previewed independently so operators can select a smaller
        run before supplying private configuration.
        """
        for suite, count, limit in (("single", 4, 10), ("multi", 4, 12), ("removal", 3, 9),
                                   ("ranking", 2, 6), ("empty_diagnostic", 1, 3),
                                   ("reference_diagnostic", 3, 5), ("clarification_diagnostic", 6, 12),
                                   ("all", 13, 37), ("conditional", 8, 24), ("multihop", 6, 18)):
            with self.subTest(suite=suite), patch.object(validation, "load_llm_config") as load, patch.object(
                validation, "LLMClient",
            ) as client, redirect_stdout(io.StringIO()) as output:
                self.assertEqual(validation.main(["--list-cases", "--suite", suite]), 0)
                plan = json.loads(output.getvalue())
                self.assertEqual(plan["logical_call_limit"], limit)
                self.assertEqual(len(plan["plan"]), count)
                load.assert_not_called()
                client.assert_not_called()

    def test_all_cases_use_public_parsers_and_keep_semantic_review_pending(self) -> None:
        """Exercise thirteen cases and fifteen requests with real HTTP accounting.

        The model keeps the useful path and rejects a distractor. Absence of old
        memory removal is recorded as unobserved rather than fabricated success.
        """
        author, birth, distractor = validation.AUTHOR, validation.BIRTH, validation.DISTRACTOR
        decomp = completion([], [], [])
        decomp["choices"][0]["message"]["content"] = json.dumps(["Who wrote The Glass Orchard?"])
        envelopes = [
            decomp,
            completion([author, birth], [birth[2]], []),
            completion([author], [], ["Where was Mira Vale born?"]),
            completion([], [], [validation.QUESTION]),
            completion([author], [], ["Where was Mira Vale born?"]),
            completion([author, birth], [birth[2]], []),
            completion([author, birth], [birth[2]], []),
            completion([distractor], [distractor[2]], []),
            completion([birth], [], ["Who published The Glass Orchard?"]),
            completion([validation.PUBLISHER, validation.OTHER_BIRTH], ["Cedar City"], []),
            completion([validation.PUBLISHER, validation.OTHER_BIRTH], ["Cedar City"], []),
            completion([birth, validation.OTHER_BIRTH], ["Cedar City", "Linden City"], []),
            completion([], [], []),
            completion([validation.CONFIRMED_BIRTH, validation.POSSIBLE_BIRTH],
                       ["Linden City", "Cedar City"], []),
            completion([], [], []),
        ]
        for index, answers in ((12, ["Cedar City", "Linden City"]),
                               (14, ["Linden City", "Cedar City"])):
            envelopes[index]["choices"][0]["message"]["content"] = json.dumps({"predict_answer": answers})
        (ROOT / "tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="tmp_acceptance_") as directory:
            root = Path(directory)
            config = LLMConfig("gpt-4o-mini", "offline-secret", "https://test.invalid/v1", 2, 2)
            client = LLMClient(config, root / "tmp_usage.csv")
            with patch.object(validation, "ROOT", root), patch.object(
                validation, "load_llm_config", return_value=config,
            ), patch.object(validation, "LLMClient", return_value=client), patch(
                "requests.adapters.HTTPAdapter.send",
                side_effect=[self.response(body) for body in envelopes],
            ) as send, patch.object(client, "close", wraps=client.close) as close, redirect_stdout(io.StringIO()):
                self.assertEqual(validation.main(["--prompt-variant", "retrieval_questions"]), 0)
                self.assertEqual(send.call_count, 15)
                close.assert_called_once()
            report = json.loads(next((root / "tmp").glob("tmp_*.json")).read_text(encoding="utf-8"))
            self.assertEqual(report["http_attempt_limit"], 75)
            self.assertEqual(report["logical_calls_started"], 15)
            self.assertTrue(all(record["ranking_stage_enabled"] for record in report["results"][1:]))
            self.assertEqual(report["semantic_review"], "pending")
            self.assertFalse(report["multi_observations"]["old_memory_removal"])
            self.assertTrue(report["multi_observations"]["overlapping_rejection_isolation"])
            self.assertEqual(report["results"][5]["queries"], ["Where was Mira Vale born?"])
            self.assertEqual(report["results"][9]["queries"], ["Who published The Glass Orchard?"])
            self.assertTrue(all(report["removal_observations"].values()))
            self.assertEqual(report["results"][9]["removed_memory"], [list(birth)])
            self.assertNotIn(list(birth), report["results"][10]["submitted"])
            self.assertEqual(report["results"][11]["predict_answer"], ["Cedar City", "Linden City"])
            self.assertEqual(report["results"][12]["predict_answer"], ["Linden City", "Cedar City"])
            self.assertEqual(report["results"][12]["state_before"]["memory"], [])
            self.assertEqual(report["results"][12]["state_before"]["question"],
                             validation.RANKING_QUESTION)
            self.assertEqual(report["results"][12]["newly_rejected"], [list(validation.RESIDENCE)])
            for record in report["results"][1:]:
                self.assertTrue(record["old_state_unchanged"])
                self.assertTrue(record["state_rules_match"])
            self.assertNotIn(config.api_key, json.dumps(report))
            with (root / "tmp_usage.csv").open(encoding="utf-8", newline="") as stream:
                self.assertEqual(len(list(csv.DictReader(stream))), 15)

    def test_failure_stops_suite_closes_client_and_preserves_previous_state(self) -> None:
        """Stop on malformed content or exhausted timeout without leaking detail.

        A successful partial round precedes the failure, so the report must retain
        real nonempty prior state. Only transport errors spend the retry budget.
        """
        for timeout in (False, True):
            with self.subTest(timeout=timeout):
                (ROOT / "tmp").mkdir(exist_ok=True)
                with tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="tmp_acceptance_failure_") as directory:
                    root = Path(directory)
                    config = LLMConfig("gpt-4o-mini", "offline-secret", "https://test.invalid/v1", 2, 2)
                    client = LLMClient(config, root / "tmp_usage.csv")
                    first = self.response(completion([validation.AUTHOR], [], ["Where was Mira Vale born?"]))
                    bad = completion([], [], [])
                    bad["choices"][0]["message"]["content"] = "sensitive-failed-content"
                    failures = ([requests.Timeout("sensitive-exception-detail")] * 3
                                if timeout else [self.response(bad)])
                    with patch.object(validation, "ROOT", root), patch.object(
                        validation, "load_llm_config", return_value=config,
                    ), patch.object(validation, "LLMClient", return_value=client), patch(
                        "requests.adapters.HTTPAdapter.send", side_effect=[first] + failures,
                    ) as send, patch("src.agent.llm_client.time.sleep"), patch.object(
                        client, "close", wraps=client.close,
                    ) as close, redirect_stdout(io.StringIO()) as output:
                        self.assertEqual(validation.main(["--suite", "multi", "--prompt-variant", "retrieval_questions"]), 1)
                        self.assertEqual(send.call_count, 4 if timeout else 2)
                        close.assert_called_once()
                    report_text = next((root / "tmp").glob("tmp_*.json")).read_text(encoding="utf-8")
                    report = json.loads(report_text)
                    failed = report["results"][-1]
                    self.assertEqual(report["logical_calls_started"], 2)
                    self.assertEqual(failed["case"], "multi.recovery")
                    self.assertTrue(failed["old_state_unchanged"])
                    self.assertEqual(failed["state_before"], failed["state_after"])
                    self.assertEqual(failed["state_before"]["memory"], [list(validation.AUTHOR)])
                    for private in (config.api_key, "sensitive-failed-content", "sensitive-exception-detail"):
                        self.assertNotIn(private, report_text + output.getvalue())
