# TAEM — mc-state
## taem-dev/mc-state Repository Brief for Claude Code

Read this completely before writing any code.

---

## What This Repo Is

`taem-dev/mc-state` is the git-first state store and GitHub Actions
execution backend for TAEM missions. It serves three roles:

1. **State store** — append-only JSONL mission records in `missions/`
2. **Actions backend** — NAV and PAO workflows the kernel dispatches to
3. **Audit log** — `git log` is the complete mission history

**Critical architecture point (ADR-007):**
The kernel (`taem-dev/taem`) is the orchestrator. GitHub Actions is a
backend. The kernel dispatches to workflows here for NAV (repo access
via App token) and PAO (repository_dispatch to FABRIC/SOCIAL). The
kernel runs all other controllers locally. Do not add orchestration
logic to workflow files.

---

## What the Kernel Writes Here

The kernel writes mission state directly to a local clone of this repo.
It does NOT use GitHub Actions for state writes — it uses git directly.

Files the kernel creates/appends:
```
missions/{mission-id}/
  manifest.jsonl    # MISSION_START, PHASE_START, PHASE_COMPLETE, MISSION_LANDED
  signals.jsonl     # one line per controller signal
  integration-map.json  # written by NAV workflow
  step-plan.json        # written by INCO controller
  LANDED.md             # written by CAPCOM controller
```

The schemas in `schemas/` define the exact shape of every JSONL entry.
DPS validates all JSONL against these schemas.

---

## What Gets Built Here

Two GitHub Actions workflows that the kernel dispatches to:

### 1. `.github/workflows/controllers/nav.yml`
NAV — reads target repos via GitHub App token, builds integration-map.json,
writes to ecosystem, commits map to missions/{id}/integration-map.json.

**Inputs the kernel sends:**
```yaml
mission_id:   string   # e.g. MSN-a1b2c3d4e5f67890
mission_dir:  string   # local path in kernel — NOT used by workflow
phase:        string   # "1"
adrs_path:    string   # path to adrs checkout
```

**What NAV workflow does:**
1. Checkout mc-state (to write integration-map.json)
2. Read repos from mission manifest (missions/{mission_id}/manifest.jsonl)
3. For each repo: fetch file tree, extract API surfaces, detect patterns
4. Diff against ecosystem repo_surfaces (GET /api/repo_surfaces/{org}/{repo})
5. Cache hit → use cached surface. Cache miss → read live repo
6. Write integration-map.json to missions/{mission_id}/integration-map.json
7. Commit and push to mc-state main
8. Update ecosystem repo_surfaces (PUT /api/repo_surfaces/{org}/{repo})
9. Append GO signal to missions/{mission_id}/signals.jsonl
10. Commit and push

### 2. `.github/workflows/controllers/pao.yml`
PAO — fires repository_dispatch to ry-ops/fabric-social on LANDED missions.

**Inputs the kernel sends:**
```yaml
mission_id:   string
mission_dir:  string
phase:        string   # "6"
adrs_path:    string
```

**What PAO workflow does:**
1. Checkout mc-state
2. Read LANDED.md from missions/{mission_id}/LANDED.md
3. Fire repository_dispatch to ry-ops/fabric-social (event: mission-landed)
4. Append RELAY signal to missions/{mission_id}/signals.jsonl
5. Commit and push

---

## Python Scripts

The Python scripts in `scripts/` are utilities used by the workflows.
They handle git-safe JSONL append (retry-with-rebase) and signal writing.

### `scripts/append_signal.py`
Appends one signal JSONL line. Uses retry-with-rebase for concurrent
push safety (ADR-004 C-004-007).

```
args: --mission-id, --controller, --phase, --signal,
      --reason, --mission-dir (path in mc-state checkout)
      --constraint-ref (optional)
      --evidence (optional, JSON array string)
```

### `scripts/append_manifest.py`
Appends a manifest event JSONL line.

```
args: --mission-id, --event, --phase, --mission-dir
      --task (optional), --repos (optional, comma-separated)
```

### `scripts/build_integration_map.py`
Core NAV logic. Reads repos, extracts surfaces, builds integration-map.json.

```
args: --mission-id, --mission-dir, --repos (comma-separated)
      --ecosystem-url (optional)
      --adrs-path (optional)
output: writes missions/{id}/integration-map.json
```

### `scripts/validate_schemas.py`
DPS helper. Validates all JSONL files in a mission dir against schemas/.

```
args: --mission-dir
output: exits 0 on clean, exits 1 with errors printed to stderr
```

---

## Git Discipline (ADR-004 C-004-007)

All scripts that commit must use this exact pattern:

```bash
git config user.name  "taem-flight[bot]"
git config user.email "flight@taem-dev.github.io"
git add missions/${MISSION_ID}/
git pull --rebase origin main
git commit -m "taem: ${CONTROLLER} ${SIGNAL} for ${MISSION_ID}"
git push origin main
```

**Never** use `git push --force`. Append only to JSONL files.

---

## Secrets Required

Set in mc-state repo → Settings → Secrets → Actions:

| Secret | Description |
|---|---|
| `TAEM_APP_ID` | GitHub App ID for taem-flight[bot] |
| `TAEM_APP_PRIVATE_KEY` | GitHub App private key (full PEM) |
| `ECOSYSTEM_URL` | taem-dev/ecosystem service URL |
| `ANTHROPIC_API_KEY` | For future reference — not used by these workflows |

---

## Build Order

1. `scripts/append_signal.py` — most critical, called by all workflows
2. `scripts/append_manifest.py`
3. `scripts/validate_schemas.py`
4. `scripts/build_integration_map.py` — the real NAV logic
5. `.github/workflows/controllers/nav.yml`
6. `.github/workflows/controllers/pao.yml`
7. `index.jsonl` — empty seed file

---

## Hard Rules

- No gate logic in workflow files — the kernel owns gates (ADR-007 C-007-001)
- No controller writes to collections they don't own (ADR-006 C-006-002)
- append_signal.py must use retry-with-rebase — never force push
- JSONL files are append-only — never overwrite a line
- Workflow inputs are trusted (from kernel) — no validation needed
- If ECOSYSTEM_URL is not set, NAV writes integration-map.json without
  ecosystem caching (cold read) and logs a warning — does not fail
