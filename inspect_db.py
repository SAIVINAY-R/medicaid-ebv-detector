#!/usr/bin/env python3
"""Interactive Database Inspector & Query Tool for Medicaid EBV Fraud Intelligence.

Usage:
    python3 inspect_db.py --stats
    python3 inspect_db.py --nodes [TYPE]
    python3 inspect_db.py --edges [TYPE]
    python3 inspect_db.py --sql "SELECT node_type, count(*) FROM graph_nodes GROUP BY node_type"
    python3 inspect_db.py --export ebv_graph.db
    python3 inspect_db.py --interactive
"""

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys

# Ensure src/ is on sys.path
src_dir = Path(__file__).resolve().parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from ebv_detector.config import SystemConfig
from ebv_detector.graph.store import GraphStore
from ebv_detector.pipeline.checkpoint1 import Checkpoint1Pipeline
from ebv_detector.synthetic.generator import SyntheticEBVGenerator


def build_live_database(db_path: str = ":memory:") -> GraphStore:
    """Instantiate and populate GraphStore with the full benchmark cohort."""
    generator = SyntheticEBVGenerator(seed=42)
    dataset = generator.generate_benchmark_suite()

    store = GraphStore(db_path=db_path)
    cp1 = Checkpoint1Pipeline(SystemConfig(), dataset.seed_records)
    # Point cp1 to our target store
    cp1.graph_store = store

    # Ingest seeds and ownership edges
    cp1.ingest_ownership_edges(dataset.ownership_edges)

    # Ingest applications
    for app in dataset.applications:
        cp1.evaluate_application(app)

    return store


def format_table(columns, rows):
    """Format rows as a clean ASCII table."""
    if not rows:
        return "No results found."

    # Compute column widths
    str_rows = [[str(item) if item is not None else "NULL" for item in row] for row in rows]
    widths = [len(col) for col in columns]
    for row in str_rows:
        for idx, val in enumerate(row):
            widths[idx] = max(widths[idx], len(val))

    # Format header
    sep = "+-" + "-+-".join("-" * w for w in widths) + "-+"
    header = "| " + " | ".join(col.ljust(widths[idx]) for idx, col in enumerate(columns)) + " |"

    lines = [sep, header, sep]
    for row in str_rows:
        line = "| " + " | ".join(val.ljust(widths[idx]) for idx, val in enumerate(row)) + " |"
        lines.append(line)
    lines.append(sep)
    return "\n".join(lines)


def run_sql(conn: sqlite3.Connection, query: str):
    """Execute SQL query and print formatted table."""
    try:
        cur = conn.cursor()
        cur.execute(query)
        if not cur.description:
            print("[*] Query executed successfully (no rows returned).")
            return
        columns = [d[0] for d in cur.description]
        rows = cur.fetchall()
        print(f"\nResults ({len(rows)} rows):")
        print(format_table(columns, rows))
    except Exception as e:
        print(f"[!] SQL Error: {e}", file=sys.stderr)


def show_stats(conn: sqlite3.Connection):
    """Print summary statistics for the database."""
    print("=" * 60)
    print("  Medicaid EBV Graph Database Statistics")
    print("=" * 60)

    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM graph_nodes")
    total_nodes = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM graph_edges")
    total_edges = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM graph_nodes WHERE is_seed = 1")
    total_seeds = cur.fetchone()[0]

    print(f"\n• Total Nodes: {total_nodes}")
    print(f"• Total Edges: {total_edges}")
    print(f"• Sanctioned Seed Nodes: {total_seeds}")

    print("\n--- Nodes by Type ---")
    cur.execute("SELECT node_type, COUNT(*) as count FROM graph_nodes GROUP BY node_type ORDER BY count DESC")
    print(format_table(["Node Type", "Count"], cur.fetchall()))

    print("\n--- Edges by Type ---")
    cur.execute("SELECT edge_type, COUNT(*) as count FROM graph_edges GROUP BY edge_type ORDER BY count DESC")
    print(format_table(["Edge Type", "Count"], cur.fetchall()))


def export_database(target_file: str):
    """Export the populated database to a persistent SQLite file."""
    p = Path(target_file)
    if p.exists():
        p.unlink()

    print(f"[*] Building and exporting database to '{target_file}'...")
    store = build_live_database(db_path=target_file)
    print(f"[✓] Successfully exported! File size: {p.stat().st_size:,} bytes")
    print(f"\nYou can now open this file directly using:")
    print(f"  • sqlite3 {target_file}")
    print(f"  • DB Browser for SQLite (https://sqlitebrowser.org/)")
    print(f"  • VS Code SQLite Extension / DBeaver / TablePlus\n")


def interactive_repl(conn: sqlite3.Connection):
    """Interactive SQL terminal REPL."""
    print("=" * 60)
    print("  Interactive EBV SQLite Shell")
    print("  Type SQL query (e.g. 'SELECT * FROM graph_nodes LIMIT 5;')")
    print("  Type 'tables', 'schema', or 'exit' / 'quit'.")
    print("=" * 60)

    while True:
        try:
            line = input("ebv-sql> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            break

        if not line:
            continue
        if line.lower() in ("exit", "quit", "q"):
            break
        if line.lower() == "tables":
            run_sql(conn, "SELECT name AS table_name FROM sqlite_master WHERE type='table';")
            continue
        if line.lower() == "schema":
            run_sql(conn, "SELECT sql FROM sqlite_master WHERE type IN ('table', 'index');")
            continue

        run_sql(conn, line)


def main():
    parser = argparse.ArgumentParser(description="Medicaid EBV SQLite Database Inspector")
    parser.add_argument("--db", default=":memory:", help="Path to SQLite database (default: :memory: populated from cohort)")
    parser.add_argument("--stats", action="store_true", help="Print table row counts and breakdown")
    parser.add_argument("--nodes", nargs="?", const="ALL", help="List nodes (optional: filter by node type, e.g. PROVIDER)")
    parser.add_argument("--edges", nargs="?", const="ALL", help="List edges (optional: filter by edge type, e.g. DIRECT_OWNERSHIP)")
    parser.add_argument("--sql", help="Run a custom SQL query")
    parser.add_argument("--export", metavar="FILE", help="Export live database to an SQLite file (e.g. ebv_graph.db)")
    parser.add_argument("--interactive", action="store_true", help="Start an interactive SQL prompt")
    parser.add_argument("--limit", type=int, default=25, help="Row limit for listings (default: 25)")
    args = parser.parse_args()

    if args.export:
        export_database(args.export)
        return

    # Check if user specified a file or wants live dataset
    if args.db != ":memory:" and Path(args.db).exists():
        conn = sqlite3.connect(args.db)
    else:
        print("[*] Initializing live relationship graph database...")
        store = build_live_database(":memory:")
        conn = store.conn

    if args.stats:
        show_stats(conn)
    elif args.nodes:
        if args.nodes == "ALL":
            run_sql(conn, f"SELECT node_id, node_type, label, is_seed FROM graph_nodes LIMIT {args.limit};")
        else:
            run_sql(conn, f"SELECT node_id, node_type, label, is_seed FROM graph_nodes WHERE node_type = '{args.nodes.upper()}' LIMIT {args.limit};")
    elif args.edges:
        if args.edges == "ALL":
            run_sql(conn, f"SELECT source_id, edge_type, target_id, ownership_percentage FROM graph_edges LIMIT {args.limit};")
        else:
            run_sql(conn, f"SELECT source_id, edge_type, target_id, ownership_percentage FROM graph_edges WHERE edge_type = '{args.edges.upper()}' LIMIT {args.limit};")
    elif args.sql:
        run_sql(conn, args.sql)
    elif args.interactive:
        interactive_repl(conn)
    else:
        show_stats(conn)
        print("\nTip: Run with --interactive for a live SQL shell, or --export ebv_graph.db to save as a file.")


if __name__ == "__main__":
    main()
