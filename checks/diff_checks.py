"""Checks on the diff of a pull request. Stdlib only.

Usage: diff_checks.py --base REV --head REV [--labels a,b] [--self-repo]

Prints GitHub annotations. Writes a table to $GITHUB_STEP_SUMMARY when set.
Exit code 1 when a check fails.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import PurePosixPath

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_CHANGED_LINES = 800

DATA_FILE_GLOBS = ("*.parquet", "*.duckdb", "*.db", "*.sqlite", "*.sqlite3", ".env", ".env.*")
ENV_TEMPLATES = {".env.example", ".env.sample", ".env.template"}
DOC_SUFFIXES = {".md", ".markdown", ".rst", ".txt"}
LOCK_FILES = {
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "go.sum",
    "Pipfile.lock",
    "composer.lock",
    "Gemfile.lock",
    "bun.lockb",
}
CI_ROOT_FILES = {".gitleaks.toml", ".gitleaksignore"}
CONFIG_FILES = {
    "pyproject.toml",
    "setup.cfg",
    "tox.ini",
    "pytest.ini",
    "mypy.ini",
    ".mypy.ini",
    ".coveragerc",
    "ruff.toml",
    ".ruff.toml",
    ".flake8",
    ".pylintrc",
    "pyrightconfig.json",
    "tsconfig.json",
    ".eslintrc",
    ".eslintrc.json",
    ".eslintrc.js",
    ".eslintrc.cjs",
    ".eslintrc.yml",
    "eslint.config.js",
    "eslint.config.mjs",
    "eslint.config.cjs",
}

# The character classes ([q], [e], ...) keep this file from matching itself.
SUPPRESSION_RES = [
    re.compile(rx, re.IGNORECASE)
    for rx in (
        r"\bno[q]a\b",
        r"\btype:\s*ignor[e]\b",
        r"\bpyright:\s*ignor[e]\b",
        r"\bmypy:\s*ignore-error[s]\b",
        r"\bpytest\.(?:mark\.)?(?:skip|skipif|xfail)\b",
        r"\bunittest\.(?:skip|skipIf|skipUnless|expectedFailure)\b",
        r"\bpragma:\s*no\s*cove[r]\b",
        r"\beslint-disabl[e]",
        r"@ts-ignor[e]\b",
        r"@ts-expect-erro[r]\b",
        r"@ts-nochec[k]\b",
    )
]
CONFIG_IGNORE_RE = re.compile(
    r"""^\s*["']?(ignore|extend-ignore|per-file-ignores|extend-per-file-ignores|ignore_errors|"""
    r"""ignore_missing_imports|disable_error_code|omit|fail_under|fail-under)["']?\s*[=:]"""
)
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")

LABEL_SUPPRESSION = "allow-suppression"
LABEL_CI = "allow-ci-change"


@dataclass(frozen=True)
class Change:
    status: str  # A, M, D, R, C, T
    path: str
    old_path: str | None = None


@dataclass(frozen=True)
class Finding:
    check: str
    message: str
    path: str | None = None
    line: int | None = None
    level: str = "error"  # error or warning


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def changed_files(base: str, head: str) -> list[Change]:
    fields = git("diff", "--name-status", "-z", "-M", base, head).split("\0")
    changes: list[Change] = []
    i = 0
    while i < len(fields) and fields[i]:
        status = fields[i][0]
        if status in "RC":
            changes.append(Change(status, fields[i + 2], fields[i + 1]))
            i += 3
        else:
            changes.append(Change(status, fields[i + 1]))
            i += 2
    return changes


def blob_sizes(head: str, paths: list[str]) -> dict[str, int]:
    if not paths:
        return {}
    out = subprocess.run(
        ["git", "cat-file", "--batch-check=%(objectsize)"],
        input="".join(f"{head}:{p}\n" for p in paths),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    return {p: int(size) for p, size in zip(paths, out, strict=True) if size.isdigit()}


def added_lines(base: str, head: str) -> dict[str, list[tuple[int, str]]]:
    diff = git(
        "diff",
        "-U0",
        "-M",
        "--no-color",
        "--no-ext-diff",
        "--src-prefix=a/",
        "--dst-prefix=b/",
        base,
        head,
    )
    result: dict[str, list[tuple[int, str]]] = {}
    path: str | None = None
    line_no = 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            target = raw[4:]
            path = target[2:] if target.startswith("b/") else None
        elif raw.startswith("@@"):
            match = HUNK_RE.match(raw)
            line_no = int(match.group(1)) if match else 0
        elif raw.startswith("+") and path is not None:
            result.setdefault(path, []).append((line_no, raw[1:]))
            line_no += 1
    return result


def changed_line_count(base: str, head: str) -> int:
    fields = git("diff", "--numstat", "-z", "-M", base, head).split("\0")
    total = 0
    i = 0
    while i < len(fields) and fields[i]:
        added, deleted, path = fields[i].split("\t", 2)
        if path == "":  # rename: the next two fields are old and new paths
            path = fields[i + 2]
            i += 3
        else:
            i += 1
        if added == "-" or is_lock_file(path):
            continue
        total += int(added) + int(deleted)
    return total


def is_lock_file(path: str) -> bool:
    name = PurePosixPath(path).name
    return name in LOCK_FILES or name.endswith(".lock")


def is_data_file(path: str) -> bool:
    name = PurePosixPath(path).name
    if name in ENV_TEMPLATES:
        return False
    return any(fnmatch.fnmatchcase(name, glob) for glob in DATA_FILE_GLOBS)


def is_ci_path(path: str) -> bool:
    return path.startswith(".github/") or path in CI_ROOT_FILES


def check_files(changes: list[Change], sizes: dict[str, int]) -> list[Finding]:
    findings: list[Finding] = []
    for change in changes:
        if change.status == "D":
            continue
        size = sizes.get(change.path, 0)
        if size > MAX_FILE_BYTES:
            findings.append(
                Finding(
                    "Large file",
                    f"File is {size / 1024 / 1024:.1f} MiB. The limit is 5 MB. "
                    "Keep large files out of Git.",
                    change.path,
                )
            )
        if is_data_file(change.path):
            findings.append(
                Finding(
                    "Data file",
                    "Data and .env files must not be in Git. Add the pattern to .gitignore.",
                    change.path,
                )
            )
    return findings


def check_suppressions(added: dict[str, list[tuple[int, str]]], allowed: bool) -> list[Finding]:
    level = "warning" if allowed else "error"
    hint = "" if allowed else f" Add the PR label `{LABEL_SUPPRESSION}` if it is necessary."
    findings: list[Finding] = []
    for path, lines in added.items():
        suffix = PurePosixPath(path).suffix.lower()
        if suffix in DOC_SUFFIXES:
            continue
        is_config = PurePosixPath(path).name in CONFIG_FILES
        for line_no, text in lines:
            for regex in SUPPRESSION_RES:
                if found := regex.search(text):
                    findings.append(
                        Finding(
                            "Suppression",
                            f"New suppression `{found.group(0)}`.{hint}",
                            path,
                            line_no,
                            level,
                        )
                    )
                    break
            else:
                match = CONFIG_IGNORE_RE.match(text) if is_config else None
                if match:
                    findings.append(
                        Finding(
                            "Suppression",
                            f"New config key `{match.group(1)}` can hide failures.{hint}",
                            path,
                            line_no,
                            level,
                        )
                    )
    return findings


def check_ci_changes(changes: list[Change], allowed: bool) -> list[Finding]:
    level = "warning" if allowed else "error"
    hint = "" if allowed else f" Tell the owner, then add the PR label `{LABEL_CI}`."
    findings: list[Finding] = []
    for change in changes:
        if is_ci_path(change.path) or (change.old_path and is_ci_path(change.old_path)):
            findings.append(
                Finding(
                    "CI change",
                    f"This PR changes CI or secret-scan config.{hint}",
                    change.path,
                    level=level,
                )
            )
    return findings


def check_size(count: int) -> list[Finding]:
    if count <= MAX_CHANGED_LINES:
        return []
    return [
        Finding(
            "PR size",
            f"This PR changes {count} lines (limit {MAX_CHANGED_LINES}, lock files not counted). "
            "Split it into smaller PRs if possible.",
            level="warning",
        )
    ]


def escape_data(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_property(text: str) -> str:
    return escape_data(text).replace(":", "%3A").replace(",", "%2C")


def annotation(finding: Finding) -> str:
    props = []
    if finding.path:
        props.append(f"file={escape_property(finding.path)}")
    if finding.line:
        props.append(f"line={finding.line}")
    props.append(f"title={escape_property('guardrails: ' + finding.check)}")
    return f"::{finding.level} {','.join(props)}::{escape_data(finding.message)}"


CHECK_NAMES = ["Large file", "Data file", "Suppression", "CI change", "PR size"]


def write_summary(findings: list[Finding]) -> None:
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return
    rows = ["## guardrails diff checks", "", "| Check | Result |", "|---|---|"]
    for name in CHECK_NAMES:
        mine = [f for f in findings if f.check == name]
        errors = sum(f.level == "error" for f in mine)
        warnings = len(mine) - errors
        result = "fail" if errors else ("warning" if warnings else "pass")
        detail = f" ({errors} errors, {warnings} warnings)" if mine else ""
        rows.append(f"| {name} | {result}{detail} |")
    with open(target, "a", encoding="utf-8") as fh:
        fh.write("\n".join(rows) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--labels", default="", help="comma-separated PR labels")
    parser.add_argument("--self-repo", action="store_true", help="skip the CI change check")
    args = parser.parse_args(argv)
    labels = {label.strip() for label in args.labels.split(",") if label.strip()}

    changes = changed_files(args.base, args.head)
    live = [c.path for c in changes if c.status != "D"]
    findings = check_files(changes, blob_sizes(args.head, live))
    findings += check_suppressions(added_lines(args.base, args.head), LABEL_SUPPRESSION in labels)
    if not args.self_repo:
        findings += check_ci_changes(changes, LABEL_CI in labels)
    findings += check_size(changed_line_count(args.base, args.head))

    for finding in findings:
        print(annotation(finding))
    write_summary(findings)
    errors = sum(f.level == "error" for f in findings)
    print(
        f"guardrails: {len(changes)} files checked, {errors} errors, "
        f"{len(findings) - errors} warnings."
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
