#!/usr/bin/env python3
"""
scripts/build_integration_map.py

Core NAV logic. Reads target repos via GitHub API, extracts API surfaces,
detects patterns, and writes integration-map.json to the mission directory.

Per ADR-006 C-006-003: diffs against ecosystem repo_surfaces cache.
Per ADR-006 C-006-004: if ecosystem unreachable, logs warning and proceeds
  with cold read (NAV stub in the kernel already emitted HOLD if ecosystem
  was unreachable — if we reach here, ecosystem was reachable at stub time).

Usage:
    python3 scripts/build_integration_map.py \
        --mission-id  MSN-abc123 \
        --mission-dir missions/MSN-abc123 \
        --repos       ry-ops/git-steer,git-fabric/cve \
        --ecosystem-url http://ecosystem:8765 \
        --gh-token    ghp_...
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Optional

try:
    import requests
except ImportError:
    print("requests not installed: pip install requests", file=sys.stderr)
    sys.exit(1)


# ── GitHub API helpers ────────────────────────────────────────────────────────

def gh_get(url: str, token: str) -> Optional[dict]:
    """GET a GitHub API endpoint. Returns None on 404."""
    resp = requests.get(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }, timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def get_repo_head_sha(owner: str, repo: str, token: str) -> Optional[str]:
    """Get the HEAD SHA of the default branch."""
    data = gh_get(f"https://api.github.com/repos/{owner}/{repo}/commits/HEAD", token)
    return data["sha"] if data else None


def get_repo_tree(owner: str, repo: str, token: str) -> list[dict]:
    """Get the full recursive file tree of the default branch."""
    data = gh_get(
        f"https://api.github.com/repos/{owner}/{repo}/git/trees/HEAD?recursive=1",
        token
    )
    return data.get("tree", []) if data else []


def get_file_content(owner: str, repo: str, path: str, token: str) -> Optional[str]:
    """Get the decoded content of a file."""
    import base64
    data = gh_get(
        f"https://api.github.com/repos/{owner}/{repo}/contents/{path}",
        token
    )
    if not data or "content" not in data:
        return None
    return base64.b64decode(data["content"]).decode("utf-8", errors="replace")


# ── Surface extraction ────────────────────────────────────────────────────────

def detect_language(tree: list[dict]) -> str:
    """Infer primary language from file extensions."""
    ext_counts = {}
    for item in tree:
        if item["type"] != "blob":
            continue
        ext = os.path.splitext(item["path"])[1].lower()
        if ext:
            ext_counts[ext] = ext_counts.get(ext, 0) + 1
    lang_map = {
        ".go": "Go", ".ts": "TypeScript", ".js": "JavaScript",
        ".py": "Python", ".rs": "Rust", ".java": "Java",
    }
    if not ext_counts:
        return "unknown"
    top_ext = max(ext_counts, key=ext_counts.get)
    return lang_map.get(top_ext, "unknown")


def extract_mcp_tools(owner: str, repo: str, tree: list[dict], token: str) -> list[dict]:
    """Extract MCP tool definitions from TypeScript/Go/Python source."""
    tools = []
    tool_files = [
        f for f in tree
        if f["type"] == "blob" and any(
            f["path"].endswith(ext) for ext in [".ts", ".go", ".py"]
        ) and "test" not in f["path"].lower()
    ]

    # Quick heuristic: look for tool registration patterns
    tool_patterns = [
        ('typescript', ['server.tool(', 'this.server.tool(', 'tools.push(']),
        ('go',         ['tools.Tool{', 'ToolDefinition{', 'mcp.NewTool(']),
        ('python',     ['@tool', 'tool_name =', 'FastMCP']),
    ]

    for file_item in tool_files[:20]:  # cap to avoid API rate limits
        content = get_file_content(owner, repo, file_item["path"], token)
        if not content:
            continue

        for lang, patterns in tool_patterns:
            for pattern in patterns:
                if pattern in content:
                    # Extract tool names with simple regex-free parsing
                    for line in content.split('\n'):
                        line = line.strip()
                        if pattern in line and ('"' in line or "'" in line):
                            # Try to extract quoted name
                            for q in ['"', "'"]:
                                parts = line.split(q)
                                if len(parts) >= 3:
                                    candidate = parts[1]
                                    if candidate and len(candidate) < 60 and '_' in candidate or '-' in candidate:
                                        if candidate not in [t["name"] for t in tools]:
                                            tools.append({
                                                "type": "mcp_tool",
                                                "name": candidate,
                                                "file_path": file_item["path"],
                                            })
                                        break
    return tools


def extract_github_actions(tree: list[dict]) -> list[dict]:
    """Detect GitHub Actions workflows as exposed capabilities."""
    workflows = []
    for item in tree:
        if item["type"] == "blob" and ".github/workflows/" in item["path"]:
            name = os.path.basename(item["path"]).replace(".yml", "").replace(".yaml", "")
            workflows.append({
                "type": "github_workflow",
                "name": name,
                "file_path": item["path"],
            })
    return workflows


def extract_http_endpoints(tree: list[dict]) -> list[dict]:
    """Detect if repo exposes HTTP endpoints."""
    endpoints = []
    for item in tree:
        if item["type"] == "blob":
            name = item["path"].lower()
            if any(x in name for x in ["server.go", "server.ts", "app.ts", "main.go", "app.py"]):
                endpoints.append({
                    "type": "http_endpoint",
                    "name": "http_server",
                    "file_path": item["path"],
                })
                break
    return endpoints


def extract_consumed(tree: list[dict]) -> list[dict]:
    """Detect external dependencies consumed."""
    consumed = []
    for item in tree:
        path = item["path"]
        if path in ["package.json", "go.mod", "requirements.txt", "Cargo.toml", "pyproject.toml"]:
            consumed.append({
                "type": "dependency_manifest",
                "source": path,
                "event": None,
            })
    return consumed


def build_repo_surface(owner: str, repo: str, token: str) -> dict:
    """Build a repo surface dict from live GitHub data."""
    tree = get_repo_tree(owner, repo, token)
    head_sha = get_repo_head_sha(owner, repo, token)
    language = detect_language(tree)

    exposes = []
    exposes.extend(extract_mcp_tools(owner, repo, tree, token))
    exposes.extend(extract_github_actions(tree))
    exposes.extend(extract_http_endpoints(tree))

    consumes = extract_consumed(tree)

    return {
        "name":            repo,
        "org":             owner,
        "language":        language,
        "last_commit_sha": head_sha or "unknown",
        "last_updated":    datetime.now(timezone.utc).isoformat(),
        "status":          "fresh",
        "exposes":         exposes,
        "consumes":        consumes,
        "active_constraints": [],
        "known_patterns":  [],
    }


# ── Ecosystem cache ───────────────────────────────────────────────────────────

def get_cached_surface(ecosystem_url: str, owner: str, repo: str) -> Optional[dict]:
    """GET cached repo surface from ecosystem. Returns None if unavailable."""
    if not ecosystem_url:
        return None
    try:
        resp = requests.get(
            f"{ecosystem_url.rstrip('/')}/api/repo_surfaces/{owner}/{repo}",
            timeout=5
        )
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception:
        return None


def put_cached_surface(ecosystem_url: str, owner: str, repo: str, surface: dict) -> bool:
    """PUT repo surface to ecosystem cache. Returns True on success."""
    if not ecosystem_url:
        return False
    try:
        resp = requests.put(
            f"{ecosystem_url.rstrip('/')}/api/repo_surfaces/{owner}/{repo}",
            json=surface,
            headers={"X-TAEM-Writer": "NAV"},
            timeout=10
        )
        return resp.status_code in (200, 201, 204)
    except Exception:
        return False


# ── Integration map builder ───────────────────────────────────────────────────

def build_integration_map(
    mission_id: str,
    repos: list[str],
    token: str,
    ecosystem_url: Optional[str],
) -> dict:
    """Build the complete integration-map.json for a set of repos."""

    repo_surfaces = []

    for repo_full in repos:
        parts = repo_full.strip().split("/")
        if len(parts) != 2:
            print(f"Warning: skipping malformed repo '{repo_full}'", file=sys.stderr)
            continue
        owner, repo = parts

        # Check ecosystem cache first (ADR-006 C-006-003)
        cached = get_cached_surface(ecosystem_url, owner, repo)
        if cached and cached.get("status") == "fresh":
            print(f"NAV cache hit: {repo_full} (sha: {cached.get('last_commit_sha', '?')[:8]})")
            repo_surfaces.append(cached)
        else:
            # Cold read from GitHub
            print(f"NAV cold read: {repo_full}")
            try:
                surface = build_repo_surface(owner, repo, token)
                repo_surfaces.append(surface)
                # Update ecosystem cache
                ok = put_cached_surface(ecosystem_url, owner, repo, surface)
                if ok:
                    print(f"NAV ecosystem updated: {repo_full}")
                else:
                    print(f"NAV ecosystem update skipped (unavailable): {repo_full}")
            except Exception as e:
                print(f"Warning: could not read {repo_full}: {e}", file=sys.stderr)
                repo_surfaces.append({
                    "name": repo, "org": owner,
                    "language": "unknown", "last_commit_sha": "unknown",
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                    "status": "unreachable",
                    "exposes": [], "consumes": [],
                    "active_constraints": [], "known_patterns": [],
                })

    # Build proposed wiring (naive: cross-product of exposes/consumes)
    wiring = []
    conflicts = []
    tool_names_seen = {}

    for surface in repo_surfaces:
        for exposed in surface.get("exposes", []):
            name = exposed.get("name", "")
            repo_key = f"{surface['org']}/{surface['name']}"
            if name in tool_names_seen:
                conflicts.append({
                    "type": "naming_collision",
                    "description": f"Tool '{name}' exposed by both {tool_names_seen[name]} and {repo_key}",
                })
            else:
                tool_names_seen[name] = repo_key

    # Propose wiring between repos that consume what others expose
    if len(repo_surfaces) >= 2:
        for consumer in repo_surfaces:
            consumer_key = f"{consumer['org']}/{consumer['name']}"
            for provider in repo_surfaces:
                if consumer is provider:
                    continue
                provider_key = f"{provider['org']}/{provider['name']}"
                for exposed in provider.get("exposes", []):
                    if exposed["type"] == "mcp_tool":
                        wiring.append({
                            "from":        consumer_key,
                            "to":          provider_key,
                            "via":         f"mcp_tool:{exposed['name']}",
                            "transport":   "unicast",
                            "arch_status": "PENDING",
                            "constraint_checks": [],
                        })

    nav_signal = "GO" if all(
        s.get("status") != "unreachable" for s in repo_surfaces
    ) else "NO-GO"

    return {
        "mission_id":    mission_id,
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "generated_by":  "NAV",
        "nav_signal":    nav_signal,
        "repos":         repo_surfaces,
        "wiring":        wiring,
        "conflicts":     conflicts,
        "unresolved":    [],
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mission-id",    required=True)
    parser.add_argument("--mission-dir",   required=True)
    parser.add_argument("--repos",         required=True,
                        help="Comma-separated list of owner/repo")
    parser.add_argument("--ecosystem-url", default=None)
    parser.add_argument("--gh-token",      default=None,
                        help="GitHub token — falls back to GH_TOKEN env var")
    args = parser.parse_args()

    token = args.gh_token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("Error: GitHub token required (--gh-token or GH_TOKEN env var)", file=sys.stderr)
        sys.exit(1)

    repos = [r.strip() for r in args.repos.split(",") if r.strip()]
    if not repos:
        print("Error: no repos specified", file=sys.stderr)
        sys.exit(1)

    print(f"NAV: building integration map for {len(repos)} repo(s)")
    integration_map = build_integration_map(
        mission_id=args.mission_id,
        repos=repos,
        token=token,
        ecosystem_url=args.ecosystem_url,
    )

    # Write to mission dir
    os.makedirs(args.mission_dir, exist_ok=True)
    output_path = os.path.join(args.mission_dir, "integration-map.json")
    with open(output_path, "w") as f:
        json.dump(integration_map, f, indent=2)

    print(f"NAV: integration-map.json written to {output_path}")
    print(f"NAV: {len(integration_map['repos'])} repo(s), "
          f"{len(integration_map['wiring'])} wiring(s), "
          f"{len(integration_map['conflicts'])} conflict(s)")

    # Exit with error if nav_signal is NO-GO
    if integration_map["nav_signal"] == "NO-GO":
        print("NAV: one or more repos unreachable — signal will be NO-GO", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
