"""Check graph evaluation and atomic candidate execution with synthetic replies.

These cases establish protocol behavior, not real model semantic correctness.
"""

import copy
import json
import unittest
from unittest.mock import Mock

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
