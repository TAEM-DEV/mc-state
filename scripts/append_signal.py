#!/usr/bin/env python3
"""
scripts/append_signal.py

Appends one controller signal to missions/{id}/signals.jsonl.
Uses retry-with-rebase for concurrent push safety per ADR-004 C-004-007.

Usage:
    python3 scripts/append_signal.py \
        --mission-id  MSN-abc123 \
        --mission-dir missions/MSN-abc123 \
        --controller  ARCH \
        --phase       2 \
        --signal      GO \
        --reason      "all ADR-001 constraints cleared" \
        [--constraint-ref ADR-001:C-001-001] \
        [--evidence '["file.go:44"]']
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone


def ulid_simple() -> str:
    """Generate a simple time-sortable ID without external deps."""
    ts = int(time.time() * 1000)
    import random
    rand = random.randint(0, 0xFFFFFFFFFF)
    return f"{ts:013X}{rand:010X}"


def git_run(*args, check=True, capture=False):
    """Run a git command in the current directory."""
    kwargs = {"check": check}
    if capture:
        kwargs["capture_output"] = True
        kwargs["text"] = True
    return subprocess.run(["git"] + list(args), **kwargs)


def append_with_retry(filepath: str, line: str, max_retries: int = 5) -> None:
    """Append a line to a file using git retry-with-rebase pattern."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    for attempt in range(max_retries):
        # Pull latest to reduce conflict probability
        if attempt > 0:
            git_run("pull", "--rebase", "origin", "main", check=False)
            time.sleep(0.5 * attempt)

        # Append to file
        with open(filepath, "a") as f:
            f.write(line + "\n")

        # Stage and commit
        git_run("add", filepath)

        # Check if there's anything to commit
        result = git_run(
            "diff", "--cached", "--quiet", check=False
        )
        if result.returncode == 0:
            # Nothing staged (duplicate write) — still success
            return

        git_run(
            "commit",
            "-m",
            f"taem: signal write {os.path.basename(filepath)}",
            env={
                **os.environ,
                "GIT_AUTHOR_NAME": "taem-flight[bot]",
                "GIT_AUTHOR_EMAIL": "flight@taem-dev.github.io",
                "GIT_COMMITTER_NAME": "taem-flight[bot]",
                "GIT_COMMITTER_EMAIL": "flight@taem-dev.github.io",
            },
        )

        # Try to push
        push_result = git_run("push", "origin", "main", check=False)
        if push_result.returncode == 0:
            return  # Success

        # Push failed (conflict) — pull rebase and retry
        git_run("reset", "HEAD~1", check=False)
        with open(filepath, "r") as f:
            lines = f.readlines()
        # Remove the appended line from the file before retry
        target_line = line + "\n"
        if lines and lines[-1] == target_line:
            with open(filepath, "w") as f:
                f.writelines(lines[:-1])

    raise RuntimeError(
        f"Failed to push signal after {max_retries} attempts"
    )


def main():
    parser = argparse.ArgumentParser(description="Append a TAEM signal to signals.jsonl")
    parser.add_argument("--mission-id",     required=True)
    parser.add_argument("--mission-dir",    required=True, help="Path to mission dir in mc-state checkout")
    parser.add_argument("--controller",     required=True)
    parser.add_argument("--phase",          required=True, type=int)
    parser.add_argument("--signal",         required=True,
                        choices=["GO", "NO-GO", "HOLD", "PASS", "WARN", "RELAY", "OVERRIDE"])
    parser.add_argument("--reason",         required=True)
    parser.add_argument("--constraint-ref", default=None)
    parser.add_argument("--evidence",       default="[]",
                        help="JSON array string of evidence items")
    parser.add_argument("--prb-votes",      default=None,
                        help="JSON object with skeptic/correctness/adr_audit votes")
    parser.add_argument("--override-by",    default=None)
    parser.add_argument("--override-reason",default=None)
    parser.add_argument("--dry-run",        action="store_true",
                        help="Print the signal JSON without writing")

    args = parser.parse_args()

    # Parse evidence
    try:
        evidence = json.loads(args.evidence)
    except json.JSONDecodeError:
        print(f"Warning: could not parse --evidence as JSON, using []", file=sys.stderr)
        evidence = []

    # Parse prb_votes
    prb_votes = None
    if args.prb_votes:
        try:
            prb_votes = json.loads(args.prb_votes)
        except json.JSONDecodeError:
            print(f"Warning: could not parse --prb-votes as JSON", file=sys.stderr)

    # Build signal entry
    entry = {
        "signal_id":       ulid_simple(),
        "mission_id":      args.mission_id,
        "phase":           args.phase,
        "controller":      args.controller,
        "signal":          args.signal,
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "reason":          args.reason,
        "evidence":        evidence,
        "constraint_ref":  args.constraint_ref,
        "prb_votes":       prb_votes,
        "override_by":     args.override_by,
        "override_reason": args.override_reason,
    }

    line = json.dumps(entry)

    if args.dry_run:
        print(line)
        return

    signals_path = os.path.join(args.mission_dir, "signals.jsonl")
    append_with_retry(signals_path, line)
    print(f"Signal written: {args.controller} {args.signal} for {args.mission_id}")


if __name__ == "__main__":
    main()
