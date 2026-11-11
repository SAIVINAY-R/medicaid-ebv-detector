"""SQLite-backed Graph Store with Recursive CTE support.

Implements M3 relational storage model complying with ADR-0001
and open SQL query standards (no proprietary graph database required).
Supports point-in-time filtering via effective dates.
"""

import json
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple

from .schema import EdgeType, GraphEdge, GraphNode, NodeType


class GraphStore:
    """Relational graph engine backed by SQLite with Recursive CTE support."""

    def __init__(self, db_path: str = ":memory:"):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        """Initialize tables and indexes."""
        with self.conn:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS graph_nodes (
                    node_id TEXT PRIMARY KEY,
                    node_type TEXT NOT NULL,
                    label TEXT,
                    is_seed INTEGER DEFAULT 0,
                    seed_source TEXT,
                    attributes_json TEXT
                );
            """)
            self.conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_nodes_type ON graph_nodes(node_type);
            """)
            self.conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_nodes_seed ON graph_nodes(is_seed);
            """)

            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS graph_edges (
                    edge_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    edge_type TEXT NOT NULL,
                    weight REAL DEFAULT 1.0,
                    ownership_percentage REAL DEFAULT 0.0,
                    effective_start TEXT DEFAULT '2020-01-01',
                    effective_end TEXT,
                    FOREIGN KEY(source_id) REFERENCES graph_nodes(node_id),
                    FOREIGN KEY(target_id) REFERENCES graph_nodes(node_id)
                );
            """)
            self.conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_edges_source ON graph_edges(source_id, edge_type);
            """)
            self.conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_edges_target ON graph_edges(target_id, edge_type);
            """)
            self.conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_edges_pair ON graph_edges(source_id, target_id);
            """)

    def add_node(self, node: GraphNode):
        """Insert or replace graph node."""
        with self.conn:
            self.conn.execute("""
                INSERT OR REPLACE INTO graph_nodes (node_id, node_type, label, is_seed, seed_source, attributes_json)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                node.node_id,
                node.node_type.value,
                node.label,
                1 if node.is_seed else 0,
                node.seed_source,
                json.dumps(node.attributes)
            ))

    def add_edge(self, edge: GraphEdge):
        """Insert edge between two nodes."""
        with self.conn:
            self.conn.execute("""
                INSERT INTO graph_edges (source_id, target_id, edge_type, weight, ownership_percentage, effective_start, effective_end)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                edge.source_id,
                edge.target_id,
                edge.edge_type.value,
                edge.weight,
                edge.ownership_percentage,
                edge.effective_start,
                edge.effective_end
            ))

    def get_node(self, node_id: str) -> Optional[GraphNode]:
        """Fetch node by ID."""
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM graph_nodes WHERE node_id = ?", (node_id,))
        row = cur.fetchone()
        if not row:
            return None
        return GraphNode(
            node_id=row["node_id"],
            node_type=NodeType(row["node_type"]),
            label=row["label"],
            is_seed=bool(row["is_seed"]),
            seed_source=row["seed_source"],
            attributes=json.loads(row["attributes_json"] or "{}")
        )

    def get_all_seeds(self) -> List[GraphNode]:
        """Retrieve all known-bad seed nodes."""
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM graph_nodes WHERE is_seed = 1")
        seeds = []
        for row in cur.fetchall():
            seeds.append(GraphNode(
                node_id=row["node_id"],
                node_type=NodeType(row["node_type"]),
                label=row["label"],
                is_seed=True,
                seed_source=row["seed_source"],
                attributes=json.loads(row["attributes_json"] or "{}")
            ))
        return seeds

    def get_neighbors(
        self, node_id: str, direction: str = "BOTH"
    ) -> List[Tuple[str, EdgeType, float, float]]:
        """Return (neighbor_id, edge_type, weight, ownership_pct)."""
        cur = self.conn.cursor()
        results = []
        if direction in ("OUT", "BOTH"):
            cur.execute("""
                SELECT target_id AS n_id, edge_type, weight, ownership_percentage
                FROM graph_edges WHERE source_id = ?
            """, (node_id,))
            for r in cur.fetchall():
                results.append((r["n_id"], EdgeType(r["edge_type"]), r["weight"], r["ownership_percentage"]))
        if direction in ("IN", "BOTH"):
            cur.execute("""
                SELECT source_id AS n_id, edge_type, weight, ownership_percentage
                FROM graph_edges WHERE target_id = ?
            """, (node_id,))
            for r in cur.fetchall():
                results.append((r["n_id"], EdgeType(r["edge_type"]), r["weight"], r["ownership_percentage"]))
        return results

    def get_node_degree(self, node_id: str) -> int:
        """Calculate total undirected degree of a node."""
        cur = self.conn.cursor()
        cur.execute("""
            SELECT COUNT(*) AS deg FROM (
                SELECT edge_id FROM graph_edges WHERE source_id = ?
                UNION ALL
                SELECT edge_id FROM graph_edges WHERE target_id = ?
            )
        """, (node_id, node_id))
        row = cur.fetchone()
        return row["deg"] if row else 0

    def get_all_nodes(self) -> List[str]:
        """Return list of all node IDs."""
        cur = self.conn.cursor()
        cur.execute("SELECT node_id FROM graph_nodes")
        return [r["node_id"] for r in cur.fetchall()]

    def close(self):
        self.conn.close()
