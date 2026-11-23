"""Tests for scripts/apply_ruleset.py with a fake `gh api`."""

import copy
import json
from typing import Any

import apply_ruleset
import pytest


class FakeGh:
    """Records calls. Answers GET from a dict of path -> response."""

    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, Any]] = []

    def __call__(self, method: str, path: str, body: Any = None) -> Any:
        self.calls.append((method, path, body))
        if method == "GET":
            return copy.deepcopy(self.responses[path])
        return {"id": 99}

    @property
    def writes(self) -> list[tuple[str, str, Any]]:
        return [c for c in self.calls if c[0] != "GET"]


def repo_info(private: bool = False, archived: bool = False) -> dict[str, Any]:
    return {
        "private": private,
        "archived": archived,
        "visibility": "private" if private else "public",
    }


def fake(existing: dict[str, Any] | None = None, **info: bool) -> FakeGh:
    responses: dict[str, Any] = {"repos/designed7000/demo": repo_info(**info)}
    if existing is None:
        responses["repos/designed7000/demo/rulesets"] = []
    else:
        responses["repos/designed7000/demo/rulesets"] = [{"id": 7, "name": existing["name"]}]
        responses["repos/designed7000/demo/rulesets/7"] = existing
    return FakeGh(responses)


def rule(body: dict[str, Any], kind: str) -> dict[str, Any]:
    return next(r for r in body["rules"] if r["type"] == kind)


def test_desired_ruleset_has_the_four_rules() -> None:
    body = apply_ruleset.desired_ruleset(["ci / gate"])
    assert body["name"] == "guardrails"
    assert body["enforcement"] == "active"
    assert body["bypass_actors"] == []
    assert body["conditions"]["ref_name"]["include"] == ["~DEFAULT_BRANCH"]
    assert {r["type"] for r in body["rules"]} == {
        "pull_request",
        "required_status_checks",
        "non_fast_forward",
        "deletion",
    }
    assert rule(body, "pull_request")["parameters"]["required_approving_review_count"] == 0
    checks = rule(body, "required_status_checks")["parameters"]["required_status_checks"]
    assert checks == [{"context": "ci / gate", "integration_id": 15368}]


def test_dry_run_prints_diff_and_does_not_write(capsys: pytest.CaptureFixture[str]) -> None:
    gh = fake()
    assert apply_ruleset.main(["demo"], gh=gh) == 0
    out = capsys.readouterr().out
    assert "designed7000/demo: create" in out
    assert '+  "name": "guardrails"' in out
    assert "dry run" in out
    assert gh.writes == []


def test_apply_creates_ruleset() -> None:
    gh = fake()
    assert apply_ruleset.main(["--apply", "designed7000/demo"], gh=gh) == 0
    assert len(gh.writes) == 1
    method, path, body = gh.writes[0]
    assert (method, path) == ("POST", "repos/designed7000/demo/rulesets")
    assert body == apply_ruleset.desired_ruleset(["ci / gate"])


def test_existing_equal_ruleset_is_no_change(capsys: pytest.CaptureFixture[str]) -> None:
    existing = apply_ruleset.desired_ruleset(["ci / gate"])
    # GitHub adds fields that we do not set. They must not count as a change.
    existing |= {"id": 7, "source": "designed7000/demo", "node_id": "x", "_links": {}}
    rule(existing, "pull_request")["parameters"]["automatic_copilot_code_review_enabled"] = False
    gh = fake(existing)
    assert apply_ruleset.main(["--apply", "demo"], gh=gh) == 0
    assert "no change" in capsys.readouterr().out
    assert gh.writes == []


def test_changed_ruleset_is_updated(capsys: pytest.CaptureFixture[str]) -> None:
    existing = apply_ruleset.desired_ruleset(["old / check"])
    existing["id"] = 7
    gh = fake(existing)
    assert apply_ruleset.main(["--apply", "demo"], gh=gh) == 0
    out = capsys.readouterr().out
    assert '-            "context": "old / check"' in out
    assert '+            "context": "ci / gate"' in out
    assert gh.writes[0][:2] == ("PUT", "repos/designed7000/demo/rulesets/7")


def test_extra_rule_in_existing_shows_as_removed(capsys: pytest.CaptureFixture[str]) -> None:
    existing = apply_ruleset.desired_ruleset(["ci / gate"])
    existing["rules"].append({"type": "required_signatures"})
    gh = fake(existing)
    assert apply_ruleset.main(["demo"], gh=gh) == 0
    assert '-      "type": "required_signatures"' in capsys.readouterr().out


def test_private_repo_is_skipped(capsys: pytest.CaptureFixture[str]) -> None:
    gh = fake(private=True)
    assert apply_ruleset.main(["--apply", "demo"], gh=gh) == 0
    assert "skip (private" in capsys.readouterr().out
    assert gh.writes == []


def test_archived_repo_is_skipped(capsys: pytest.CaptureFixture[str]) -> None:
    gh = fake(archived=True)
    assert apply_ruleset.main(["--apply", "demo"], gh=gh) == 0
    assert "skip (archived" in capsys.readouterr().out


def test_check_option_replaces_required_checks() -> None:
    gh = fake()
    assert apply_ruleset.main(["--apply", "--check", "test", "--check", "lint", "demo"], gh=gh) == 0
    body = gh.writes[0][2]
    checks = rule(body, "required_status_checks")["parameters"]["required_status_checks"]
    assert [c["context"] for c in checks] == ["test", "lint"]


def test_gh_error_is_reported_and_other_repos_continue(capsys: pytest.CaptureFixture[str]) -> None:
    gh = fake()

    def failing(method: str, path: str, body: Any = None) -> Any:
        if path.startswith("repos/designed7000/broken"):
            raise apply_ruleset.GhError("HTTP 404")
        return gh(method, path, body)

    assert apply_ruleset.main(["broken", "demo"], gh=failing) == 1
    out = capsys.readouterr().out
    assert "designed7000/broken: error: HTTP 404" in out
    assert "designed7000/demo: create" in out


def test_ruleset_file_is_valid_json() -> None:
    body = json.loads(apply_ruleset.RULESET_FILE.read_text())
    assert body["name"] == "guardrails"
