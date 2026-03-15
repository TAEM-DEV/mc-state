#!/usr/bin/env python3
"""
scripts/validate_schemas.py

Validates mission JSONL files against schemas/ using jsonschema.
DPS calls this. Exits 0 on clean, exits 1 with errors to stderr.

Usage:
    python3 scripts/validate_schemas.py --mission-dir missions/MSN-abc123
    python3 scripts/validate_schemas.py --mission-dir missions/MSN-abc123 --file integration-map.json
"""

import argparse
import json
import os
import sys

try:
    import jsonschema
except ImportError:
    print("jsonschema not installed: pip install jsonschema", file=sys.stderr)
    sys.exit(1)


# Map from filename to schema file
SCHEMA_MAP = {
    "signals.jsonl":           "schemas/signal.schema.json",
    "manifest.jsonl":          "schemas/manifest.schema.json",
    "integration-map.json":    "schemas/integration-map.schema.json",
    "step-plan.json":          "schemas/step-plan.schema.json",
    "remediation.jsonl":       "schemas/remediation.schema.json",
}


def load_schema(schema_path: str) -> dict:
    with open(schema_path) as f:
        return json.load(f)


def validate_jsonl(filepath: str, schema: dict) -> list[str]:
    """Validate each line of a JSONL file. Returns list of error strings."""
    errors = []
    with open(filepath) as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                errors.append(f"Line {i}: invalid JSON: {e}")
                continue
            try:
                jsonschema.validate(obj, schema)
            except jsonschema.ValidationError as e:
                errors.append(f"Line {i}: {e.message} (path: {list(e.path)})")
    return errors


def validate_json(filepath: str, schema: dict) -> list[str]:
    """Validate a single JSON file. Returns list of error strings."""
    with open(filepath) as f:
        obj = json.load(f)
    errors = []
    try:
        jsonschema.validate(obj, schema)
    except jsonschema.ValidationError as e:
        errors.append(f"{e.message} (path: {list(e.path)})")
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mission-dir", required=True)
    parser.add_argument("--file", default=None,
                        help="Validate only this specific file (default: validate all)")
    args = parser.parse_args()

    all_errors = {}

    files_to_check = {}
    if args.file:
        if args.file in SCHEMA_MAP:
            files_to_check[args.file] = SCHEMA_MAP[args.file]
        else:
            print(f"No schema defined for {args.file}", file=sys.stderr)
            sys.exit(1)
    else:
        files_to_check = SCHEMA_MAP

    for filename, schema_path in files_to_check.items():
        filepath = os.path.join(args.mission_dir, filename)
        if not os.path.exists(filepath):
            continue  # File not yet written — not an error

        if not os.path.exists(schema_path):
            print(f"Warning: schema not found: {schema_path}", file=sys.stderr)
            continue

        schema = load_schema(schema_path)

        if filename.endswith(".jsonl"):
            errors = validate_jsonl(filepath, schema)
        else:
            errors = validate_json(filepath, schema)

        if errors:
            all_errors[filename] = errors

    if all_errors:
        for filename, errors in all_errors.items():
            for err in errors:
                print(f"SCHEMA ERROR [{filename}]: {err}", file=sys.stderr)
        sys.exit(1)

    print(f"Schema validation clean: {args.mission_dir}")
    sys.exit(0)


if __name__ == "__main__":
    main()
