#!/usr/bin/env python3
"""
scripts/append_manifest.py

Appends a manifest event to missions/{id}/manifest.jsonl.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone


def git_run(*args, check=True):
    import subprocess
    return subprocess.run(["git"] + list(args), check=check)


def append_with_retry(filepath: str, line: str, max_retries: int = 5) -> None:
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    for attempt in range(max_retries):
        if attempt > 0:
            git_run("pull", "--rebase", "origin", "main", check=False)
            time.sleep(0.5 * attempt)
        with open(filepath, "a") as f:
            f.write(line + "\n")
        git_run("add", filepath)
        result = git_run("diff", "--cached", "--quiet", check=False)
        if result.returncode == 0:
            return
        git_run("commit", "-m", f"taem: manifest event {os.path.basename(filepath)}",
                env={**os.environ,
                     "GIT_AUTHOR_NAME": "taem-flight[bot]",
                     "GIT_AUTHOR_EMAIL": "flight@taem-dev.github.io",
                     "GIT_COMMITTER_NAME": "taem-flight[bot]",
                     "GIT_COMMITTER_EMAIL": "flight@taem-dev.github.io"})
        push = git_run("push", "origin", "main", check=False)
        if push.returncode == 0:
            return
        git_run("reset", "HEAD~1", check=False)
    raise RuntimeError(f"Failed to push manifest after {max_retries} attempts")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mission-id",  required=True)
    parser.add_argument("--mission-dir", required=True)
    parser.add_argument("--event",       required=True,
                        choices=["MISSION_START", "PHASE_START", "PHASE_COMPLETE",
                                 "PHASE_HOLD", "MISSION_LANDED", "MISSION_ESCALATED"])
    parser.add_argument("--phase",       type=int, default=None)
    parser.add_argument("--task",        default=None)
    parser.add_argument("--repos",       default=None, help="Comma-separated repos")
    parser.add_argument("--adrs",        default=None, help="Comma-separated ADR IDs")
    parser.add_argument("--dry-run",     action="store_true")
    args = parser.parse_args()

    entry = {
        "event_id":   f"{args.mission_id}-{args.event.lower().replace('_', '-')}-{int(time.time())}",
        "mission_id": args.mission_id,
        "event":      args.event,
        "timestamp":  datetime.now(timezone.utc).isoformat(),
    }
    if args.phase is not None:
        entry["phase"] = args.phase
    if args.task:
        entry["task"] = args.task
    if args.repos:
        entry["repos"] = [r.strip() for r in args.repos.split(",")]
    if args.adrs:
        entry["adrs"] = [a.strip() for a in args.adrs.split(",")]

    line = json.dumps(entry)
    if args.dry_run:
        print(line)
        return

    manifest_path = os.path.join(args.mission_dir, "manifest.jsonl")
    append_with_retry(manifest_path, line)
    print(f"Manifest event written: {args.event} for {args.mission_id}")


if __name__ == "__main__":
    main()
