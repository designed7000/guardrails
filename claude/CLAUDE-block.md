## Guardrails (all repos)

CI is the check. The ruleset is the lock. These rules are the advice.

1. **Run the checks before each commit.** Use the check command in the repo's `CLAUDE.md`,
   README, or CI file. For a Python repo with uv, run:
   `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`.
   Do not commit when a check fails.
2. **Never weaken a check without telling the owner first.** This includes `noqa`,
   `type: ignore`, `pytest.skip`, `xfail`, `eslint-disable`, a lower threshold, a new ignore
   rule in a config file, and any change to `.github/` or other CI files. Say what you
   changed and why before the commit.
3. **Never add the labels `allow-suppression` or `allow-ci-change`.** Only the owner adds them.
4. **Write the failing test first.** Run it and see it fail. Then write the code.
5. **For a new dependency, say why it is necessary.** Before you add it, check on the registry
   (PyPI, npm) that the exact name exists, that it had a release in the last 12 months, and
   that it is not a near-copy of a popular name. Tell the owner the result.
6. **Never put secrets or data in Git.** No `.env` files, no `*.parquet`, `*.duckdb`, `*.db`,
   `*.sqlite`, and no file larger than 5 MB. Add the pattern to `.gitignore`.
7. **Keep PRs small.** One change per PR, about 800 changed lines or fewer. Split larger work.
8. **Never push to `main` and never force push.** Push a branch and open a PR.
9. **Report failures with the output.** Give the command and the lines that show the failure.
   Do not say "done" when a check failed or did not run.
