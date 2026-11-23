"""Rules for the workflow files that the owner set. Text checks, stdlib only."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
TEMPLATE = (ROOT / "templates" / "caller.yml").read_text()
USES = re.compile(r"^\s*(?:-\s*)?uses:\s*(\S+)(.*)$", re.MULTILINE)


def test_caller_template_is_15_lines_or_fewer() -> None:
    assert len(TEMPLATE.splitlines()) <= 15


def test_caller_job_id_and_central_job_name_make_the_required_check() -> None:
    # The required check "ci / gate" is "<caller job id> / <central job name>".
    assert re.search(r"^  ci:\n    uses: designed7000/guardrails/.+@v1$", TEMPLATE, re.MULTILINE)
    assert re.search(r"^  gate:\n    name: gate$", CI, re.MULTILINE)


def test_third_party_actions_are_pinned_to_a_full_sha() -> None:
    for path in [*WORKFLOWS]:
        for ref, comment in USES.findall(path.read_text()):
            if ref.startswith("./") or ref.startswith("designed7000/guardrails/"):
                continue
            assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref), f"{path.name}: {ref}"
            assert re.match(r"\s*# v\d", comment), f"{path.name}: {ref} needs a '# vX.Y.Z' comment"


def test_every_job_sets_permissions_and_a_timeout() -> None:
    jobs = re.split(r"^  (\w+):\n", CI.split("\njobs:\n", 1)[1], flags=re.MULTILINE)[1:]
    for name, body in zip(jobs[::2], jobs[1::2], strict=True):
        assert "    permissions:" in body, f"job {name} has no permissions"
        assert "    timeout-minutes:" in body, f"job {name} has no timeout"


def test_pr_jobs_finish_within_10_minutes() -> None:
    for job in ("common", "python", "gate"):
        after = CI.split(f"\n  {job}:\n", 1)[1]
        match = re.search(r"timeout-minutes: (\d+)", after)
        assert match is not None
        assert int(match[1]) <= 10, f"{job}: {match[1]} minutes"


def test_diff_coverage_threshold_is_80_and_not_an_input() -> None:
    assert 'DIFF_COVERAGE_MIN: "80"' in CI
    inputs = CI.split("inputs:", 1)[1].split("\npermissions:", 1)[0]
    assert "coverage" not in inputs.lower()
