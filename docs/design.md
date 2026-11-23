# Guardrails design

Approved by the owner on 2026-11-23.

## Intent

The owner ships a lot of code through AI agents and can miss mistakes in
large changes. Guardrails catches those mistakes before a merge.

- `CLAUDE.md` is advice.
- CI is the check.
- The ruleset is the lock.

## Owner decisions

| Question | Decision |
|---|---|
| Private repos | Advice only. CI runs there, but no ruleset (rulesets on private repos need GitHub Pro). |
| Pilot repo | `wiki-pulse` |
| Central repo | `designed7000/guardrails`, public. A public repo cannot call a workflow in a private repo. |

## Goals and non-goals

| Goals | Non-goals |
|---|---|
| One shared set of checks for all 16 repos | Defense against a hostile contributor (the owner works alone) |
| A merge lock on public repos | A lock on private repos (owner chose advice only) |
| PR gate in less than 10 minutes | Code review by a person (0 approvals) |
| Changes to the checks are loud, never quiet | |

Anti-goal: a gate so slow or noisy that the owner turns it off.

## Approaches considered

| Approach | Trade-off | Picked |
|---|---|---|
| One reusable workflow + one aggregate `gate` job as the only required check | The same check name for each language. New checks need no ruleset change. | Yes |
| Each job is a required check | Clear in the ruleset, but each language needs its own list, and a new check needs a ruleset change on 16 repos. | No |
| Org-level "required workflows" rule | It would stop a PR that edits its own caller. Only for organizations, not personal accounts. | Not available |

## Design

- `.github/workflows/ci.yml` is the only reusable workflow (`on: workflow_call`).
  It routes by event: `pull_request` runs `common`, `python`, `gate`;
  `schedule` or `workflow_dispatch` runs `codeql` and `mutation`.
- Required check: `ci / gate` (caller job id `ci`, central job `gate`).
- Callers use `@v1`, a tag that the owner moves after a test on the pilot.
- `common` checks out the central repo at `v1` into `.guardrails/` to run
  `checks/diff_checks.py`. In the central repo itself it uses its own commit.
- Every third-party action is pinned to a commit SHA. Tool versions are
  pinned, at least 7 days old when picked.
- Each job sets `permissions: contents: read`. Only `codeql` adds
  `security-events: write`, so the caller must grant it.
- Each PR job has `timeout-minutes: 10`.
- The diff coverage threshold (80%) is fixed in central code. No input can lower it.

### `common` job (all languages)

| Check | Result |
|---|---|
| gitleaks on the PR commits | fail |
| A file > 5 MB added or changed | fail |
| Data file: `*.parquet`, `*.duckdb`, `*.db`, `*.sqlite`, `*.sqlite3`, `.env`, `.env.*` (not `.env.example`, `.env.sample`, `.env.template`) | fail |
| New suppression in an added line: `noqa`, `type: ignore`, `pyright: ignore`, `mypy: ignore-errors`, `pytest.skip`, `pytest.mark.skip`, `skipif`, `xfail`, `unittest.skip`, `eslint-disable`, `@ts-ignore`, `@ts-expect-error`, `@ts-nocheck`, `pragma: no cover` | fail, unless label `allow-suppression` |
| New ignore key in a lint/type/test/coverage config file (`ignore`, `per-file-ignores`, `ignore_errors`, `ignore_missing_imports`, `disable_error_code`, `omit`, `fail_under`) | fail, unless label `allow-suppression` |
| Change in `.github/`, `.gitleaks.toml`, `.gitleaksignore` | fail, unless label `allow-ci-change` |
| More than 800 changed lines (lock files not counted) | warning |

Docs files (`*.md`, `*.rst`, `*.txt`) are not scanned for suppressions.

### `python` job

All steps run even when one fails, so the owner sees every failure at once.
The project's own tool version is used when the project has it. Otherwise
the central pinned version is used.

`uv lock --check`, `uv sync --locked --all-extras`, `ruff check .`,
`ruff format --check .`, `mypy` (targets from `[tool.mypy].files`, else `.`),
`coverage run -m pytest`, `diff-cover --fail-under=80` against the PR base,
`pip-audit` on the exported lock file.

### Weekly jobs

| Job | Behavior |
|---|---|
| `codeql` | Public repos only. Languages from the repo language list, plus `actions`. |
| `mutation` | Python repos only. mutmut. Reports survived mutants in the job summary. Does not fail on survivors. |

### Ruleset

Target: default branch. Rules: PR required with 0 approvals, required check
`ci / gate` from GitHub Actions, no force push, no deletion. No bypass.
"Branch must be up to date" is off. The script `scripts/apply_ruleset.py`
does a dry run by default and skips private repos.

`Euro_stepper_analyst` gets the ruleset with its current check `test` until
its caller workflow is merged.

### Claude layer

- A rules block for `~/.claude/CLAUDE.md` (in `claude/CLAUDE-block.md`).
- A `PreToolUse` hook (`claude/hooks/block_risky_push.py`) that blocks
  `git push --force`, `-f`, `+refspec`, and a push to `main` or `master`.
  `--force-with-lease` stays allowed on other branches.

## Known gaps

- A PR can edit its own caller file. The `allow-ci-change` check makes this
  loud, but a PR that removes the call to `common` is caught only because
  `ci / gate` then never reports.
- Private repos have no lock.
