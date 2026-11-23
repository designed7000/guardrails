"""Tests for checks/diff_checks.py. Each test builds a small git repo."""

import subprocess
from collections.abc import Callable
from pathlib import Path

import diff_checks
import pytest

# Built from pieces so that this file does not trip the check it tests.
NO_QA = "no" + "qa"
TYPE_IGNORE = "type: " + "ignore"
MIB = 1024 * 1024

Commit = Callable[[dict[str, str | bytes | None]], str]


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "test")
    git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / "README.md").write_text("start\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "base")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    return tmp_path


@pytest.fixture
def commit(repo: Path) -> Commit:
    """Write files (None deletes) and commit. Return the new commit SHA."""

    def _commit(files: dict[str, str | bytes | None]) -> str:
        for name, content in files.items():
            path = repo / name
            if content is None:
                path.unlink()
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "change")
        return git(repo, "rev-parse", "HEAD")

    return _commit


def run(base: str, *extra: str) -> int:
    return diff_checks.main(["--base", base, "--head", "HEAD", *extra])


def base_sha(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD")


def test_clean_change_passes(
    repo: Path, commit: Commit, capsys: pytest.CaptureFixture[str]
) -> None:
    base = base_sha(repo)
    commit({"src/app.py": "def add(a: int, b: int) -> int:\n    return a + b\n"})
    assert run(base) == 0
    assert "::error" not in capsys.readouterr().out


def test_file_over_5_mib_fails(
    repo: Path, commit: Commit, capsys: pytest.CaptureFixture[str]
) -> None:
    base = base_sha(repo)
    commit({"blob.bin": b"x" * (5 * MIB + 1)})
    assert run(base) == 1
    out = capsys.readouterr().out
    assert "::error file=blob.bin" in out
    assert "5 MB" in out


def test_file_at_5_mib_passes(repo: Path, commit: Commit) -> None:
    base = base_sha(repo)
    commit({"blob.bin": b"x" * (5 * MIB)})
    assert run(base) == 0


@pytest.mark.parametrize(
    "path",
    ["data/x.parquet", "w.duckdb", "a.db", "a.sqlite", "a.sqlite3", ".env", "config/.env.local"],
)
def test_data_file_fails(
    repo: Path, commit: Commit, capsys: pytest.CaptureFixture[str], path: str
) -> None:
    base = base_sha(repo)
    commit({path: "data\n"})
    assert run(base) == 1
    assert f"::error file={path}" in capsys.readouterr().out


@pytest.mark.parametrize("path", [".env.example", ".env.sample", ".env.template", "envoy.py"])
def test_env_templates_allowed(repo: Path, commit: Commit, path: str) -> None:
    base = base_sha(repo)
    commit({path: "KEY=\n"})
    assert run(base) == 0


def test_rename_to_data_file_fails(repo: Path, commit: Commit) -> None:
    commit({"a.csv": "x,y\n1,2\n"})
    base = base_sha(repo)
    git(repo, "mv", "a.csv", "a.parquet")
    git(repo, "commit", "-q", "-m", "rename")
    assert run(base) == 1


def test_deleting_big_or_data_file_passes(repo: Path, commit: Commit) -> None:
    commit({".env": "SECRET=1\n", "blob.bin": b"x" * (5 * MIB + 1)})
    base = base_sha(repo)
    commit({".env": None, "blob.bin": None})
    assert run(base) == 0


@pytest.mark.parametrize(
    "line",
    [
        f"x = 1  # {NO_QA}: E501",
        f"x: int = 'a'  # {TYPE_IGNORE}[assignment]",
        "y = 2  # pyright: " + "ignore",
        "# mypy: " + "ignore-errors",
        "@pytest.mark." + "skip(reason='later')",
        "@pytest.mark." + "skipif(True, reason='x')",
        "@pytest.mark." + "xfail",
        "    pytest." + "skip('later')",
        "@unittest." + "skip('later')",
        "if x:  # pragma: " + "no cover",
    ],
)
def test_python_suppression_fails(
    repo: Path, commit: Commit, capsys: pytest.CaptureFixture[str], line: str
) -> None:
    base = base_sha(repo)
    commit({"src/app.py": f"import pytest\n{line}\n"})
    assert run(base) == 1
    out = capsys.readouterr().out
    assert "::error file=src/app.py,line=2" in out
    assert "allow-suppression" in out


@pytest.mark.parametrize(
    "line",
    [
        "// eslint-" + "disable-next-line",
        "// @ts-" + "ignore",
        "// @ts-" + "expect-error",
        "// @ts-" + "nocheck",
    ],
)
def test_js_suppression_fails(repo: Path, commit: Commit, line: str) -> None:
    base = base_sha(repo)
    commit({"web/app.ts": f"{line}\nconst a = 1;\n"})
    assert run(base) == 1


def test_suppression_with_label_passes(
    repo: Path, commit: Commit, capsys: pytest.CaptureFixture[str]
) -> None:
    base = base_sha(repo)
    commit({"src/app.py": f"x = 1  # {NO_QA}\n"})
    assert run(base, "--labels", "bug,allow-suppression") == 0
    assert "::warning file=src/app.py,line=1" in capsys.readouterr().out


def test_suppression_in_docs_is_ignored(repo: Path, commit: Commit) -> None:
    base = base_sha(repo)
    commit({"README.md": f"Do not add `# {NO_QA}`.\n", "notes.txt": f"{TYPE_IGNORE}\n"})
    assert run(base) == 0


def test_existing_suppression_is_not_flagged(repo: Path, commit: Commit) -> None:
    commit({"src/app.py": f"x = 1  # {NO_QA}\ny = 2\n"})
    base = base_sha(repo)
    commit({"src/app.py": f"x = 1  # {NO_QA}\ny = 3\n"})
    assert run(base) == 0


@pytest.mark.parametrize(
    ("path", "line"),
    [
        ("pyproject.toml", 'ignore = ["E501"]'),
        ("pyproject.toml", '"src/x.py" = ["E501"]\nper-file-ignores = {}'),
        ("pyproject.toml", "ignore_missing_imports = true"),
        ("pyproject.toml", 'disable_error_code = ["attr-defined"]'),
        ("setup.cfg", "fail_under = 50"),
        (".coveragerc", "omit = src/legacy/*"),
        ("mypy.ini", "ignore_errors = True"),
    ],
)
def test_config_ignore_key_fails(repo: Path, commit: Commit, path: str, line: str) -> None:
    base = base_sha(repo)
    commit({path: f"[section]\n{line}\n"})
    assert run(base) == 1


def test_ignore_key_in_code_file_is_not_a_config_change(repo: Path, commit: Commit) -> None:
    base = base_sha(repo)
    commit({"src/app.py": "ignore = True\n"})
    assert run(base) == 0


@pytest.mark.parametrize("path", [".github/workflows/ci.yml", ".gitleaks.toml", ".gitleaksignore"])
def test_ci_change_needs_label(
    repo: Path, commit: Commit, capsys: pytest.CaptureFixture[str], path: str
) -> None:
    base = base_sha(repo)
    commit({path: "x: 1\n"})
    assert run(base) == 1
    assert "allow-ci-change" in capsys.readouterr().out
    assert run(base, "--labels", "allow-ci-change") == 0


def test_ci_file_deletion_needs_label(repo: Path, commit: Commit) -> None:
    commit({".github/workflows/ci.yml": "x: 1\n"})
    base = base_sha(repo)
    commit({".github/workflows/ci.yml": None})
    assert run(base) == 1


def test_self_repo_skips_ci_change_check(repo: Path, commit: Commit) -> None:
    base = base_sha(repo)
    commit({".github/workflows/ci.yml": "x: 1\n"})
    assert run(base, "--self-repo") == 0


def test_large_diff_warns_but_passes(
    repo: Path, commit: Commit, capsys: pytest.CaptureFixture[str]
) -> None:
    base = base_sha(repo)
    commit({"src/big.py": "".join(f"x{i} = {i}\n" for i in range(801))})
    assert run(base) == 0
    out = capsys.readouterr().out
    assert "::warning" in out
    assert "801" in out


def test_lock_files_do_not_count_to_diff_size(
    repo: Path, commit: Commit, capsys: pytest.CaptureFixture[str]
) -> None:
    base = base_sha(repo)
    lines = "".join(f"line {i}\n" for i in range(2000))
    commit({"uv.lock": lines, "web/package-lock.json": lines, "src/a.py": "a = 1\n"})
    assert run(base) == 0
    assert "::warning" not in capsys.readouterr().out


def test_summary_is_written(
    repo: Path,
    commit: Commit,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary = tmp_path_factory.mktemp("summary") / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    base = base_sha(repo)
    commit({".env": "A=1\n"})
    assert run(base) == 1
    text = summary.read_text()
    assert "| Check | Result |" in text
    assert "Data file" in text


def test_annotation_message_is_escaped(
    repo: Path, commit: Commit, capsys: pytest.CaptureFixture[str]
) -> None:
    base = base_sha(repo)
    commit({"a,b.parquet": "x\n"})
    assert run(base) == 1
    assert "::error file=a%2Cb.parquet" in capsys.readouterr().out
