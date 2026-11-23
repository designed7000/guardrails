"""Apply the guardrails ruleset to a list of repos with `gh api`. Stdlib only.

Dry run is the default: it prints the change and writes nothing.

    python3 scripts/apply_ruleset.py wiki-pulse Risk_Cli           # dry run
    python3 scripts/apply_ruleset.py --apply wiki-pulse            # write
    python3 scripts/apply_ruleset.py --check test Euro_stepper_analyst

Private repos are skipped: rulesets on private repos need GitHub Pro.
"""

from __future__ import annotations

import argparse
import copy
import difflib
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

OWNER = "designed7000"
DEFAULT_CHECKS = ["ci / gate"]
GITHUB_ACTIONS_APP_ID = 15368
RULESET_FILE = Path(__file__).resolve().parent.parent / "ruleset" / "ruleset.json"
COMPARED_KEYS = ("name", "target", "enforcement", "bypass_actors", "conditions", "rules")

Gh = Callable[[str, str, Any], Any]


class GhError(RuntimeError):
    pass


def real_gh(method: str, path: str, body: Any = None) -> Any:
    cmd = ["gh", "api", "-X", method, "-H", "Accept: application/vnd.github+json", path]
    if body is not None:
        cmd += ["--input", "-"]
    proc = subprocess.run(
        cmd,
        input=None if body is None else json.dumps(body),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise GhError((proc.stderr or proc.stdout).strip())
    return json.loads(proc.stdout) if proc.stdout.strip() else None


def desired_ruleset(checks: list[str]) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(RULESET_FILE.read_text())
    for rule in body["rules"]:
        if rule["type"] == "required_status_checks":
            rule["parameters"]["required_status_checks"] = [
                {"context": c, "integration_id": GITHUB_ACTIONS_APP_ID} for c in checks
            ]
    return body


def project(existing: dict[str, Any], desired: dict[str, Any]) -> dict[str, Any]:
    """Keep only what the desired body sets, so fields GitHub adds are not a change.

    Rules that only the existing ruleset has are kept whole: an update removes them.
    """
    result = {k: copy.deepcopy(existing.get(k)) for k in COMPARED_KEYS}
    wanted = {r["type"]: r for r in desired["rules"]}
    rules = []
    for rule in existing.get("rules", []):
        want = wanted.get(rule["type"])
        if want is None or "parameters" not in want:
            rules.append({k: v for k, v in rule.items() if k in ("type", "parameters")})
            continue
        params = rule.get("parameters", {})
        rules.append(
            {
                "type": rule["type"],
                "parameters": {k: params.get(k) for k in want["parameters"]},
            }
        )
    order = {t: i for i, t in enumerate(wanted)}
    result["rules"] = sorted(rules, key=lambda r: order.get(r["type"], len(order)))
    return result


def render(body: dict[str, Any] | None) -> list[str]:
    if body is None:
        return []
    return json.dumps(body, indent=2, sort_keys=False).splitlines()


def full_name(repo: str) -> str:
    return repo if "/" in repo else f"{OWNER}/{repo}"


def apply_repo(repo: str, checks: list[str], apply: bool, gh: Gh) -> str:
    info = gh("GET", f"repos/{repo}", None)
    if info.get("archived"):
        return "skip (archived)"
    if info.get("private"):
        return "skip (private: rulesets need GitHub Pro)"

    desired = desired_ruleset(checks)
    summary = next(
        (r for r in gh("GET", f"repos/{repo}/rulesets", None) if r["name"] == desired["name"]),
        None,
    )
    current = None
    if summary is not None:
        current = project(gh("GET", f"repos/{repo}/rulesets/{summary['id']}", None), desired)
        if current == desired:
            return "no change"

    action = "create" if summary is None else "update"
    diff = difflib.unified_diff(
        render(current), render(desired), "current", "desired", lineterm="", n=3
    )
    print(f"{repo}: {action} ruleset {desired['name']!r}")
    print("\n".join(diff))
    if not apply:
        return f"{action} (dry run: nothing written, use --apply)"
    if summary is None:
        gh("POST", f"repos/{repo}/rulesets", desired)
    else:
        gh("PUT", f"repos/{repo}/rulesets/{summary['id']}", desired)
    return f"{action}d"


def main(argv: list[str] | None = None, gh: Gh = real_gh) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("repos", nargs="+", help=f"repo or owner/repo (default owner {OWNER})")
    parser.add_argument("--apply", action="store_true", help="write the change (default: dry run)")
    parser.add_argument(
        "--check",
        action="append",
        dest="checks",
        help="required check name; repeat for more (default: 'ci / gate')",
    )
    args = parser.parse_args(argv)
    checks = args.checks or DEFAULT_CHECKS

    failed = False
    for name in args.repos:
        repo = full_name(name)
        try:
            print(f"{repo}: {apply_repo(repo, checks, args.apply, gh)}")
        except GhError as exc:
            failed = True
            print(f"{repo}: error: {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
