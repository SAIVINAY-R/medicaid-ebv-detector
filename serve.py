#!/usr/bin/env python3
"""Run the Medicaid Provider Fraud Intelligence API and Web Dashboard."""

import argparse
import sys

from ebv_detector.api.server import run_server


def main():
    parser = argparse.ArgumentParser(description="Start Medicaid EBV Fraud Intelligence API & Dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="Host address to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="Port to listen on (default: 8080)")
    args = parser.parse_args()

    run_server(host=args.host, port=args.port)


if __name__ == "__main__":
    main()

