"""
tests/test_challenger_2_gen8_adversarial.py: Adversarial Test Harness for Gen 8 (R3 & R4).

Empirical verification of:
1. R4 (Folder Alias Rejection):
   - In `verify_assigned_repository`:
     - Test all variations with `..`: `..`, `../`, `repo/..`, `repo/../..`, `a/b/../../..`,
       `foo/bar/../baz`, `.\\..`, `a\\b\\..\\..`, `..\\`, `foo/./../bar`, `/..`, `//..`,
       `repo/../../outside`, `a/b/c/../../../..`.
     - Verify ALL of them raise ValueError with message containing "path component".
     - Test `.` and `./` and `.\\`: verify they succeed and point to root.
     - Test normal valid subfolder `repo`, `src/core`, `deep/nested/subfolder`: verify they succeed.
     - Test non-existent folder and file-as-folder: verify fail-closed.
2. R3 (Workspace Path Resolution):
   - Test MCPHelperServer initialization:
     - `workspace_root=Path("/workspace/team-sdlc-pack")` -> resolves to `/workspace`
     - `workspace_root=Path("/workspace/TEAM-SDLC-PACK")` -> resolves to `/workspace`
     - `workspace_root=Path("/workspace/other-folder")` -> resolves to `/workspace/other-folder`
     - Windows paths and casing variations: `C:\\workspace\\team-sdlc-pack`, etc.
     - CWD resolution when cwd ends with `team-sdlc-pack`.
   - Test `configure_session`:
     - `teamWorkspace="/custom/ws"` -> updates `self.workspace_root` to `/custom/ws`
     - `teamWorkspace="/custom/ws/team-sdlc-pack"` -> strips to `/custom/ws`
     - `teamWorkspace="/custom/ws/TEAM-SDLC-PACK"` -> strips to `/custom/ws`
     - `team_workspace` (snake_case fallback) -> updates `self.workspace_root`
     - `teamSdlcPackWorkspace` -> updates `self.workspace_root`
   - Knowledge & Journal DB path construction:
     - Verify Knowledge output path has exactly 1 `team-sdlc-pack` component.
     - Verify Journal DB path is `<workspace_root>/.mtm/submissions.db`.
     - End-to-end PREPARE_GAME execution verifying physical creation without duplicate folders.
"""

import os
import sys
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from mtm_core.git.repository import verify_assigned_repository, run_git
from runner.mcp_server import MCPHelperServer


# ==============================================================================
# Suite 1: R4 Folder Alias Rejection Stress Testing
# ==============================================================================

class TestR4FolderAliasRejectionStress:
    """Adversarial stress-testing of folder alias rejection in verify_assigned_repository."""

    DOTDOT_VARIATIONS = [
        "..",
        "../",
        "repo/..",
        "repo/../..",
        "a/b/../../..",
        "foo/bar/../baz",
        ".\\..",
        "a\\b\\..\\..",
        "..\\",
        ".\\..\\",
        "repo\\..",
        "repo/../",
        "repo\\..\\",
        "foo/./../bar",
        "/..",
        "//..",
        "repo/../../outside",
        "a/b/c/../../../..",
        "src/core/../../..",
        "src/../..",
    ]

    @pytest.mark.parametrize("dotdot_folder", DOTDOT_VARIATIONS)
    def test_verify_assigned_repository_rejects_dotdot_variations(
        self, offline_git_env: Dict[str, Any], dotdot_folder: str
    ):
        """All variations containing '..' path components MUST raise ValueError with 'path component'."""
        checkout_dir = offline_git_env["checkout_dir"]
        remote_url = offline_git_env["remote_url"]

        descriptor = {
            "url": remote_url,
            "branch": "main",
            "folder": dotdot_folder,
        }

        with pytest.raises(ValueError) as exc_info:
            verify_assigned_repository(checkout_dir, descriptor)

        error_msg = str(exc_info.value)
        assert "path component" in error_msg, (
            f"Expected 'path component' in error message for folder '{dotdot_folder}', got: {error_msg}"
        )

    @pytest.mark.parametrize("dot_folder", [".", "./", ".\\", " . ", " ./ "])
    def test_verify_assigned_repository_allows_explicit_root_aliases(
        self, offline_git_env: Dict[str, Any], dot_folder: str
    ):
        """Explicit root aliases ('.', './', '.\\', with whitespace) MUST succeed and point to root."""
        checkout_dir = offline_git_env["checkout_dir"]
        remote_url = offline_git_env["remote_url"]

        descriptor = {
            "url": remote_url,
            "branch": "main",
            "folder": dot_folder,
        }

        res = verify_assigned_repository(checkout_dir, descriptor)
        assert res["match_folder"] == checkout_dir
        assert res["folder_path"] == checkout_dir
        assert res["branch"] == "main"

    def test_verify_assigned_repository_valid_subfolders(
        self, offline_git_env: Dict[str, Any]
    ):
        """Valid existing subfolders succeed and resolve correctly under checkout_root."""
        checkout_dir = offline_git_env["checkout_dir"]
        remote_url = offline_git_env["remote_url"]

        # 1. Existing 'repo' subfolder
        res_repo = verify_assigned_repository(
            checkout_dir,
            {"url": remote_url, "branch": "main", "folder": "repo"},
        )
        assert res_repo["match_folder"] == (checkout_dir / "repo").resolve()

        # 2. Created 'src/core' subfolder
        src_core = checkout_dir / "src" / "core"
        src_core.mkdir(parents=True, exist_ok=True)
        (src_core / "mod.py").write_text("# mod\n", encoding="utf-8")
        run_git(checkout_dir, ["add", "-A"])
        run_git(checkout_dir, ["commit", "-m", "add src/core"])

        res_src_core = verify_assigned_repository(
            checkout_dir,
            {"url": remote_url, "branch": "main", "folder": "src/core"},
        )
        assert res_src_core["match_folder"] == src_core.resolve()

        # 3. Created 'src\\core' with backslash
        res_src_core_bs = verify_assigned_repository(
            checkout_dir,
            {"url": remote_url, "branch": "main", "folder": "src\\core"},
        )
        assert res_src_core_bs["match_folder"] == src_core.resolve()

        # 4. Deeply nested subfolder
        deep_dir = checkout_dir / "nested" / "deep" / "leaf"
        deep_dir.mkdir(parents=True, exist_ok=True)
        res_deep = verify_assigned_repository(
            checkout_dir,
            {"url": remote_url, "branch": "main", "folder": "nested/deep/leaf"},
        )
        assert res_deep["match_folder"] == deep_dir.resolve()

    def test_verify_assigned_repository_fail_closed_nonexistent_or_file(
        self, offline_git_env: Dict[str, Any]
    ):
        """Non-existent directory or regular file as folder fails closed."""
        checkout_dir = offline_git_env["checkout_dir"]
        remote_url = offline_git_env["remote_url"]

        # Non-existent
        with pytest.raises(RuntimeError) as exc_nonexistent:
            verify_assigned_repository(
                checkout_dir,
                {"url": remote_url, "branch": "main", "folder": "missing_folder_404"},
            )
        assert "does not exist" in str(exc_nonexistent.value)

        # Regular file
        file_path = checkout_dir / "repo" / "README.md"
        assert file_path.is_file()
        with pytest.raises(RuntimeError) as exc_file:
            verify_assigned_repository(
                checkout_dir,
                {"url": remote_url, "branch": "main", "folder": "repo/README.md"},
            )
        assert "is not a directory" in str(exc_file.value)


# ==============================================================================
# Suite 2: R3 Workspace Path Resolution Stress Testing
# ==============================================================================

class TestR3WorkspacePathResolutionStress:
    """Adversarial stress-testing of workspace path resolution in MCPHelperServer."""

    def test_init_workspace_root_resolves_parent_case_insensitive(self, tmp_path: Path):
        """MCPHelperServer.__init__ trims 'team-sdlc-pack' suffix case-insensitively."""
        base_ws = tmp_path / "mock_workspace"
        base_ws.mkdir(parents=True, exist_ok=True)

        # 1. Lowercase team-sdlc-pack
        pack_lower = base_ws / "team-sdlc-pack"
        pack_lower.mkdir(parents=True, exist_ok=True)
        server_lower = MCPHelperServer(workspace_root=pack_lower)
        assert server_lower.workspace_root == base_ws.resolve()

        # 2. Uppercase TEAM-SDLC-PACK
        pack_upper = base_ws / "TEAM-SDLC-PACK"
        pack_upper.mkdir(parents=True, exist_ok=True)
        server_upper = MCPHelperServer(workspace_root=pack_upper)
        assert server_upper.workspace_root == base_ws.resolve()

        # 3. Mixed case Team-Sdlc-Pack
        pack_mixed = base_ws / "Team-Sdlc-Pack"
        pack_mixed.mkdir(parents=True, exist_ok=True)
        server_mixed = MCPHelperServer(workspace_root=pack_mixed)
        assert server_mixed.workspace_root == base_ws.resolve()

        # 4. Other folder name stays untouched
        other_dir = base_ws / "other-folder"
        other_dir.mkdir(parents=True, exist_ok=True)
        server_other = MCPHelperServer(workspace_root=other_dir)
        assert server_other.workspace_root == other_dir.resolve()

        # 5. String path inputs
        server_str = MCPHelperServer(workspace_root=str(pack_lower))
        assert server_str.workspace_root == base_ws.resolve()

        server_str_other = MCPHelperServer(workspace_root=str(other_dir))
        assert server_str_other.workspace_root == other_dir.resolve()

    def test_init_workspace_root_default_cwd(self, tmp_path: Path, monkeypatch):
        """When workspace_root is None, Path.cwd() ending in team-sdlc-pack resolves to parent."""
        base_ws = tmp_path / "cwd_workspace"
        pack_dir = base_ws / "team-sdlc-pack"
        pack_dir.mkdir(parents=True, exist_ok=True)

        # Simulate CWD = pack_dir
        monkeypatch.chdir(pack_dir)
        server = MCPHelperServer()
        assert server.workspace_root == base_ws.resolve()

        # Simulate CWD = base_ws (not ending in team-sdlc-pack)
        monkeypatch.chdir(base_ws)
        server2 = MCPHelperServer()
        assert server2.workspace_root == base_ws.resolve()

    def test_configure_session_team_workspace_updates(self, tmp_path: Path):
        """configure_session correctly updates self.workspace_root and trims team-sdlc-pack."""
        init_ws = tmp_path / "initial_ws"
        init_ws.mkdir(parents=True, exist_ok=True)
        server = MCPHelperServer(workspace_root=init_ws)
        assert server.workspace_root == init_ws.resolve()

        # 1. Plain custom workspace
        custom_ws = tmp_path / "custom" / "ws"
        custom_ws.mkdir(parents=True, exist_ok=True)
        server.configure_session({
            "type": "START_GAME",
            "matchId": "M1",
            "teamWorkspace": str(custom_ws),
        })
        assert server.workspace_root == custom_ws.resolve()

        # 2. Custom workspace ending in team-sdlc-pack (lowercase)
        custom_pack_lower = custom_ws / "team-sdlc-pack"
        custom_pack_lower.mkdir(parents=True, exist_ok=True)
        server.configure_session({
            "type": "START_GAME",
            "matchId": "M1",
            "teamWorkspace": str(custom_pack_lower),
        })
        assert server.workspace_root == custom_ws.resolve()

        # 3. Custom workspace ending in TEAM-SDLC-PACK (uppercase)
        custom_pack_upper = custom_ws / "TEAM-SDLC-PACK"
        custom_pack_upper.mkdir(parents=True, exist_ok=True)
        server.configure_session({
            "type": "START_GAME",
            "matchId": "M1",
            "teamWorkspace": str(custom_pack_upper),
        })
        assert server.workspace_root == custom_ws.resolve()

        # 4. Fallback snake_case 'team_workspace'
        alt_ws = tmp_path / "alt_ws"
        alt_ws.mkdir(parents=True, exist_ok=True)
        server.configure_session({
            "type": "START_GAME",
            "matchId": "M1",
            "team_workspace": str(alt_ws),
        })
        assert server.workspace_root == alt_ws.resolve()

        # 5. Fallback 'teamSdlcPackWorkspace' ending in team-sdlc-pack
        pack_ws = tmp_path / "pack_ws"
        pack_ws_inner = pack_ws / "team-sdlc-pack"
        pack_ws_inner.mkdir(parents=True, exist_ok=True)
        server.configure_session({
            "type": "START_GAME",
            "matchId": "M1",
            "teamSdlcPackWorkspace": str(pack_ws_inner),
        })
        assert server.workspace_root == pack_ws.resolve()

    def test_journal_db_path_construction_without_duplicate(self, tmp_path: Path):
        """Journal DB is constructed at <workspace_root>/.mtm/submissions.db without duplicated team-sdlc-pack."""
        base_ws = tmp_path / "tournament_ws"
        pack_dir = base_ws / "team-sdlc-pack"
        pack_dir.mkdir(parents=True, exist_ok=True)

        # Server initialized with team-sdlc-pack path
        server = MCPHelperServer(workspace_root=pack_dir)
        server.configure_session({
            "type": "START_GAME",
            "matchId": "MATCH_JOURNAL_TEST",
            "matchUuid": "uuid-j-01",
        })

        expected_db = base_ws.resolve() / ".mtm" / "submissions.db"
        assert server.journal is not None
        assert Path(server.journal.db_path).resolve() == expected_db
        assert str(server.journal.db_path).count("team-sdlc-pack") == 0
        assert expected_db.parent.exists()

    def test_knowledge_output_path_construction_without_duplicate(
        self, offline_git_env: Dict[str, Any], tmp_path: Path
    ):
        """PREPARE_GAME generates PROJECT_MAP.md at <workspace_root>/team-sdlc-pack/state/<matchCode>/knowledge/."""
        base_ws = tmp_path / "tourney_workspace"
        pack_dir = base_ws / "team-sdlc-pack"
        pack_dir.mkdir(parents=True, exist_ok=True)

        checkout_dir = offline_git_env["checkout_dir"]
        remote_url = offline_git_env["remote_url"]
        match_code = "MATCH_KB_TEST"

        server = MCPHelperServer(workspace_root=pack_dir)
        assert server.workspace_root == base_ws.resolve()

        descriptor = {
            "type": "PREPARE_GAME",
            "matchId": match_code,
            "matchUuid": "uuid-kb-test",
            "repository": {
                "url": remote_url,
                "branch": "main",
                "folder": "repo",
            },
            "checkoutRoot": str(checkout_dir),
            "matchRoot": str(checkout_dir / "repo"),
            "offline": True,
        }

        # Run configure_session (PREPARE_GAME phase)
        server.configure_session(descriptor)

        # Verify expected path
        expected_kb_path = (
            base_ws.resolve()
            / "team-sdlc-pack"
            / "state"
            / match_code
            / "knowledge"
            / "PROJECT_MAP.md"
        )
        assert expected_kb_path.exists(), f"PROJECT_MAP.md was not created at {expected_kb_path}"

        # Verify no duplicate team-sdlc-pack in the actual file system
        kb_path_str = str(expected_kb_path)
        assert kb_path_str.count("team-sdlc-pack") == 1

        duplicate_nested_path = (
            base_ws.resolve()
            / "team-sdlc-pack"
            / "team-sdlc-pack"
            / "state"
            / match_code
            / "knowledge"
            / "PROJECT_MAP.md"
        )
        assert not duplicate_nested_path.exists(), "Found double-nested team-sdlc-pack knowledge file!"
