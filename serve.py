#!/usr/bin/env python3
"""Run the Medicaid Provider Fraud Intelligence API and Web Dashboard."""

import argparse
import os
import sys

from ebv_detector.api.server import run_server


def main():
    default_host = os.environ.get("HOST", "0.0.0.0")
    default_port = int(os.environ.get("PORT", "8080"))

    parser = argparse.ArgumentParser(description="Start Medicaid EBV Fraud Intelligence API & Dashboard")
    parser.add_argument("--host", default=default_host, help=f"Host address to bind (default: {default_host})")
    parser.add_argument("--port", type=int, default=default_port, help=f"Port to listen on (default: {default_port})")
    args = parser.parse_args()

    run_server(host=args.host, port=args.port)


if __name__ == "__main__":
    main()

