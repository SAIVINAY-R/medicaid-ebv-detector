"""Supernode Detection and Damping Engine.

Mitigates artificial false positives caused by high-degree legitimate entities:
- Commercial registered agent services (e.g., CT Corporation, CSC)
- Academic medical centers / large hospital campuses (thousands of affiliated clinicians)
- Shared email provider domains (e.g., matching @gmail.com domain instead of full email)
- Centralized billing/management organizations

Applies degree-based damping factor to down-weight trivial connections during graph traversal.
"""

from typing import Dict, List, Set

from .schema import NodeType
from .store import GraphStore


class SupernodeManager:
    """Detects and adjusts graph weights for high-degree supernodes."""

    def __init__(self, store: GraphStore, degree_threshold: int = 30, damping_factor: float = 0.1):
        self.store = store
        self.degree_threshold = degree_threshold
        self.damping_factor = damping_factor
        self._supernodes: Set[str] = set()

    def scan_for_supernodes(self) -> Set[str]:
        """Identify all nodes in the graph whose degree exceeds threshold."""
        cur = self.store.conn.cursor()
        cur.execute("""
            SELECT node_id FROM (
                SELECT source_id AS node_id FROM graph_edges
                UNION ALL
                SELECT target_id AS node_id FROM graph_edges
            )
            GROUP BY node_id
            HAVING COUNT(*) >= ?
        """, (self.degree_threshold,))
        
        self._supernodes = {r[0] for r in cur.fetchall()}
        return self._supernodes

    def is_supernode(self, node_id: str) -> bool:
        """Check if a node has been identified as a supernode."""
        if node_id in self._supernodes:
            return True
        return self.store.get_node_degree(node_id) >= self.degree_threshold

    def get_effective_edge_weight(self, source_id: str, target_id: str, base_weight: float = 1.0) -> float:
        """Apply damping if either endpoint is a supernode."""
        if self.is_supernode(source_id) or self.is_supernode(target_id):
            return base_weight * self.damping_factor
        return base_weight
