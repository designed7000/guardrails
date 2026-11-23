#!/usr/bin/env python3
"""Claude Code PreToolUse hook. Stdlib only.

Blocks: force pushes, pushes to main or master, and `gh` commands that put the
owner-only labels `allow-suppression` or `allow-ci-change` on a PR or an issue (also any
`gh api` write to an issue's labels). Reading those labels stays allowed.

Reads the hook JSON on stdin. Exit 2 blocks the Bash call and shows the reason to
Claude. Exit 0 allows it. `--force-with-lease` stays allowed on other branches, and a
force push of an explicit tag (`refs/tags/v1`) is allowed for releases.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from typing import TextIO

PROTECTED = {"main", "master"}
OWNER_LABELS = ("allow-suppression", "allow-ci-change")
SEPARATORS = {"&&", "||", ";", "|", "&", "(", ")", ";;"}
WRAPPERS = {"command", "exec", "sudo", "env", "time", "nohup"}
GIT_OPTS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}
PUSH_OPTS_WITH_VALUE = {"--repo", "-o", "--push-option", "--receive-pack", "--exec"}
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
FALLBACK_FORCE = re.compile(
    r"\bgit\b[^;&|]*\bpush\b[^;&|]*(\s--force(?!-with-lease)\b|\s-[a-zA-Z]*f[a-zA-Z]*\b|\s--mirror\b)"
)


def segments(command: str) -> list[list[str]]:
    lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    result: list[list[str]] = [[]]
    for token in lexer:
        if token in SEPARATORS:
            result.append([])
        else:
            result[-1].append(token)
    return [s for s in result if s]


def current_branch(cwd: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    branch = proc.stdout.strip()
    return branch if proc.returncode == 0 and branch else None


def command_word(tokens: list[str]) -> int:
    """Index of the program name, after env assignments and wrappers like `sudo`."""
    i = 0
    while i < len(tokens) and (ASSIGNMENT.match(tokens[i]) or tokens[i] in WRAPPERS):
        i += 1
    return i


def is_gh(tokens: list[str]) -> bool:
    i = command_word(tokens)
    return i < len(tokens) and os.path.basename(tokens[i]) == "gh"


def has_owner_label(value: str) -> bool:
    return any(label in value for label in OWNER_LABELS)


def flag_value(args: list[str], names: tuple[str, ...]) -> list[str]:
    """Values of a flag, in the forms `--flag value` and `--flag=value`."""
    values = []
    for i, arg in enumerate(args):
        if arg in names and i + 1 < len(args):
            values.append(args[i + 1])
        for name in names:
            if arg.startswith(name + "="):
                values.append(arg.split("=", 1)[1])
    return values


def gh_adds_owner_label(args: list[str]) -> bool:
    """True when a gh command puts an owner-only label on a PR or an issue.

    Reads (`gh pr list --label ...`) and `gh label create` are allowed.
    """
    sub = tuple(args[:2])
    if sub in {("pr", "create"), ("issue", "create")}:
        return any(has_owner_label(v) for v in flag_value(args, ("--label", "-l")))
    if sub in {("pr", "edit"), ("issue", "edit")}:
        return any(has_owner_label(v) for v in flag_value(args, ("--add-label",)))
    if args[:1] == ["api"]:
        path = next((a for a in args[1:] if not a.startswith("-") and "/" in a), "")
        if not re.search(r"/issues/\d+/labels", path):
            return False
        method = (flag_value(args, ("-X", "--method")) or ["GET"])[-1].upper()
        writes_body = any(
            a in {"-f", "-F", "--field", "--raw-field", "--input"}
            or a.startswith(("--field=", "--raw-field=", "--input="))
            for a in args
        )
        return method != "GET" or writes_body
    return False


def split_git(tokens: list[str], cwd: str) -> tuple[str, str, list[str]] | None:
    """Return (cwd, subcommand, args) when the segment runs git, else None."""
    i = command_word(tokens)
    if i >= len(tokens) or os.path.basename(tokens[i]) != "git":
        return None
    i += 1
    while i < len(tokens) and tokens[i].startswith("-"):
        opt = tokens[i]
        if opt in GIT_OPTS_WITH_VALUE and i + 1 < len(tokens):
            if opt == "-C":
                cwd = os.path.join(cwd, os.path.expanduser(tokens[i + 1]))
            i += 2
        else:
            i += 1
    if i >= len(tokens):
        return None
    return cwd, tokens[i], tokens[i + 1 :]


def is_tag_only(refspecs: list[str]) -> bool:
    """True when every refspec writes an explicit tag (refs/tags/...)."""
    dsts = [(s.split(":", 1)[1] if ":" in s else s).lstrip("+") for s in refspecs]
    return bool(dsts) and all(d.startswith("refs/tags/") for d in dsts)


def check_push(args: list[str], cwd: str) -> str | None:
    positional: list[str] = []
    force: str | None = None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in PUSH_OPTS_WITH_VALUE:
            i += 2
            continue
        if arg == "--mirror":
            return "`--mirror` force-updates and deletes remote refs"
        if arg in ("--all", "--branches"):
            return "`--all` also pushes main"
        if arg == "--force" or arg.startswith("--force-if-includes"):
            force = "force push (`--force`) rewrites remote history"
        elif re.fullmatch(r"-[a-zA-Z]+", arg) and "f" in arg:
            force = "force push (`-f`) rewrites remote history"
        elif not arg.startswith("-"):
            positional.append(arg)
        i += 1

    refspecs = positional[1:]
    if is_tag_only(refspecs):
        return None  # moving a release tag, written out as refs/tags/...
    if force:
        return force
    if not refspecs:
        branch = current_branch(cwd)
        if branch in PROTECTED:
            return f"push from branch `{branch}` goes straight to `{branch}`"
        return None
    for spec in refspecs:
        if spec.startswith("+"):
            return f"force push (`{spec}`) rewrites remote history"
        dst = spec.split(":", 1)[1] if ":" in spec else spec
        if dst == "HEAD":
            dst = current_branch(cwd) or ""
        dst = dst.removeprefix("refs/heads/")
        if dst in PROTECTED:
            return f"push to `{dst}`"
    return None


def check_command(command: str, cwd: str) -> str | None:
    try:
        parts = segments(command)
    except ValueError:
        if FALLBACK_FORCE.search(command):
            return "force push rewrites remote history"
        return None
    for tokens in parts:
        if tokens[0] == "cd" and len(tokens) > 1:
            cwd = os.path.join(cwd, os.path.expanduser(tokens[1]))
            continue
        if is_gh(tokens) and gh_adds_owner_label(tokens[command_word(tokens) + 1 :]):
            return "only the owner adds the labels `allow-suppression` and `allow-ci-change`"
        found = split_git(tokens, cwd)
        if found is None:
            continue
        git_cwd, sub, args = found
        if sub == "push" and (reason := check_push(args, git_cwd)):
            return reason
    return None


def main(stdin: TextIO = sys.stdin) -> int:
    try:
        payload = json.load(stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return 0
    command = str(payload.get("tool_input", {}).get("command", ""))
    reason = check_command(command, str(payload.get("cwd") or os.getcwd()))
    if reason is None:
        return 0
    print(
        f"Blocked by the guardrails hook: {reason}. Push a feature branch and open a PR. "
        "If the owner wants this push, the owner runs it in a terminal.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
