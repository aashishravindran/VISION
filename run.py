#!/usr/bin/env python3
"""VISION CLI entry point.

Usage:
    python run.py "your question here"
    python run.py -v "your question"      # verbose: shows tool calls
"""
import argparse
import sys

from vision.agent import run


def main():
    parser = argparse.ArgumentParser(description="VISION — finance research agent")
    parser.add_argument("query", nargs="+", help="Your question for VISION")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Print tool calls as they happen"
    )
    args = parser.parse_args()

    query = " ".join(args.query)
    print(f"VISION ▸ {query}\n")
    try:
        answer = run(query, verbose=args.verbose)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(answer)


if __name__ == "__main__":
    main()
