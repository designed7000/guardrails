# guardrails

Shared checks that catch mistakes in AI-written code before a merge.

- `CLAUDE.md` is advice (`claude/CLAUDE-block.md`).
- CI is the check (`.github/workflows/ci.yml`).
- The ruleset is the lock (`scripts/apply_ruleset.py`). Public repos only.

## Checks

| Check | When | Risk it guards against | Cost |
|---|---|---|---|
| gitleaks | PR | A key or token is committed | ~5 s |
| File > 5 MB | PR | Large binary or data dump in Git history | ~1 s |
| Data file (`*.parquet`, `*.duckdb`, `*.db`, `*.sqlite`, `.env`) | PR | Private data or secrets in Git | ~1 s |
| New suppression (`noqa`, `type: ignore`, skip, `xfail`, `eslint-disable`, `@ts-ignore`, `pragma: no cover`, ignore keys in config) | PR, fails without label `allow-suppression` | The agent hides a failure instead of fixing it | ~1 s. A label click when the suppression is correct. |
| Change in `.github/` or gitleaks config | PR, fails without label `allow-ci-change` | The agent weakens CI quietly | A label click on CI PRs |
| PR > 800 changed lines | PR, warning only | A change too large to review | none |
| `uv lock --check`, `uv sync --locked` | PR, Python | Lock file does not match `pyproject.toml` | ~10 s with cache |
| `ruff check`, `ruff format --check` | PR, Python | Bugs found by lint, noisy diffs | ~2 s |
| mypy | PR, Python | Type errors, wrong calls, `None` bugs | ~10–30 s |
| pytest | PR, Python | Broken behavior | the repo's test time |
| Diff coverage ≥ 80% (diff-cover) | PR, Python | New code without tests, also new files that no test imports | ~5 s |
| pip-audit | PR, Python | A dependency with a known vulnerability | ~10 s. A failure can come from a new advisory, not from your change. |
| `gate` | PR | One required check for the ruleset | ~5 s |
| CodeQL | Weekly + manual, public repos | Security bugs (injection, unsafe workflows) | ~5 min a week. No PR time. |
| Mutation tests (mutmut) | Weekly + manual, Python | Tests that run code but do not check results | ~1–10 min a week. Report only. |

PR jobs run in parallel and stop after 10 minutes. Measured on the `wiki-pulse` pilot
(83 tests): `common` 9 s, `python` 24 s, `gate` 4 s. About 30 s for the whole PR gate.

## Use it in a repo

1. Copy `templates/caller.yml` to `.github/workflows/guardrails.yml`.
   For a repo without Python, set `python: false`.
2. Python repos need `pyproject.toml` and `uv.lock`. The checks use the repo's own ruff
   and mypy when the repo has them, else pinned versions. mypy checks `[tool.mypy].files`
   when set, else the whole repo.
3. Open the PR with the label `allow-ci-change`. See it green.
4. Apply the ruleset (public repos):

   ```bash
   python3 scripts/apply_ruleset.py wiki-pulse            # dry run: shows the change
   python3 scripts/apply_ruleset.py --apply wiki-pulse    # writes it
   ```

The ruleset on the default branch: PR required, 0 approvals, required check `ci / gate`,
no force push, no deletion, no bypass. Turn it off in Settings → Rules in an emergency.

## Labels

| Label | Effect | Who adds it |
|---|---|---|
| `allow-suppression` | A new suppression becomes a warning | The owner only |
| `allow-ci-change` | A CI change becomes a warning | The owner only |

## Claude layer

- `claude/CLAUDE-block.md`: rules for `~/.claude/CLAUDE.md`.
- `claude/hooks/block_risky_push.py`: a `PreToolUse` hook. It blocks `git push --force`,
  `-f`, `+refspec`, `--mirror`, `--all`, a push to `main` or `master`, and `gh` commands that
  add the owner-only labels. `--force-with-lease` stays allowed on other branches.

Install the hook:

```bash
mkdir -p ~/.claude/hooks && cp claude/hooks/block_risky_push.py ~/.claude/hooks/
```

Then merge `claude/settings-hooks.json` into `~/.claude/settings.json`.
If the hook file is missing, the command exits with code 1: Claude Code shows a hook error and
does not block. Exit code 2 (a blocked push) is the only code that blocks.

## Change the checks

1. Open a PR in this repo. The `self-test` workflow runs the same checks on this repo, from
   the PR commit, plus actionlint and zizmor.
2. After the merge, test on the pilot (`wiki-pulse`) with a ref to the new commit.
3. Move the tag: `git tag -f v1 && git push -f origin refs/tags/v1`. All callers then use
   the new checks. (The hook allows a force push only when the target is written as `refs/tags/...`.)

Tool versions are pinned in the `env:` block of `ci.yml`. Pick versions at least 7 days old.

## Known gaps

- A PR can edit its own caller file. The `allow-ci-change` check makes this visible, and
  `ci / gate` must still report.
- Private repos have no lock (rulesets on private repos need GitHub Pro). Checks there are
  advice only.
