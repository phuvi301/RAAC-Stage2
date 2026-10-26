"""Check the chain contract before integrating the shared Engine."""

import unittest

from raac.collaboration.topologies import get_topology


class ChainTopologyTests(unittest.TestCase):
    def test_layout_keeps_core_team_and_routes_feedback_through_limit(self):
        topology = get_topology("chain")
        self.assertEqual(
            [step.role for step in topology.steps if step.role is not None],
            ["Environment", "Analyst", "Planner", "Coder", "Tester", "Reviewer"],
        )
        self.assertEqual(topology.incoming_nodes("coder"), ("planner", "limit"))
        self.assertEqual(topology.incoming_nodes("limit"), ("check",))
        self.assertEqual(topology.incoming_nodes("check"), ("tester",))
        self.assertEqual(topology.incoming_nodes("environment"), ())
        self.assertNotIn(("reviewer", "coder"), topology.edges)
        self.assertEqual(topology.exit_node, "result")

    def test_all_edges_reference_known_nodes_and_result_has_no_successor(self):
        topology = get_topology("chain")
        node_ids = {step.node_id for step in topology.steps}
        self.assertEqual(len(node_ids), len(topology.steps))
        for source, target in topology.edges:
            self.assertIn(source, node_ids)
            self.assertIn(target, node_ids)
        self.assertFalse(any(source == "result" for source, _ in topology.edges))
        self.assertEqual(topology.incoming_nodes("analyst"), ("environment",))
        self.assertEqual(topology.incoming_nodes("planner"), ("analyst",))
        self.assertEqual(topology.incoming_nodes("tester"), ("coder",))

    def test_unknown_message_recipient_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown node"):
            get_topology("chain").incoming_nodes("security")

    def test_unimplemented_or_generated_topology_is_rejected(self):
        for topology_id in ("star", "coder_reviewer_loop", "debate", "custom", ""):
            with self.subTest(topology_id=topology_id):
                with self.assertRaisesRegex(ValueError, "Unsupported topology"):
                    get_topology(topology_id)


class ReviewLoopTopologyTests(unittest.TestCase):
    def test_same_team_as_chain_plus_reviewer_feedback_edge(self):
        chain, loop = get_topology("chain"), get_topology("chain_review_loop")
        self.assertEqual(loop.steps, chain.steps)
        self.assertEqual(set(loop.edges) - set(chain.edges), {("reviewer", "coder")})
        self.assertEqual(set(chain.edges) - set(loop.edges), set())
        self.assertEqual(loop.incoming_nodes("coder"), ("planner", "limit", "reviewer"))
        self.assertEqual((loop.entry_node, loop.exit_node), (chain.entry_node, chain.exit_node))
