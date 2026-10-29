from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from runner.announced_inputs import AnnouncedInputError, resolve_announced_files


def git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args], check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def repository(tmp_path: Path) -> tuple[Path, Path, Path]:
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, stdout=subprocess.PIPE)
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-b", "main", str(seed)], check=True, stdout=subprocess.PIPE)
    git(seed, "config", "user.email", "test@example.invalid")
    git(seed, "config", "user.name", "Test")
    match = seed / "assigned-folder"
    (match / "challenge").mkdir(parents=True)
    (match / "README.md").write_text("baseline\n")
    git(seed, "add", "assigned-folder/README.md")
    git(seed, "commit", "-m", "baseline")
    git(seed, "remote", "add", "origin", str(bare))
    git(seed, "push", "-u", "origin", "main")
    checkout = tmp_path / "checkout"
    subprocess.run(["git", "clone", "--branch", "main", str(bare), str(checkout)], check=True, stdout=subprocess.PIPE)
    git(checkout, "config", "user.email", "test@example.invalid")
    git(checkout, "config", "user.name", "Test")
    # Advance the trusted remote after the checkout, as happens when a release is published.
    (match / "challenge" / "TASK-7.md").write_text("public task document\n")
    (match / "challenge" / "TASK-7-extra.md").write_text("announced supplemental document\n")
    (match / "challenge" / "TASK-7-schema.json").write_text('{"public": true}\n')
    (match / "challenge" / "TASK-8.md").write_text("another task, must not be returned\n")
    (match / "challenge" / "TASK-7-hidden.txt").write_text("unannounced\n")
    (match / "private.txt").write_text("outside match task folder\n")
    git(seed, "add", "assigned-folder")
    git(seed, "commit", "-m", "publish public task files")
    git(seed, "push", "origin", "main")
    return checkout, checkout / "assigned-folder", seed


def test_fetches_only_announced_public_path_from_advanced_branch(tmp_path: Path) -> None:
    checkout, match, _seed = repository(tmp_path)
    result = resolve_announced_files(
        checkout, match, "main", "TASK-7", ["challenge/TASK-7.md"], {},
    )
    assert result == {"challenge/TASK-7.md": "public task document\n"}


def test_api_payload_wins_and_avoids_fetch_when_complete(tmp_path: Path) -> None:
    checkout, match, _seed = repository(tmp_path)
    result = resolve_announced_files(
        checkout, match, "main", "TASK-7", ["challenge/TASK-7.md"],
        {"challenge/TASK-7.md": "Start Work authoritative text"},
    )
    assert result == {"challenge/TASK-7.md": "Start Work authoritative text"}
    assert not (checkout / ".git" / "FETCH_HEAD").exists()


def test_api_payload_is_preferred_while_other_announced_file_is_fetched(tmp_path: Path) -> None:
    checkout, match, _seed = repository(tmp_path)
    result = resolve_announced_files(
        checkout, match, "main", "TASK-7",
        ["challenge/TASK-7.md", "challenge/TASK-7-extra.md"],
        {"challenge/TASK-7.md": "API text"},
    )
    assert result["challenge/TASK-7.md"] == "API text"
    assert result["challenge/TASK-7-extra.md"] == "announced supplemental document\n"


def test_realistic_release_markdown_and_start_work_json_ts_inputs(tmp_path: Path) -> None:
    checkout, match, _seed = repository(tmp_path)
    result = resolve_announced_files(
        checkout, match, "main", "TASK-7",
        ["challenge/TASK-7.md", "challenge/TASK-7-schema.json", "challenge/TASK-7-seed.ts"],
        {
            "challenge/TASK-7-schema.json": '{"from":"api"}',
            "challenge/TASK-7-seed.ts": "export const seed = true;",
        },
    )
    assert result == {
        "challenge/TASK-7.md": "public task document\n",
        "challenge/TASK-7-schema.json": '{"from":"api"}',
        "challenge/TASK-7-seed.ts": "export const seed = true;",
    }


def test_optional_missing_file_reports_absence_and_returns_delivered_inputs(tmp_path: Path) -> None:
    checkout, match, _seed = repository(tmp_path)
    unavailable: list[str] = []
    delivered = {"challenge/TASK-7-schema.json": '{"checks": []}'}
    result = resolve_announced_files(
        checkout, match, "main", "TASK-7",
        ["challenge/TASK-7-schema.json", "challenge/TASK-7-missing.md"],
        delivered,
        missing_paths=unavailable,
    )
    assert result == delivered
    assert unavailable == ["challenge/TASK-7-missing.md"]


def test_missing_file_remains_strict_by_default(tmp_path: Path) -> None:
    checkout, match, _seed = repository(tmp_path)
    with pytest.raises(AnnouncedInputError, match="missing from the assigned branch"):
        resolve_announced_files(
            checkout, match, "main", "TASK-7", ["challenge/TASK-7-missing.md"], {},
        )


@pytest.mark.parametrize("path", [
    "challenge/TASK-8.md", "../challenge/TASK-7.md", "/challenge/TASK-7.md",
    "answers/TASK-7/answer.md", "challenge/TASK-7/secret.md", "challenge/.env",
    "challenge/TASK-7-binary.exe", "challenge/TASK-7-.pem",
])
def test_rejects_unannounced_or_out_of_scope_paths(tmp_path: Path, path: str) -> None:
    checkout, match, _seed = repository(tmp_path)
    with pytest.raises(AnnouncedInputError):
        resolve_announced_files(checkout, match, "main", "TASK-7", [path], {})


def test_rejects_symlink_blob(tmp_path: Path) -> None:
    checkout, match, seed = repository(tmp_path)
    target = seed / "assigned-folder" / "challenge" / "TASK-7.md"
    target.unlink()
    target.symlink_to("../../private.txt")
    git(seed, "add", "-A", "assigned-folder/challenge/TASK-7.md")
    git(seed, "commit", "-m", "malformed public input")
    git(seed, "push", "origin", "main")
    with pytest.raises(AnnouncedInputError, match="not a regular file"):
        resolve_announced_files(checkout, match, "main", "TASK-7", ["challenge/TASK-7.md"], {})


def test_rejects_delivered_unannounced_file(tmp_path: Path) -> None:
    checkout, match, _seed = repository(tmp_path)
    with pytest.raises(AnnouncedInputError, match="unannounced"):
        resolve_announced_files(
            checkout, match, "main", "TASK-7", ["challenge/TASK-7.md"],
        {"challenge/TASK-7-other.md": "not announced"},
        )
