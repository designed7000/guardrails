"""Tests for claude/hooks/block_risky_push.py."""

import io
import json
import subprocess
from pathlib import Path

import block_risky_push
import pytest


def make_repo(path: Path, branch: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", branch], cwd=path, check=True)
    return path


@pytest.fixture
def on_feature(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "feature-repo", "feat")


@pytest.fixture
def on_main(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "main-repo", "main")


@pytest.mark.parametrize(
    "command",
    [
        "git push --force",
        "git push -f origin feat",
        "git push -uf origin feat",
        "git push origin +feat",
        "git add . && git push -f",
        "git commit -m 'x'; git push --force origin feat",
        "FOO=1 git push --force origin feat",
        "git -C /tmp/x push --force origin feat",
        "/usr/bin/git push --force origin feat",
        "git push --mirror",
    ],
)
def test_force_push_is_blocked(on_feature: Path, command: str) -> None:
    reason = block_risky_push.check_command(command, str(on_feature))
    assert reason is not None
    assert "force" in reason or "mirror" in reason


@pytest.mark.parametrize(
    "command",
    [
        "git push origin main",
        "git push origin HEAD:main",
        "git push origin feat:master",
        "git push origin refs/heads/main",
        "git push --all",
        "git push origin --delete main",
        "git push origin :main",
        "git push --force-with-lease origin main",
    ],
)
def test_push_to_main_is_blocked(on_feature: Path, command: str) -> None:
    reason = block_risky_push.check_command(command, str(on_feature))
    assert reason is not None


@pytest.mark.parametrize("command", ["git push", "git push origin", "git push -u origin HEAD"])
def test_push_of_current_branch_main_is_blocked(on_main: Path, command: str) -> None:
    reason = block_risky_push.check_command(command, str(on_main))
    assert reason is not None
    assert "main" in reason


def test_cd_into_repo_on_main_is_followed(on_main: Path, tmp_path: Path) -> None:
    command = f"cd {on_main} && git push"
    assert block_risky_push.check_command(command, str(tmp_path)) is not None


def test_dash_c_into_repo_on_main_is_followed(on_main: Path, tmp_path: Path) -> None:
    command = f"git -C {on_main} push"
    assert block_risky_push.check_command(command, str(tmp_path)) is not None


@pytest.mark.parametrize(
    "command",
    [
        "git push origin feat",
        "git push -u origin feat",
        "git push --force-with-lease origin feat",
        "git push",
        "git push origin main:feat",
        "git status",
        "echo 'git push --force'",
        "git commit -m 'push -f to main later'",
        "ls -la",
    ],
)
def test_safe_commands_are_allowed(on_feature: Path, command: str) -> None:
    assert block_risky_push.check_command(command, str(on_feature)) is None


def test_unbalanced_quotes_fall_back_to_simple_match(on_feature: Path) -> None:
    assert block_risky_push.check_command("git push --force 'oops", str(on_feature)) is not None
    assert block_risky_push.check_command("echo 'oops", str(on_feature)) is None


def run_hook(payload: object, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = block_risky_push.main(io.StringIO(json.dumps(payload)))
    return code, capsys.readouterr().err


def test_hook_blocks_with_exit_2(on_feature: Path, capsys: pytest.CaptureFixture[str]) -> None:
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "git push -f"},
        "cwd": str(on_feature),
    }
    code, err = run_hook(payload, capsys)
    assert code == 2
    assert "Blocked by the guardrails hook" in err


def test_hook_allows_with_exit_0(on_feature: Path, capsys: pytest.CaptureFixture[str]) -> None:
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "git push origin feat"},
        "cwd": str(on_feature),
    }
    assert run_hook(payload, capsys) == (0, "")


def test_hook_ignores_other_tools_and_bad_input(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_hook({"tool_name": "Read", "tool_input": {"file_path": "x"}}, capsys)[0] == 0
    assert block_risky_push.main(io.StringIO("not json")) == 0


@pytest.mark.parametrize(
    "command",
    [
        "gh pr edit 12 --add-label allow-suppression",
        "gh pr create --fill --label allow-ci-change",
        "gh api repos/o/r/issues/3/labels -f 'labels[]=allow-ci-change'",
        "git push origin feat && gh pr edit --add-label bug,allow-suppression",
    ],
)
def test_gh_cannot_add_owner_labels(on_feature: Path, command: str) -> None:
    reason = block_risky_push.check_command(command, str(on_feature))
    assert reason is not None
    assert "owner" in reason


def test_gh_with_other_labels_is_allowed(on_feature: Path) -> None:
    assert block_risky_push.check_command("gh pr edit 3 --add-label bug", str(on_feature)) is None


def test_force_push_of_an_explicit_tag_is_allowed(on_feature: Path) -> None:
    # Moving the release tag v1 is part of the guardrails release steps.
    command = "git push -f origin refs/tags/v1"
    assert block_risky_push.check_command(command, str(on_feature)) is None


@pytest.mark.parametrize(
    "command",
    [
        "git push -f origin v1",
        "git push -f origin refs/tags/v1 feat",
        "git push -f",
        "git push -f origin",
    ],
)
def test_force_push_without_explicit_tag_is_blocked(on_feature: Path, command: str) -> None:
    assert block_risky_push.check_command(command, str(on_feature)) is not None
