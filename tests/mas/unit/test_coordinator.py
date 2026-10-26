"""Coordinator decisions precede graph construction and respect configuration caps."""

import json
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from raac.collaboration.levels.l3_collaboration import (
    CoordinatorDecision, build_collaboration_graph, coordinate_chain,
)


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.config = {"version": "test-v1", "topology": "chain", "max_rounds": 3,
                       "hard_max_rounds": 4}

    def test_default_and_json_roundtrip(self):
        self.config.pop("topology")
        decision = coordinate_chain(self.config)
        self.assertEqual(json.loads(decision.model_dump_json()), {
            "topology": "chain", "topology_version": "5", "max_rounds": 3,
            "max_review_rounds": None, "source": "fixed_default",
        })
        self.assertEqual(CoordinatorDecision.model_validate_json(decision.model_dump_json()), decision)

    def test_override_does_not_mutate_configuration(self):
        self.assertEqual(coordinate_chain(self.config, max_rounds=1).max_rounds, 1)
        self.assertEqual(coordinate_chain(self.config, max_rounds=4).max_rounds, 4)
        self.assertEqual(self.config["max_rounds"], 3)

    def test_invalid_rounds_and_unimplemented_topology_fail_before_execution(self):
        for rounds in (0, -1, 5, True, "3"):
            with self.subTest(rounds=rounds), self.assertRaises(ValidationError):
                coordinate_chain(self.config, max_rounds=rounds)
        with self.assertRaises(ValidationError):
            coordinate_chain({**self.config, "topology": "debate"})
        with self.assertRaises(ValidationError):
            coordinate_chain({**self.config, "max_rounds": 5})

    def test_graph_dispatch_and_version_guard(self):
        decision = coordinate_chain(self.config)
        module = "raac.collaboration.levels.l3_collaboration.l3_collaboration"
        with patch(module + ".build_chain_graph") as factory:
            nodes = object()
            graph = build_collaboration_graph(decision, nodes)
            factory.assert_called_once_with(nodes, checkpointer=None)
            self.assertIs(graph, factory.return_value)
            with self.assertRaisesRegex(ValueError, "version"):
                build_collaboration_graph(decision.model_copy(update={"topology_version": "old"}), nodes)
            self.assertEqual(factory.call_count, 1)



class ManualTopologyTests(unittest.TestCase):
    def setUp(self):
        self.config = {"version": "test-v1", "topology": "chain", "max_rounds": 3, "hard_max_rounds": 4}

    def test_topology_is_taken_from_configuration(self):
        decision = coordinate_chain({**self.config, "topology": "chain_review_loop"})
        self.assertEqual((decision.topology, decision.topology_version, decision.max_rounds, decision.source),
                         ("chain_review_loop", "1", 3, "fixed_default"))
        self.assertEqual(CoordinatorDecision.model_validate_json(decision.model_dump_json()), decision)

    def test_argument_overrides_configuration_without_mutating_it(self):
        decision = coordinate_chain(self.config, topology="chain_review_loop", max_rounds=2)
        self.assertEqual((decision.topology, decision.max_rounds), ("chain_review_loop", 2))
        self.assertEqual(self.config["topology"], "chain")
        self.assertEqual(coordinate_chain({**self.config, "topology": "chain_review_loop"}, topology="chain").topology, "chain")

    def test_undeclared_topology_is_rejected_before_execution(self):
        for name in ("debate", "star", ""):
            with self.subTest(name=name), self.assertRaises(ValidationError):
                coordinate_chain(self.config, topology=name)
            with self.subTest(config=name), self.assertRaises(ValidationError):
                coordinate_chain({**self.config, "topology": name})

    def test_review_loop_dispatches_to_the_chain_factory_with_its_topology(self):
        decision = coordinate_chain(self.config, topology="chain_review_loop")
        module = "raac.collaboration.levels.l3_collaboration.l3_collaboration"
        with patch(module + ".build_chain_graph") as factory:
            build_collaboration_graph(decision, object())
            self.assertEqual(factory.call_args.kwargs["topology"].topology_id, "chain_review_loop")


class ReviewRoundsConfigTests(unittest.TestCase):
    def setUp(self):
        self.config = {"version": "test-v1", "topology": "chain_review_loop", "max_rounds": 3,
                       "max_review_rounds": 2, "hard_max_rounds": 4}

    def test_review_loop_decision_carries_review_round_cap(self):
        self.assertEqual(coordinate_chain(self.config).max_review_rounds, 2)
        self.assertEqual(coordinate_chain(self.config, max_review_rounds=1).max_review_rounds, 1)
        self.assertEqual(coordinate_chain(self.config, max_review_rounds=0).max_review_rounds, 0)

    def test_plain_chain_has_no_review_round_cap(self):
        self.assertIsNone(coordinate_chain(self.config, topology="chain").max_review_rounds)

    def test_review_round_cap_is_validated(self):
        for value in (-1, 5, True, "2"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                coordinate_chain(self.config, max_review_rounds=value)
