"""Unit tests for M3 Graph Store, Recursive CTE path traversal, and GR-4 Indirect Ownership."""

import unittest

from ebv_detector.graph.schema import EdgeType, GraphEdge, GraphNode, NodeType
from ebv_detector.graph.store import GraphStore
from ebv_detector.graph.traversal import GraphTraversalEngine


class TestGraphAndOwnership(unittest.TestCase):

    def setUp(self):
        self.store = GraphStore()
        self.traversal = GraphTraversalEngine(self.store, max_hops=3)

    def tearDown(self):
        self.store.close()

    def test_recursive_cte_seed_path_discovery(self):
        # Build 2-hop graph: Provider -> Address -> Seed
        self.store.add_node(GraphNode("P-NEW", NodeType.PROVIDER, "New Provider LLC"))
        self.store.add_node(GraphNode("ADDR-MAILBOX", NodeType.ADDRESS, "123 Main St, PMB 400"))
        self.store.add_node(GraphNode(
            "SEED-TERMINATED-PROVIDER",
            NodeType.SEED,
            "Old Suspended LLC",
            is_seed=True,
            seed_source="STATE_CAF",
            attributes={"reason": "Suspended for kickbacks"}
        ))

        self.store.add_edge(GraphEdge("P-NEW", "ADDR-MAILBOX", EdgeType.USES_ADDRESS))
        self.store.add_edge(GraphEdge("ADDR-MAILBOX", "SEED-TERMINATED-PROVIDER", EdgeType.USES_ADDRESS))

        paths = self.traversal.find_paths_to_seeds("P-NEW")
        self.assertEqual(len(paths), 1)
        p = paths[0]
        self.assertEqual(p.hops, 2)
        self.assertEqual(p.target_node_id, "SEED-TERMINATED-PROVIDER")
        self.assertEqual(p.target_seed_source, "STATE_CAF")

    def test_gr4_indirect_ownership_calculation(self):
        # Scenario:
        # Grandparent Excluded Felon owns 60% of Parent Holding Co
        # Parent Holding Co owns 80% of Applicant Provider
        # Indirect ownership = 0.60 * 0.80 = 0.48 (48% >= 5% statutory threshold)
        self.store.add_node(GraphNode("APPLICANT-ORG", NodeType.PROVIDER, "Applicant Rehab LLC"))
        self.store.add_node(GraphNode("HOLDING-CO", NodeType.ORGANIZATION, "Holding Partners Inc"))
        self.store.add_node(GraphNode(
            "EXCLUDED-FELON-INDIVIDUAL",
            NodeType.SEED,
            "Bad Actor Mastermind",
            is_seed=True,
            seed_source="OIG_LEIE",
            attributes={"reason": "1128(a)(1) healthcare fraud conviction"}
        ))

        self.store.add_edge(GraphEdge(
            "HOLDING-CO", "APPLICANT-ORG", EdgeType.DIRECT_OWNERSHIP, ownership_percentage=0.80
        ))
        self.store.add_edge(GraphEdge(
            "EXCLUDED-FELON-INDIVIDUAL", "HOLDING-CO", EdgeType.DIRECT_OWNERSHIP, ownership_percentage=0.60
        ))

        results = self.traversal.calculate_gr4_indirect_ownership("APPLICANT-ORG")
        self.assertEqual(len(results), 2)
        
        # Check Excluded Felon result
        felon_res = next((r for r in results if r.upstream_entity_id == "EXCLUDED-FELON-INDIVIDUAL"), None)
        self.assertIsNotNone(felon_res)
        self.assertAlmostEqual(felon_res.effective_indirect_pct, 0.48, places=3)
        self.assertTrue(felon_res.meets_5pct_statutory_threshold)
        self.assertTrue(felon_res.is_upstream_excluded_seed)


if __name__ == "__main__":
    unittest.main()
