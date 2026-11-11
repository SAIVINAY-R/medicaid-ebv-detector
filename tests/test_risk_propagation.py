"""Unit tests for Supernode Damping and Personalized PageRank (PPR) Seed Risk Propagation."""

import unittest

from ebv_detector.graph.risk_propagation import SeedRiskPropagator
from ebv_detector.graph.schema import EdgeType, GraphEdge, GraphNode, NodeType
from ebv_detector.graph.store import GraphStore
from ebv_detector.graph.supernodes import SupernodeManager


class TestRiskPropagation(unittest.TestCase):

    def setUp(self):
        self.store = GraphStore()
        self.supernode_mgr = SupernodeManager(self.store, degree_threshold=5, damping_factor=0.1)
        self.propagator = SeedRiskPropagator(self.store, self.supernode_mgr, restart_prob=0.15)

    def tearDown(self):
        self.store.close()

    def test_supernode_detection_and_damping(self):
        # Create a hub node connected to 6 nodes (degree > 5)
        self.store.add_node(GraphNode("HUB-AGENT", NodeType.ORGANIZATION, "Commercial Registered Agent Service"))
        for i in range(6):
            c_id = f"CLIENT-{i}"
            self.store.add_node(GraphNode(c_id, NodeType.PROVIDER, f"Client {i}"))
            self.store.add_edge(GraphEdge(c_id, "HUB-AGENT", EdgeType.USES_ADDRESS))

        self.assertTrue(self.supernode_mgr.is_supernode("HUB-AGENT"))
        eff_weight = self.supernode_mgr.get_effective_edge_weight("CLIENT-0", "HUB-AGENT", base_weight=1.0)
        self.assertAlmostEqual(eff_weight, 0.1)

    def test_personalized_pagerank_seed_accumulation(self):
        # Network: Seed -> Provider A -> Provider B -> Distant Provider C
        self.store.add_node(GraphNode("SEED-1", NodeType.SEED, "Excluded Seed", is_seed=True, seed_source="LEIE"))
        self.store.add_node(GraphNode("PROV-A", NodeType.PROVIDER, "Direct Partner A"))
        self.store.add_node(GraphNode("PROV-B", NodeType.PROVIDER, "Indirect Partner B"))
        self.store.add_node(GraphNode("PROV-C", NodeType.PROVIDER, "Distant Provider C"))

        self.store.add_edge(GraphEdge("SEED-1", "PROV-A", EdgeType.USES_EFT))
        self.store.add_edge(GraphEdge("PROV-A", "PROV-B", EdgeType.USES_PHONE))
        self.store.add_edge(GraphEdge("PROV-B", "PROV-C", EdgeType.USES_ADDRESS))

        ppr = self.propagator.compute_personalized_pagerank()
        
        # Immediate neighbor PROV-A should have higher PPR mass than distant PROV-C
        self.assertGreater(ppr["PROV-A"], ppr["PROV-C"])
        self.assertGreater(ppr["PROV-B"], ppr["PROV-C"])


if __name__ == "__main__":
    unittest.main()
