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
            "topology": "chain", "topology_version": "2", "max_rounds": 3,
            "source": "fixed_default",
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
