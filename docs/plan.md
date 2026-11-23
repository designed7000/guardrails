# Guardrails implementation plan

> For agentic workers: use superpowers:executing-plans. Steps use `- [ ]`.

**Goal:** Shared CI checks, a ruleset script, and Claude rules for the repos of `designed7000`.

**Architecture:** One reusable workflow calls a tested Python script for the diff checks and runs Python tools inline. A Python script applies one ruleset through `gh api`.

**Tech stack:** GitHub Actions, Python 3.13 stdlib, pytest, uv, gitleaks, mypy, coverage, diff-cover, pip-audit, mutmut, CodeQL.

**Spec:** `docs/design.md`

## Global constraints

- Pin every action to a full commit SHA. Tool versions at least 7 days old.
- `permissions: contents: read` on each job (`codeql` adds `security-events: write`).
- PR gate jobs: `timeout-minutes: 10`.
- Required check name: `ci / gate`.
- Caller template: 15 lines or fewer.
- Scripts use the Python stdlib only.

## Review focus

1. Suppression pattern in a docs file: not flagged.
2. A deleted big file or a deleted `.env`: not flagged.
3. A renamed data file (`a.csv` to `a.parquet`): flagged.
4. A compound shell command (`git add . && git push -f`): hook blocks it.
5. Bare `git push` while on `main`: hook blocks it.

---

### Task 1: Diff checks script

**Files:** create `checks/diff_checks.py`, `tests/test_diff_checks.py`, `pyproject.toml`

- [ ] Write tests in a temp git repo: big file, data file, rename to data file, deleted file, suppression in code, suppression in `.md`, config ignore key, `.github/` change, labels, line count warning.
- [ ] Run tests: they fail.
- [ ] Write `main(argv) -> int` with `--base`, `--head`, `--labels`, `--self-repo`.
- [ ] Run tests: they pass. Commit.

### Task 2: Reusable workflow and caller

**Files:** create `.github/workflows/ci.yml`, `.github/workflows/self-test.yml`, `templates/caller.yml`

- [ ] Write the jobs as in the design. Lint with actionlint and zizmor.
- [ ] Push, run self-test on a PR, see `ci / gate` green. Tag `v1`.

### Task 3: Ruleset script

**Files:** create `scripts/apply_ruleset.py`, `ruleset/ruleset.json`, `tests/test_apply_ruleset.py`

- [ ] Tests: desired body has the 4 rules; dry run prints a diff and makes no write; private repo is skipped; `--check` overrides the required check.
- [ ] Implement. Tests pass. Commit.

### Task 4: Claude layer

**Files:** create `claude/CLAUDE-block.md`, `claude/hooks/block_risky_push.py`, `tests/test_block_risky_push.py`

- [ ] Tests for the review focus items 4 and 5, plus `--force-with-lease` on a feature branch is allowed.
- [ ] Implement. Tests pass. Commit.

### Task 5: README and pilot

- [ ] README table: check, risk, cost.
- [ ] PR in `wiki-pulse` with the caller. Green. Owner approves ruleset, apply.
