"""Check graph evaluation and atomic candidate execution with synthetic replies.

These cases establish protocol behavior, not real model semantic correctness.
"""

import copy
import json
import unittest
from unittest.mock import Mock, patch

from src.agent.evidence_decision import derive_decision, resolve_query, decode_branch
from src.agent.memory_management import manage_memory
from src.agent.memory_state import QuestionMemory
from src.agent.memory_response import MEMORY_RESPONSE_FORMAT
from src.agent.validation_error import MemoryValidationError
from test_memory_update import completion


FACT = ("Book", "publisher", "Actual")
LEFT = ("Person A", "born_in", "Town A")
RIGHT = ("Person B", "born_in", "Town B")
GRAPH = sorted([FACT, LEFT, RIGHT])
QUESTION = "Book has exactly one publisher. If Expected, Person A; otherwise Person B."


class IndependentRequirementTests(unittest.TestCase):
    """Fix the independent-query boundary with synthetic protocol descriptions.

    Existing path and conditional tests retain their stricter dependency gates.
    """

    def test_duplicate_groups_are_branch_local(self):
        """Reject duplicate related groups while retaining diagnostic structure.

        Synthetic repeated groups use valid evidence; rejection concerns only
        representation. Separate decoder calls represent separate branches.
        """
        group = {"entity": LEFT[0], "fact_queries": [{"relation": LEFT[1],
            "binding": {"kind": "observed", "evidence_ids": [0]}}]}
        branch = {"requirements": [group, copy.deepcopy(group)]}
        with self.assertRaisesRegex(MemoryValidationError, "decision_queries"):
            decode_branch(branch)
        self.assertEqual(len(decode_branch(branch, allow_duplicate_entities=True)["fact_queries"]), 2)
        for _ in ("true", "false"):
            self.assertEqual(decode_branch({"requirements": [group]})["targets"], [LEFT[0]])

    def test_multiple_objects_and_multiple_relations_remain_independent(self):
        """Derive both answers for separately named objects or relations.

        These fixtures test local contracts, not a model's response to the prompt.
        """
        for graph in ([LEFT, RIGHT], [LEFT, (LEFT[0], "possible_birthplace", RIGHT[2])]):
            graph = sorted(graph)
            groups = [{"entity": entity, "fact_queries": [
                {"relation": t[1], "binding": {"kind": "observed", "evidence_ids": [i]}}
                for i, t in enumerate(graph) if t[0] == entity]}
                for entity in dict.fromkeys(t[0] for t in graph)]
            result = derive_decision({"description": {"kind": "nonconditional",
                "branch": {"requirements": groups}}}, "Person A and Person B", graph)
            self.assertEqual(set(result["answer_candidates"]), {LEFT[2], RIGHT[2]})
            self.assertEqual(set(map(tuple, result["required_triplets"])), set(graph))


def description(value="Expected", unique=True):
    """Create an independent extraction using this graph's stable IDs.

    Args:
        value: Comparison constraint, not an observed fact.
        unique: Include the literal single-value premise when true.

    Returns:
        Structured condition and both branch paths.
    """
    return {"condition": {"kind": "equals", "subject": "Book", "relation": "publisher",
                          "operator": "eq", "value": value,
                          "uniqueness_quote": "Book has exactly one publisher." if unique else ""},
            "branches": {
                "true": {"targets": [LEFT[0]], "fact_queries": [{"target": LEFT[0], "relation": "born_in", "direction": "outgoing"}]},
                "false": {"targets": [RIGHT[0]], "fact_queries": [{"target": RIGHT[0], "relation": "born_in", "direction": "outgoing"}]}}}


def wire(record, graph=GRAPH):
    """Encode a test fixture in the new tagged protocol.

    Args:
        record: Internal fixture with condition and three branch slots.
        graph: Complete request-local graph used to bind fixture facts.

    Returns:
        Wire description retaining any deliberately malformed branch values.
    """
    record = copy.deepcopy(record)
    for branch in record.get("branches", {}).values():
        for query in branch.get("fact_queries", []):
            if "direction" in query:
                direction = query.pop("direction")
                index = 0 if direction == "outgoing" else 2
                ids = [i for i, t in enumerate(sorted(graph))
                       if t[index] == query["target"] and t[1] == query["relation"]]
                query["binding"] = ({"kind": "observed", "evidence_ids": ids}
                                    if ids else {"kind": "missing", "direction": direction})
        branch["requirements"] = [{"entity": target, "fact_queries": [
            {k: v for k, v in q.items() if k != "target"}
            for q in branch["fact_queries"] if q["target"] == target]}
            for target in branch.pop("targets")]
        branch.pop("fact_queries")
    if "condition" not in record:
        return record
    if record["condition"]["kind"] == "none":
        return {"description": {"kind": "nonconditional", "branch": record["branches"]["not_conditional"]}}
    condition = {k: v for k, v in record["condition"].items() if k != "kind"}
    condition["condition_quote"] = "If Expected, Person A; otherwise Person B."
    return {"description": {"kind": "conditional_equals", "condition": condition,
                            "branches": record["branches"]}}


def envelope(record, graph=GRAPH):
    """Serialize one extraction in a normal completion envelope.

    Args:
        record: Untrusted extraction object used by the test.
        graph: Complete request-local graph defining stable evidence IDs.

    Returns:
        Completion with an untouched JSON body.
    """
    result = completion()
    result["choices"][0]["message"]["content"] = json.dumps(wire(record, graph))
    return result


class EvidenceDecisionTests(unittest.TestCase):
    """Verify full-graph evaluation, references and publication gates.

    Caller snapshots must remain unchanged after every failed stage.
    """

    def test_conditional_endpoint_guard_and_correct_answer(self):
        """Reject subject and condition endpoints before publishing a snapshot.

        Both truth branches accept their requested city in two calls; unknown
        conditions expose no answer candidates and preserve existing guards.
        """
        for value, fact in (("Expected", RIGHT), ("Actual", LEFT)):
            record = wire(description(value))
            decision = derive_decision(record, QUESTION, GRAPH)
            self.assertEqual(decision["answer_candidates"], [fact[2]])
            for answer in (fact[0], FACT[0], FACT[2], fact[2]):
                client = Mock()
                client.complete.side_effect = [envelope(description(value)),
                                              completion(GRAPH, [answer], [])]
                state = QuestionMemory(QUESTION, [FACT])
                before = copy.deepcopy(state)
                if answer == fact[2]:
                    result = manage_memory(state, GRAPH, [0.95] * 3, client)
                    self.assertEqual(result.response.predict_answer, (answer,))
                else:
                    with self.assertRaises(MemoryValidationError) as caught:
                        manage_memory(state, GRAPH, [0.95] * 3, client)
                    self.assertEqual(caught.exception.code, "decision_answer_candidate")
                    self.assertEqual(caught.exception.stage, "decision_consistency")
                self.assertEqual(client.complete.call_count, 2)
                self.assertEqual(state, before)
        unknown = derive_decision(wire(description(unique=False)), QUESTION, GRAPH)
        self.assertEqual(unknown["answer_candidates"], [])

    def test_directional_candidates_scan_all_matches_and_requirements(self):
        """Collect opposite endpoints across independent requests without extras.

        Referencing one observed fact still scans every exact relation match.
        Incoming facts, multiple relations and stable deduplication share this rule.
        """
        graph = sorted([("A", "r", "B"), ("A", "r", "C"),
                        ("D", "s", "A"), ("X", "noise", "Y")])
        queries = [{"target": "A", "relation": relation, "direction": direction}
                   for relation, direction in (("r", "outgoing"), ("s", "incoming"))]
        record = description()
        record["condition"]["kind"] = "none"
        record["branches"] = {"not_conditional": {"targets": ["A"], "fact_queries": queries}}
        encoded = wire(record, graph)
        encoded["description"]["branch"]["requirements"][0]["fact_queries"][0]["binding"]["evidence_ids"] = [0]
        decision = derive_decision(encoded, "Find facts of A", graph)
        self.assertEqual(decision["answer_candidates"], ["B", "C", "D"])

    def test_default_matches_explicit_decision_and_update_entry(self):
        """Exercise both public defaults through the real decision parser.

        Exact request equality with the explicit variant protects the adopted
        pipeline; an invalid extraction must stop before selection or publication.
        """
        from src.agent.memory_update import update_memory

        extraction = completion()
        extraction["choices"][0]["message"]["content"] = json.dumps(wire(description()))
        selection = completion([FACT, RIGHT], [RIGHT[2]], [])
        state = QuestionMemory(QUESTION)
        calls = []
        for entry, options in ((manage_memory, {}),
                               (manage_memory, {"prompt_variant": "evidence_decision"}),
                               (update_memory, {})):
            client = Mock()
            client.complete.side_effect = [extraction, selection]
            args = (state, GRAPH, client) if entry is update_memory else (state, GRAPH, [0.95] * 3, client)
            result = entry(*args, **options)
            next_state = result[0] if entry is update_memory else result.state
            self.assertEqual(next_state.memory_triplets, frozenset({FACT, RIGHT}))
            self.assertEqual(next_state.rejected_triplets, frozenset({LEFT}))
            self.assertEqual(client.complete.call_count, 2)
            calls.append(client.complete.call_args_list)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(calls[0], calls[2])
        client = Mock()
        client.complete.return_value = completion()
        with self.assertRaises(MemoryValidationError):
            manage_memory(state, GRAPH, [0.95] * 3, client)
        self.assertEqual(client.complete.call_count, 1)
        self.assertEqual(state, QuestionMemory(QUESTION))

    def test_group_expansion_and_wire_rejection(self):
        """Inherit group entities without accepting duplicated wire targets.

        Multiple relations retain original bindings and never cross entity groups.
        Empty groups and legacy fields fail before the final request.
        """
        binding = {"kind": "missing", "direction": "incoming"}
        query = {"relation": "parent", "binding": binding}
        branch = {"requirements": [
            {"entity": "Person A", "fact_queries": [query, {**query, "relation": "friend"}]},
            {"entity": "Person B", "fact_queries": [query]}]}
        result = decode_branch(branch)
        self.assertEqual(result["targets"], ["Person A", "Person B"])
        self.assertEqual([q["target"] for q in result["fact_queries"]],
                         ["Person A", "Person A", "Person B"])
        self.assertEqual([q["relation"] for q in result["fact_queries"]],
                         ["parent", "friend", "parent"])
        self.assertTrue(all(q["binding"] == binding for q in result["fact_queries"]))
        for bad in ({"targets": [LEFT[0]], "fact_queries": []},
                    {"requirements": []},
                    {"requirements": [{"entity": " ", "fact_queries": [query]}]},
                    {"requirements": [{"entity": LEFT[0], "fact_queries": []}]},
                    {"requirements": [{"entity": LEFT[0], "fact_queries": [
                        {**query, "target": LEFT[2]}]}]}):
            record = wire(description())
            record["description"]["branches"]["true"] = bad
            reply = completion()
            reply["choices"][0]["message"]["content"] = json.dumps(record)
            client = Mock()
            client.complete.return_value = reply
            state = QuestionMemory(QUESTION, [LEFT], [RIGHT])
            before = copy.deepcopy(state)
            with self.assertRaises(MemoryValidationError):
                manage_memory(state, GRAPH, [0.95] * 3, client,
                              prompt_variant="evidence_decision")
            self.assertEqual(client.complete.call_count, 1)
            self.assertEqual(state, before)

    def test_endpoint_bindings_and_false_missing_are_atomic(self):
        """Resolve endpoints while rejecting false gaps before final generation.

        Invalid IDs, mismatched endpoints, self loops and mixed roles must leave
        nonempty memory and existing permanent rejections intact.
        """
        query = {"target": LEFT[0], "relation": "born_in"}
        incoming = ("Town", "born_in", LEFT[0])
        for graph, direction in (([LEFT], "outgoing"), ([incoming], "incoming")):
            self.assertEqual(resolve_query({**query, "binding": {
                "kind": "observed", "evidence_ids": [0]}}, graph)["direction"], direction)
        self.assertEqual(resolve_query({**query, "binding": {
            "kind": "missing", "direction": "incoming"}}, [])["direction"], "incoming")
        bad_bindings = [
            ({"kind": "observed", "evidence_ids": ids}, GRAPH)
            for ids in ([], [-1], [True], [99], [0], [2])]
        bad_bindings += [({"kind": "missing", "direction": d}, GRAPH)
                         for d in ("incoming", "outgoing")]
        bad_bindings += [({"kind": "observed", "evidence_ids": [0]},
                         [(LEFT[0], "born_in", LEFT[0])]),
                        ({"kind": "observed", "evidence_ids": [0, 1]},
                         sorted([LEFT, incoming]))]
        for binding, graph in bad_bindings:
            with self.assertRaises(MemoryValidationError):
                resolve_query({**query, "binding": binding}, graph)
        for binding in ({"kind": "missing", "direction": "incoming"},
                        {"kind": "observed", "evidence_ids": [99]}):
            record = wire(description())
            record["description"]["branches"]["false"]["requirements"][0]["fact_queries"][0]["binding"] = binding
            state = QuestionMemory(QUESTION, [LEFT], [("Old", "edge", "Rejected")])
            client = Mock()
            client.complete.return_value = envelope(record)
            with self.assertRaises(MemoryValidationError):
                manage_memory(state, GRAPH, [0.95] * 3, client,
                              prompt_variant="evidence_decision")
            self.assertEqual(client.complete.call_count, 1)
            self.assertEqual(state, QuestionMemory(QUESTION, [LEFT], [("Old", "edge", "Rejected")]))

    def test_final_schema_and_strict_failure_boundary(self):
        """Constrain final generation without repairing invalid model content.

        The reproduced fenced final reply must still fail atomically even when
        it contains the correct evidence. Other variants keep their request body.
        """
        state = QuestionMemory(QUESTION, [LEFT])
        for final, valid in ((completion([FACT, RIGHT], [RIGHT[2]]), True),
                             (completion([FACT], [FACT[2]]), False)):
            client = Mock()
            client.complete.side_effect = [envelope(description()), final]
            if not valid:
                with self.assertRaises(MemoryValidationError):
                    manage_memory(state, GRAPH, [0.95] * 3, client,
                                  prompt_variant="evidence_decision")
            else:
                manage_memory(state, GRAPH, [0.95] * 3, client,
                              prompt_variant="evidence_decision")
            self.assertEqual(client.complete.call_args_list[1].kwargs,
                             {"response_format": MEMORY_RESPONSE_FORMAT})
        fenced = completion([FACT, RIGHT], [RIGHT[2]])
        body = fenced["choices"][0]["message"]["content"]
        fenced["choices"][0]["message"]["content"] = '```json\n' + body + '\n```'
        client.complete.side_effect = [envelope(description()), fenced]
        with self.assertRaises(MemoryValidationError) as caught:
            manage_memory(state, GRAPH, [0.95] * 3, client, prompt_variant="evidence_decision")
        self.assertEqual(caught.exception.code, "invalid_json")
        self.assertEqual(state, QuestionMemory(QUESTION, [LEFT]))
        for variant in ("paper", "retrieval_questions", "answer_ranking"):
            client = Mock()
            client.complete.return_value = completion([FACT, RIGHT], [RIGHT[2]])
            manage_memory(state, GRAPH, [0.95] * 3, client, prompt_variant=variant)
            self.assertEqual(client.complete.call_args.kwargs, {})

    def test_query_matching_and_object_specific_gaps(self):
        """Resolve exact directional facts without collecting unrelated neighbors.

        Multiple matches are retained; absent objects retain separate gaps even
        when another object has facts for the same relation.
        """
        record = description(unique=False)
        extra = (LEFT[0], "born_in", "Town C")
        unrelated = (LEFT[0], "lives_in", "Town D")
        result = derive_decision(wire(record, [FACT, LEFT, extra, unrelated]), QUESTION, [FACT, LEFT, extra, unrelated])
        self.assertEqual(set(result["required_triplets"]), {FACT, LEFT, extra})
        gaps = result["missing_relations"]
        self.assertIn({"target": RIGHT[0], "relation": "born_in", "direction": "outgoing"}, gaps)
        self.assertFalse(any(q["target"] == LEFT[0] for q in gaps))
        record["branches"]["true"]["fact_queries"][0]["direction"] = "incoming"
        incoming = ("Town E", "born_in", LEFT[0])
        result = derive_decision(wire(record, [incoming]), QUESTION, [incoming])
        self.assertEqual(result["required_triplets"], [incoming])

    def test_uncovered_target_and_final_path_omission(self):
        """Reject incomplete requests and omitted answer evidence atomically.

        The final response cannot permanently reject a fact found by a query.
        """
        record = description()
        record["branches"]["false"]["targets"].append(LEFT[0])
        client = Mock()
        client.complete.return_value = envelope(record)
        state = QuestionMemory(QUESTION, [LEFT])
        with self.assertRaises(MemoryValidationError):
            manage_memory(state, GRAPH, [0.95] * 3, client, prompt_variant="evidence_decision")
        self.assertEqual(client.complete.call_count, 1)
        client.complete.side_effect = [envelope(description()), completion([FACT], [FACT[2]])]
        with self.assertRaises(MemoryValidationError) as caught:
            manage_memory(state, GRAPH, [0.95] * 3, client, prompt_variant="evidence_decision")
        self.assertEqual(caught.exception.code, "decision_required_omitted")
        self.assertEqual(state, QuestionMemory(QUESTION, [LEFT]))

    def test_nonconditional_ranking_and_unknown_answer_gate(self):
        """Retain nonconditional multi-target sorting and unknown-condition gates.

        Ranking still executes after valid selection; an unknown condition may
        retain both paths but cannot publish an answer even from a graph entity.
        """
        record = description()
        record["condition"] = dict.fromkeys(record["condition"], "")
        record["condition"]["kind"] = "none"
        for branch in record["branches"].values():
            branch.update(targets=[], fact_queries=[])
        record["branches"]["not_conditional"] = {}
        record["branches"]["not_conditional"].update(
            targets=[LEFT[0], RIGHT[0]], fact_queries=[q for b in description()["branches"].values() for q in b["fact_queries"]])
        client = Mock()
        client.complete.side_effect = [envelope(record),
            completion([LEFT, RIGHT], [LEFT[2], RIGHT[2]]),
            envelope({"predict_answer": [RIGHT[2], LEFT[2]]})]
        result = manage_memory(QuestionMemory("Where were Person A and Person B born?"), GRAPH, [0.95] * 3,
                               client, prompt_variant="evidence_decision")
        self.assertEqual(result.response.predict_answer, (RIGHT[2], LEFT[2]))
        self.assertEqual(client.complete.call_count, 3)
        client.complete.side_effect = [envelope(description(unique=False)),
                                      completion(GRAPH, [RIGHT[2]])]
        with self.assertRaises(MemoryValidationError) as caught:
            manage_memory(QuestionMemory(QUESTION), GRAPH, [0.95] * 3,
                          client, prompt_variant="evidence_decision")
        self.assertEqual(caught.exception.code, "decision_unknown_answer")

    def test_value_evaluation(self):
        """Distinguish equality, unique inequality and open-world uncertainty.

        Condition facts are discovered even though neither branch references them.
        """
        for value, unique, expected in (("Actual", True, "true"), ("Expected", True, "false"),
                                        ("Expected", False, "unknown")):
            result = derive_decision(wire(description(value, unique)), QUESTION, GRAPH)
            self.assertEqual(result["condition_status"], expected)
            self.assertEqual(result["condition_triplets"], [FACT])
            self.assertIn(FACT, result["required_triplets"])
            if expected == "unknown":
                self.assertEqual(set(result["required_triplets"]), set(GRAPH))
        record = description()
        result = derive_decision(wire(record, []), QUESTION, [])
        self.assertEqual(result["condition_status"], "unknown")
        self.assertEqual(result["required_triplets"], [])
        self.assertEqual({q["target"] for q in result["missing_relations"]}, {"Book", "Person A", "Person B"})

    def test_invalid_extraction_is_atomic(self):
        """Reject invalid IDs, unsupported predicates and single-value conflicts.

        No failure may initiate final selection or publish rejection state.
        """
        cases = []
        for bad in ([], [{"target": "Other", "relation": "born_in", "direction": "outgoing"}], [{"target": RIGHT[0], "relation": "born_in", "direction": "unsupported"}]):
            record = description()
            record["branches"]["false"]["fact_queries"] = bad
            cases.append((record, GRAPH))
        for key, value in (("operator", "ne"),
                           ("uniqueness_quote", "invented premise")):
            record = description()
            record["condition"][key] = value
            cases.append((record, GRAPH))
        record = description()
        record["branches"]["not_conditional"] = {"targets": ["Book"], "fact_queries": []}
        cases.extend([(record, GRAPH),
                      (description(), GRAPH + [("Book", "publisher", "Expected")])])
        for record, graph in cases:
            client = Mock()
            client.complete.return_value = envelope(record)
            state = QuestionMemory(QUESTION)
            with self.assertRaises(MemoryValidationError):
                manage_memory(state, graph, [0.95] * len(graph), client, prompt_variant="evidence_decision")
            self.assertEqual(client.complete.call_count, 1)
            self.assertEqual(state, QuestionMemory(QUESTION))

    def test_final_consumes_derived_facts(self):
        """Pass derived targets and remove only the satisfied condition gap.

        Final omissions become permanent rejection under the existing rule.
        """
        record = description()
        client = Mock()
        client.complete.side_effect = [envelope(record), completion([FACT, RIGHT], [RIGHT[2]])]
        state = QuestionMemory(QUESTION, [LEFT])
        result = manage_memory(state, [FACT, RIGHT], [0.95] * 2, client, prompt_variant="evidence_decision")
        self.assertEqual(result.state.rejected_triplets, {LEFT})
        prompt = client.complete.call_args_list[1].args[0][0]["content"]
        active = json.loads(prompt.split("Return only the existing three output fields.\n")[-1])
        self.assertEqual(active["answer_targets"], [RIGHT[0]])
        self.assertEqual(active["missing_relations"], [])
        self.assertEqual(state.memory_triplets, {LEFT})

    def test_strict_format_and_final_gates(self):
        """Keep strict envelopes, original answer validation and required evidence.

        Every malformed stage leaves the original empty snapshot intact.
        """
        first = envelope(description())
        cases = []
        for content in ('```json\n{}\n```', '{}', '{"x":1,"x":2}', '{"x":NaN}'):
            reply = copy.deepcopy(first)
            reply["choices"][0]["message"]["content"] = content
            cases.append([reply])
        for key, value in (("finish_reason", "length"), ("refusal", "refused")):
            reply = copy.deepcopy(first)
            target = reply["choices"][0] if key == "finish_reason" else reply["choices"][0]["message"]
            target[key] = value
            cases.append([reply])
        cases.extend([[first, completion([FACT], [FACT[2]])],
                      [first, completion([FACT, RIGHT], ["External"])],
                      [first, completion([FACT, RIGHT], questions=["What?"])],
                      [first, completion([FACT, RIGHT], [RIGHT[2]], ["What?"])],
                      [first, OSError()]])
        for replies in cases:
            state = QuestionMemory(QUESTION)
            client = Mock()
            client.complete.side_effect = replies
            with self.assertRaises((ValueError, OSError)):
                manage_memory(state, GRAPH, [0.95] * 3, client, prompt_variant="evidence_decision")
            self.assertEqual(state, QuestionMemory(QUESTION))
            self.assertEqual(client.complete.call_count, len(replies))


    def test_tagged_protocol_rejects_mixed_and_missing_quotes(self):
        """Reject the real contradictory reply and malformed tagged structures.

        Each invalid extraction stops before final selection and preserves both
        nonempty memory and an existing permanent rejection set.
        """
        old = json.loads('{"condition":{"kind":"none","subject":"","relation":"","operator":"","value":"","uniqueness_quote":""},"branches":{"true":{"targets":["Mira Vale"],"fact_queries":[{"target":"Mira Vale","relation":"born_in","direction":"outgoing"}]},"false":{"targets":["Nolan Reed"],"fact_queries":[{"target":"Nolan Reed","relation":"born_in","direction":"outgoing"}]},"not_conditional":{"targets":[],"fact_queries":[]}}}')
        valid = wire(description())
        cases = [old]
        mixed = copy.deepcopy(valid)
        mixed["description"]["branch"] = {}
        cases.append(mixed)
        for quote in (None, "invented condition", ""):
            bad = copy.deepcopy(valid)
            if quote is None:
                del bad["description"]["condition"]["condition_quote"]
            else:
                bad["description"]["condition"]["condition_quote"] = quote
            cases.append(bad)
        for record in cases:
            state = QuestionMemory(QUESTION, [LEFT], [("Other", "edge", "Value")])
            client = Mock()
            client.complete.return_value = envelope(record)
            with self.assertRaises(MemoryValidationError):
                manage_memory(state, GRAPH, [0.95] * 3, client, prompt_variant="evidence_decision")
            self.assertEqual(client.complete.call_count, 1)
            self.assertEqual(state, QuestionMemory(QUESTION, [LEFT], [("Other", "edge", "Value")]))

    def test_same_condition_across_complete_and_missing_graphs(self):
        """Evaluate identical extraction against both outcomes and missing facts.

        Public orchestration must forward the graph-derived status and preserve
        both answer paths while the condition remains unknown.
        """
        record = description()
        for graph, status, selected, answers, questions in (
            (GRAPH, "false", [FACT, RIGHT], [RIGHT[2]], []),
            ([LEFT, RIGHT, ("Book", "publisher", "Expected")], "true",
             [LEFT, ("Book", "publisher", "Expected")], [LEFT[2]], []),
            ([LEFT, RIGHT], "unknown", [LEFT, RIGHT], [],
             ['Which entity does "Book" connect to through relation "publisher"?']),
        ):
            client = Mock()
            client.complete.side_effect = [envelope(record, graph), completion(selected, answers, questions)]
            result = manage_memory(QuestionMemory(QUESTION), graph, [0.95] * len(graph),
                                   client, prompt_variant="evidence_decision")
            prompt = client.complete.call_args_list[1].args[0][0]["content"]
            active = json.loads(prompt.split("Return only the existing three output fields.\n")[-1].split("\nAllowed retrieval questions:")[0])
            self.assertEqual(active["condition_status"], status)
            self.assertEqual(set(map(tuple, active["required_triplets"])), set(selected))
            self.assertEqual(result.state.memory_triplets, set(selected))
            if status == "unknown":
                self.assertIn("condition_unknown", str(active["missing_relations"]))


class TwoHopDefaultEntryTests(unittest.TestCase):
    """Establish bounded path acceptance through the public default transaction.

    Synthetic extraction and selection exercise real parsing, graph evaluation,
    thresholding and publication. No private configuration or HTTP is involved.
    """

    question = "Which city was the author of The Glass Orchard born in?"
    author = ("The Glass Orchard", "written_by", "Mira Vale")
    birth = ("Mira Vale", "born_in", "Linden City")
    distractor = ("The Copper Harbor", "written_by", "Nolan Reed")
    orphan_birth = ("Nolan Reed", "born_in", "Harbor City")
    author_query = 'Find triples involving "The Glass Orchard" with relation "written_by".'
    birth_query = 'Find triples involving "Mira Vale" with relation "born_in".'

    def extraction(self, graph=()):
        """Build a fresh two-hop response without binding an intermediate name.

        Args:
            graph: Complete sorted-ID evidence available in this transaction.

        Returns:
            Wire description rooted in the literal question entity, with exact
            outgoing relation names shared by all controlled scenarios.
        """
        return {"description": {
            "kind": "nonconditional_path", "root_entity": self.author[0],
            "steps": [{"relation": relation, "binding": (
                {"kind": "observed", "evidence_ids": [sorted(graph).index(edge)]}
                if edge in graph and self.author in graph else
                {"kind": "missing"})}
                for relation, edge in (("written_by", self.author), ("born_in", self.birth))]}}

    def run_round(self, state, graph, scores, selected, answers, questions,
                  submitted, required, gaps, candidates):
        """Assert both requests and the returned snapshot for one valid round.

        Args:
            state: Actual caller snapshot, preserved after the call.
            graph: Controlled retrieval rows before thresholding and exclusions.
            scores: Positionally aligned refined scores.
            selected: Synthetic final selection and expected next memory.
            answers: Exact expected final answer sequence.
            questions: Exact expected next retrieval sequence.
            submitted: Expected full graph visible to both LLM stages.
            required: Expected code-protected path prefixes.
            gaps: Expected currently reachable missing relation queries.
            candidates: Expected code-derived terminal answer entities.

        Returns:
            Public result whose state can be inherited by the next round.
        """
        before = copy.deepcopy(state)
        client = Mock()
        client.complete.side_effect = [envelope(self.extraction(submitted)),
                                       completion(selected, answers, questions)]
        # Deliberately omit prompt_variant: every case must use the public default.
        result = manage_memory(state, graph, scores, client)
        self.assertEqual(client.complete.call_count, 2)
        first = client.complete.call_args_list[0].args[0][0]["content"]
        numbered = json.loads(first.split("\nNumbered knowledge: ", 1)[1])
        self.assertEqual(numbered, [[i, list(t)] for i, t in enumerate(sorted(submitted))])
        final = client.complete.call_args_list[1].args[0][0]["content"]
        knowledge, remainder = final.split("Knowledge: ", 1)[1].split(
            "\n### Validated Evidence Decision", 1)
        self.assertEqual(json.loads(knowledge), [list(t) for t in sorted(submitted)])
        active_text = remainder.split("Return only the existing three output fields.\n", 1)[1]
        active = json.loads(active_text.split("\nAllowed retrieval questions:", 1)[0])
        self.assertEqual(active["path"], self.extraction(submitted)["description"])
        self.assertEqual(set(map(tuple, active["required_triplets"])), set(required))
        self.assertEqual(active["missing_relations"], gaps)
        self.assertEqual(active["answer_candidates"], candidates)
        if gaps:
            allowed = final.split("\nAllowed retrieval questions: ", 1)[1].split("\n", 1)[0]
            self.assertEqual(json.loads(allowed), questions)
        else:
            self.assertNotIn("\nAllowed retrieval questions:", final)
        self.assertEqual(result.response.selected_triplets, tuple(selected))
        self.assertEqual(result.response.predict_answer, tuple(answers))
        self.assertEqual(result.response.subsequent_question, tuple(questions))
        self.assertEqual(result.state.memory_triplets, frozenset(selected))
        self.assertEqual(result.state.rejected_triplets,
                         state.rejected_triplets | (set(submitted) - set(selected)))
        self.assertEqual(result.state.original_question, state.original_question)
        self.assertEqual(state, before)
        self.assertIsNot(result.state, state)
        return result

    def test_incident_gap_resolves_from_either_endpoint(self):
        """Defer missing direction until a real edge establishes endpoint roles.

        The same predicate can resolve through either endpoint. False missing
        declarations fail even when the only observed fact is reversed.
        """
        from src.agent.evidence_decision import derive_decision
        partial = self.extraction([self.author])
        gap = derive_decision(partial, self.question, [self.author])["missing_relations"]
        self.assertEqual(gap, [{"target": self.author[2], "relation": "born_in", "scope": "incident"}])
        for edge in (self.birth, self.birth[::-1]):
            graph = sorted([self.author, edge])
            with self.assertRaises(MemoryValidationError):
                derive_decision(partial, self.question, graph)
            observed = self.extraction(graph)
            observed["description"]["steps"][1]["binding"] = {
                "kind": "observed", "evidence_ids": [graph.index(edge)]}
            resolved = derive_decision(observed, self.question, graph)
            self.assertEqual(resolved["answer_candidates"], [self.birth[2]])
            self.assertEqual(resolved["missing_relations"], [])

    def test_single_sufficient_insufficient_and_empty(self):
        """Cover each single-round fixture with exact path and query assertions.

        Complete evidence has no false gap; partial and empty graphs ask only
        for the next reachable relation and never name an unobserved author.
        """
        for name, graph, answers, questions, gaps, candidates in (
            ("single.sufficient", [self.author, self.birth], [self.birth[2]], [], [], [self.birth[2]]),
            ("single.insufficient", [self.author], [], [self.birth_query],
             [{"target": self.author[2], "relation": "born_in", "scope": "incident"}], []),
            ("single.empty", [], [], [self.author_query],
             [{"target": self.author[0], "relation": "written_by", "scope": "incident"}], []),
        ):
            with self.subTest(case=name):
                self.run_round(QuestionMemory(self.question), graph, [0.95] * len(graph),
                               graph, answers, questions, graph, graph, gaps, candidates)

    def test_multi_partial_recovery_and_exclusion_probe(self):
        """Inherit actual snapshots across the three multi-round fixtures.

        The threshold-equal birth fact remains retrievable, then connects to
        historical author memory. A submitted omitted distractor stays masked.
        """
        graph = [self.author, self.birth, self.distractor]
        initial = QuestionMemory(self.question)
        partial = self.run_round(
            initial, graph, [0.95, 0.7, 0.95], [self.author], [], [self.birth_query],
            [self.author, self.distractor], [self.author],
            [{"target": self.author[2], "relation": "born_in", "scope": "incident"}], [])
        self.assertNotIn(self.birth, partial.state.excluded_triplets)
        self.assertEqual(partial.state.filter_expansion_triplets(graph), [self.birth])
        self.assertEqual(partial.state.rejected_triplets, {self.distractor})
        retrieval = Mock(return_value=graph)
        recovered = self.run_round(
            partial.state, retrieval(partial.response.subsequent_question), [0.95] * 3,
            [self.author, self.birth], [self.birth[2]], [],
            [self.author, self.birth], [self.author, self.birth], [], [self.birth[2]])
        retrieval.assert_called_once_with((self.birth_query,))
        probe = self.run_round(
            recovered.state, graph, [0.95] * 3, [self.author, self.birth], [self.birth[2]], [],
            [self.author, self.birth], [self.author, self.birth], [], [self.birth[2]])
        self.assertEqual(probe.state, recovered.state)
        self.assertEqual(partial.state.memory_triplets, {self.author})
        self.assertEqual(initial, QuestionMemory(self.question))

    def test_preexisting_rejection_is_filtered_without_requiring_model_omission(self):
        """Test an explicitly seeded rejection independently of model preference.

        Both request graphs must exclude a high-scoring rejected distractor;
        memory remains available even when its current retrieval score is low.
        """
        state = QuestionMemory(self.question, [self.author], [self.distractor])
        self.assertEqual(state.filter_expansion_triplets(
            [self.distractor, self.author, self.birth]), [self.birth])
        self.run_round(state, [self.distractor, self.author, self.birth], [0.95, 0.0, 0.95],
                       [self.author, self.birth], [self.birth[2]], [],
                       [self.author, self.birth], [self.author, self.birth], [], [self.birth[2]])

    def test_orphan_birth_cannot_replace_missing_root_connection(self):
        """Ask for the author when even the correct birth lacks its bridge.

        A disconnected known city cannot produce a terminal candidate or a
        downstream query anchored to the otherwise unbound person.
        """
        self.run_round(QuestionMemory(self.question), [self.birth], [0.95], [], [],
                       [self.author_query], [self.birth], [],
                       [{"target": self.author[0], "relation": "written_by",
                         "scope": "incident"}], [])

    def test_disconnected_birth_does_not_join_the_correct_path(self):
        """Keep the reachable city as the only terminal beside unrelated births.

        Selecting optional unrelated evidence is allowed; it must not expand the
        terminal candidates or make a different city a valid answer.
        """
        graph = [self.author, self.birth, self.orphan_birth]
        self.run_round(QuestionMemory(self.question), graph, [0.95] * 3, graph,
                       [self.birth[2]], [], graph, [self.author, self.birth], [], [self.birth[2]])

    def test_one_of_two_authors_missing_birth_blocks_answer(self):
        """Protect every author prefix while one reachable candidate has a gap.

        The completed branch remains protected, but its city alone cannot end
        retrieval while the second author's birth relation is unresolved.
        """
        other_author = (self.author[0], "written_by", "Nolan Reed")
        graph = [self.author, other_author, self.birth]
        query = 'Which entity does "Nolan Reed" connect to through relation "born_in"?'
        self.run_round(QuestionMemory(self.question), graph, [0.95] * 3, graph, [],
                       [query], graph, graph,
                       [{"target": "Nolan Reed", "relation": "born_in", "direction": "outgoing"}],
                       [self.birth[2]])

    def test_final_failures_precede_snapshot_construction(self):
        """Reject omitted prefixes, invalid terminal answers and premature answers.

        Incorrect answers remain selected graph endpoints so the path gate,
        rather than generic entity membership, must reject them. No failure
        may construct a candidate snapshot or erase prior memory/rejections.
        """
        other_author = (self.author[0], "written_by", "Nolan Reed")
        full = [self.author, self.birth, self.orphan_birth]
        for name, graph, final, code in (
            ("omitted_author", full, completion([self.birth], [self.birth[2]]),
             "decision_required_omitted"),
            ("intermediate_answer", full, completion(full, [self.author[2]]),
             "decision_path_answer"),
            ("disconnected_city", full, completion(full, [self.orphan_birth[2]]),
             "decision_path_answer"),
            ("unconnected_birth", [self.birth], completion([self.birth], [self.birth[2]]),
             "decision_path_incomplete"),
            ("partial_other_author", [self.author, other_author, self.birth],
             completion([self.author, other_author, self.birth], [self.birth[2]]),
             "decision_path_incomplete"),
            ("wrong_gap_query", [self.author], completion([self.author], questions=[self.author_query]),
             "decision_questions"),
        ):
            with self.subTest(case=name):
                state = QuestionMemory(self.question, [self.distractor], [("Old", "edge", "Rejected")])
                before = copy.deepcopy(state)
                client = Mock()
                client.complete.side_effect = [envelope(self.extraction(set(graph) | state.memory_triplets)), final]
                with patch.object(QuestionMemory, "__post_init__",
                                  side_effect=AssertionError("Unexpected snapshot construction")) as constructor:
                    with self.assertRaises(MemoryValidationError) as caught:
                        manage_memory(state, graph, [0.95] * len(graph), client)
                self.assertEqual((caught.exception.code, caught.exception.stage),
                                 (code, "decision_consistency"))
                constructor.assert_not_called()
                self.assertEqual(client.complete.call_count, 2)
                self.assertEqual(state, before)

    def test_frontier_bindings_and_invalid_observations(self):
        """Derive incoming roles and reject unconnected or ambiguous references.

        Unreferenced parallel edges remain protected; malformed bindings and
        false gaps fail before a decision can be published.
        """
        from src.agent.evidence_decision import derive_decision
        for reverse_first, reverse_second in ((True, False), (False, True), (True, True)):
            graph = sorted([self.author[::-1] if reverse_first else self.author,
                            self.birth[::-1] if reverse_second else self.birth])
            record = self.extraction()
            for step, edge in zip(record["description"]["steps"],
                                  (self.author[::-1] if reverse_first else self.author,
                                   self.birth[::-1] if reverse_second else self.birth)):
                step["binding"] = {"kind": "observed", "evidence_ids": [graph.index(edge)]}
            decision = derive_decision(record, self.question, graph)
            self.assertEqual(decision["answer_candidates"], [self.birth[2]])
            self.assertEqual(set(decision["required_triplets"]), set(graph))
        graph = sorted([self.author, self.birth, self.orphan_birth])
        for binding in ({"kind": "observed", "evidence_ids": [True]},
                        {"kind": "observed", "evidence_ids": []},
                        {"kind": "observed", "evidence_ids": [-1]},
                        {"kind": "observed", "evidence_ids": [99]},
                        {"kind": "observed", "evidence_ids": [graph.index(self.birth)]},
                        {"kind": "observed", "evidence_ids": [graph.index(self.orphan_birth)]},
                        {"kind": "missing"}):
            record = self.extraction(graph)
            record["description"]["steps"][0]["binding"] = binding
            with self.assertRaises(MemoryValidationError):
                derive_decision(record, self.question, graph)
        for extra in ((self.author[0], "written_by", self.author[0]),
                      (self.author[2], "written_by", self.author[0])):
            graph = sorted([self.author, extra])
            record = self.extraction(graph)
            record["description"]["steps"][0]["binding"]["evidence_ids"] = list(range(2))
            with self.assertRaises(MemoryValidationError):
                derive_decision(record, self.question, graph)

    def test_invalid_path_structures_stop_before_selection(self):
        """Reject malformed or ungrounded plans during the extraction stage.

        Each mutation starts from a valid wire description. Both nonempty
        historical memory and permanent rejection survive without a final call.
        """
        valid = self.extraction()["description"]
        invalid = [
            {**valid, "root_entity": "Mira Vale"},
            {**valid, "root_entity": " "},
            {**valid, "root_entity": 1},
            {**valid, "steps": []},
            {**valid, "steps": valid["steps"][:1]},
            {**valid, "steps": valid["steps"] * 2},
            {**valid, "steps": {}},
            {**valid, "steps": [None, valid["steps"][1]]},
            {**valid, "steps": [{"relation": "written_by", "direction": "sideways"}, valid["steps"][1]]},
            {**valid, "steps": [{"relation": " ", "direction": "outgoing"}, valid["steps"][1]]},
            {**valid, "steps": [{"relation": "written_by"}, valid["steps"][1]]},
            {**valid, "steps": [{**valid["steps"][0], "entity": "Mira Vale"}, valid["steps"][1]]},
            {**valid, "branch": {}},
        ]
        for record in invalid:
            with self.subTest(description=record):
                state = QuestionMemory(self.question, [self.author], [self.distractor])
                before = copy.deepcopy(state)
                client = Mock()
                client.complete.return_value = envelope({"description": record})
                with patch.object(QuestionMemory, "__post_init__",
                                  side_effect=AssertionError("Unexpected snapshot construction")) as constructor:
                    with self.assertRaises(MemoryValidationError) as caught:
                        manage_memory(state, [self.birth], [0.95], client)
                self.assertEqual((caught.exception.code, caught.exception.stage),
                                 ("decision_path", "evidence_decision"))
                constructor.assert_not_called()
                self.assertEqual(client.complete.call_count, 1)
                self.assertEqual(state, before)
