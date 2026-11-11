"""Recursive CTE Path Traversal and GR-4 Indirect Ownership Engine.

Implements:
- 2-3 hop recursive CTE path queries from applicant to known-bad seeds
- Bidirectional relationship traversal (shared address, phone, EFT, clinician)
- GR-4 Indirect Ownership multiplication down corporate holding trees (42 CFR 455.104)
- Verification of 5%+ indirect ownership statutory threshold
"""

from collections import defaultdict
from dataclasses import dataclass, field
import sqlite3
from typing import Dict, List, Optional, Set, Tuple

from .schema import EdgeType, NodeType
from .store import GraphStore


@dataclass
class PathResult:
    """Represents a discovered relationship path between two nodes."""
    start_node_id: str
    target_node_id: str
    hops: int
    path_nodes: List[str]
    path_description: str
    is_seed_path: bool = False
    target_seed_source: Optional[str] = None
    target_seed_reason: Optional[str] = None


@dataclass
class IndirectOwnershipResult:
    """Result of GR-4 indirect ownership calculation."""
    upstream_entity_id: str
    downstream_provider_id: str
    effective_indirect_pct: float     # e.g. 0.48 = 48%
    meets_5pct_statutory_threshold: bool
    is_upstream_excluded_seed: bool
    seed_reason: Optional[str] = None
    ownership_paths: List[List[Tuple[str, float]]] = field(default_factory=list)


class GraphTraversalEngine:
    """Executes CTE queries and indirect ownership analysis against GraphStore."""

    def __init__(self, store: GraphStore, max_hops: int = 3):
        self.store = store
        self.max_hops = max_hops

    def find_paths_to_seeds(self, start_node_id: str) -> List[PathResult]:
        """Find all paths from start_node_id to any known-bad seed within max_hops using Recursive CTE."""
        cur = self.store.conn.cursor()
        
        # SQLite recursive CTE traversing undirected edges up to max_hops
        query = """
        WITH RECURSIVE traversal_cte(current_id, path_str, hops) AS (
            -- Base case
            SELECT ?, ?, 0
            
            UNION ALL
            
            -- Outgoing edges
            SELECT e.target_id, t.path_str || '->' || e.target_id, t.hops + 1
            FROM traversal_cte t
            JOIN graph_edges e ON t.current_id = e.source_id
            WHERE t.hops < ?
              AND instr(t.path_str, e.target_id) = 0
            
            UNION ALL
            
            -- Incoming edges
            SELECT e.source_id, t.path_str || '->' || e.source_id, t.hops + 1
            FROM traversal_cte t
            JOIN graph_edges e ON t.current_id = e.target_id
            WHERE t.hops < ?
              AND instr(t.path_str, e.source_id) = 0
        )
        SELECT 
            t.current_id,
            t.path_str,
            t.hops,
            n.is_seed,
            n.seed_source,
            n.label
        FROM traversal_cte t
        JOIN graph_nodes n ON t.current_id = n.node_id
        WHERE n.is_seed = 1 AND t.current_id != ?
        ORDER BY t.hops ASC;
        """
        
        cur.execute(query, (start_node_id, start_node_id, self.max_hops, self.max_hops, start_node_id))
        rows = cur.fetchall()
        
        results = []
        seen_targets: Set[str] = set()
        
        for r in rows:
            target_id = r["current_id"]
            if target_id in seen_targets:
                continue
            seen_targets.add(target_id)
            
            node_seq = r["path_str"].split("->")
            desc = self._format_path_description(node_seq)
            
            seed_node = self.store.get_node(target_id)
            reason = seed_node.attributes.get("reason") if seed_node else None
            
            results.append(PathResult(
                start_node_id=start_node_id,
                target_node_id=target_id,
                hops=r["hops"],
                path_nodes=node_seq,
                path_description=desc,
                is_seed_path=True,
                target_seed_source=r["seed_source"],
                target_seed_reason=reason
            ))
            
        return results

    def _format_path_description(self, node_ids: List[str]) -> str:
        """Create human-readable chain of hops."""
        labels = []
        for nid in node_ids:
            node = self.store.get_node(nid)
            label = f"{node.node_type.value}:{node.label}" if node else nid
            labels.append(label)
        return " -> ".join(labels)

    def calculate_gr4_indirect_ownership(self, target_provider_id: str) -> List[IndirectOwnershipResult]:
        """Calculate indirect ownership for target_provider_id along ownership chains (GR-4).
        
        Multiplies ownership percentages along directed ownership chains:
        (e.g., Parent owns 80% of Child, Grandparent owns 60% of Parent -> Grandparent has 48% indirect ownership).
        Checks against 5% statutory threshold (42 CFR 455.104).
        """
        cur = self.store.conn.cursor()
        
        # Recursive CTE tracking incoming ownership chains and multiplying percentages
        query = """
        WITH RECURSIVE ownership_cte(upstream_id, downstream_id, cumulative_pct, depth, path_str) AS (
            -- Base step: direct parents of target provider
            SELECT 
                source_id AS upstream_id,
                target_id AS downstream_id,
                ownership_percentage AS cumulative_pct,
                1 AS depth,
                source_id || '->' || target_id AS path_str
            FROM graph_edges
            WHERE target_id = ? 
              AND edge_type = 'DIRECT_OWNERSHIP'
              AND ownership_percentage > 0.0
              
            UNION ALL
            
            -- Recursive step: parents of upstream entities
            SELECT 
                e.source_id AS upstream_id,
                o.downstream_id AS downstream_id,
                o.cumulative_pct * e.ownership_percentage AS cumulative_pct,
                o.depth + 1 AS depth,
                e.source_id || '->' || o.path_str AS path_str
            FROM ownership_cte o
            JOIN graph_edges e ON o.upstream_id = e.target_id
            WHERE e.edge_type = 'DIRECT_OWNERSHIP'
              AND e.ownership_percentage > 0.0
              AND o.depth < 5
              AND instr(o.path_str, e.source_id) = 0
        )
        SELECT upstream_id, cumulative_pct, path_str, depth
        FROM ownership_cte
        ORDER BY depth ASC;
        """
        
        cur.execute(query, (target_provider_id,))
        rows = cur.fetchall()
        
        # Aggregate cumulative percentages per upstream entity (in case of multiple paths)
        entity_totals: Dict[str, float] = defaultdict(float)
        entity_paths: Dict[str, List[List[Tuple[str, float]]]] = defaultdict(list)
        
        for r in rows:
            upstream_id = r["upstream_id"]
            pct = r["cumulative_pct"]
            entity_totals[upstream_id] += pct
            path_nodes = r["path_str"].split("->")
            entity_paths[upstream_id].append([(nid, pct) for nid in path_nodes])
            
        results = []
        for upstream_id, total_pct in entity_totals.items():
            node = self.store.get_node(upstream_id)
            is_seed = node.is_seed if node else False
            seed_reason = node.attributes.get("reason") if node else None
            
            # Statutory threshold: 5% (0.05)
            meets_5pct = total_pct >= 0.05
            
            results.append(IndirectOwnershipResult(
                upstream_entity_id=upstream_id,
                downstream_provider_id=target_provider_id,
                effective_indirect_pct=round(total_pct, 4),
                meets_5pct_statutory_threshold=meets_5pct,
                is_upstream_excluded_seed=is_seed,
                seed_reason=seed_reason,
                ownership_paths=entity_paths[upstream_id]
            ))
            
        return sorted(results, key=lambda x: x.effective_indirect_pct, reverse=True)
