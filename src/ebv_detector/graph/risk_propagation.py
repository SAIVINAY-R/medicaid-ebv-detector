"""Personalized PageRank (PPR) Seed Risk Propagation Engine.

Propagates risk scores from known-bad seeds (LEIE, SAM, terminations, CAF suspensions)
through the relationship network. Nodes closely linked or topologically proximate to bad seeds
accumulate high PageRank mass, providing an advisory proximity score.
"""

from collections import defaultdict
import math
from typing import Dict, List, Optional, Set

from .schema import NodeType
from .store import GraphStore
from .supernodes import SupernodeManager


class SeedRiskPropagator:
    """Computes Personalized PageRank starting strictly from known-bad seeds."""

    def __init__(
        self,
        store: GraphStore,
        supernode_mgr: Optional[SupernodeManager] = None,
        restart_prob: float = 0.15,
        max_iterations: int = 50,
        tolerance: float = 1e-6
    ):
        self.store = store
        self.supernode_mgr = supernode_mgr or SupernodeManager(store)
        self.restart_prob = restart_prob
        self.max_iterations = max_iterations
        self.tolerance = tolerance

    def compute_personalized_pagerank(self) -> Dict[str, float]:
        """Execute Personalized PageRank power iteration localized to seed nodes.
        
        Returns:
            Dict[node_id, ppr_risk_score]
        """
        all_nodes = self.store.get_all_nodes()
        if not all_nodes:
            return {}

        seed_nodes = self.store.get_all_seeds()
        if not seed_nodes:
            # If no seeds exist, no risk propagates
            return {nid: 0.0 for nid in all_nodes}

        seed_ids = {s.node_id for s in seed_nodes}
        num_seeds = len(seed_ids)
        restart_vector: Dict[str, float] = {
            nid: (1.0 / num_seeds if nid in seed_ids else 0.0)
            for nid in all_nodes
        }

        # Build adjacency graph with supernode-damped weights
        cur = self.store.conn.cursor()
        cur.execute("SELECT source_id, target_id, weight FROM graph_edges")
        
        # Undirected adjacency list: node -> {neighbor: weight}
        adj: Dict[str, Dict[str, float]] = defaultdict(dict)
        for r in cur.fetchall():
            u, v, base_w = r["source_id"], r["target_id"], r["weight"]
            eff_w = self.supernode_mgr.get_effective_edge_weight(u, v, base_w)
            adj[u][v] = eff_w
            adj[v][u] = eff_w

        # Compute out-degree total weights for transition probability
        out_weight_sum: Dict[str, float] = {}
        for nid in all_nodes:
            out_weight_sum[nid] = sum(adj[nid].values())

        # Initialize probability vector p with restart vector
        p: Dict[str, float] = restart_vector.copy()

        # Power iteration
        for _ in range(self.max_iterations):
            p_next: Dict[str, float] = defaultdict(float)
            
            # Distribute mass from neighbors
            for u in all_nodes:
                p_u = p[u]
                if p_u <= 0.0:
                    continue
                deg = out_weight_sum.get(u, 0.0)
                if deg > 0.0:
                    for v, w in adj[u].items():
                        trans_prob = w / deg
                        p_next[v] += (1.0 - self.restart_prob) * p_u * trans_prob
                else:
                    # Dangling node jumps back to restart vector
                    for s_id in seed_ids:
                        p_next[s_id] += (1.0 - self.restart_prob) * p_u * (1.0 / num_seeds)

            # Add restart component
            for nid in all_nodes:
                p_next[nid] += self.restart_prob * restart_vector[nid]

            # Check convergence (L1 norm difference)
            l1_diff = sum(abs(p_next[nid] - p[nid]) for nid in all_nodes)
            p = p_next
            if l1_diff < self.tolerance:
                break

        return dict(p)

    def get_risk_score(self, node_id: str, ppr_scores: Optional[Dict[str, float]] = None) -> float:
        """Get relative risk score for specific node (normalized 0.0 to 1.0)."""
        scores = ppr_scores or self.compute_personalized_pagerank()
        raw_val = scores.get(node_id, 0.0)
        # Scale logarithmically for readable 0.0 - 1.0 score
        if raw_val <= 0:
            return 0.0
        # Typical non-seed values are between 1e-4 and 0.2
        scaled = min(1.0, max(0.0, (math.log10(raw_val + 1e-8) + 5.0) / 5.0))
        return round(scaled, 4)

