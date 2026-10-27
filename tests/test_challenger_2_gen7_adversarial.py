"""
Adversarial Stress Test Suite for Requirements R4, R5, and R6 (Challenger 2 - Gen 7).

Scope:
1. R4: Distribution checker layout & parity stress:
   - Mismatched fields between <TeamWorkspace>/.mtm/ and team-sdlc-pack/ manifests.
   - Missing .mtm/ manifests.
   - Missing or altered "files" array in root package.json.
   - Disambiguated --team-workspace and --pack-root path permutations.
2. R5: Submission journal concurrency & classification stress:
   - Concurrent 409 ALREADY_SUBMITTED responses: in-flight lock strictly preserved,
     blocking new client submission IDs or altered payloads.
   - Safe REJECTED state transition on 422 / 409 IDEMPOTENCY_CONFLICT unlocking payload for correction.
3. R6: Doctor fail-closed verification stress:
   - Doctor exits 0 but outputs non-matching text (no "Environment status: READY") -> must fail closed.
   - Doctor outputs "Environment status: READY" but exits with code 1 -> must fail closed.
   - Missing doctor script and missing build command -> must fail closed.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple
from unittest.mock import MagicMock, patch

import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from mtm_competition.tasks.journal import SubmissionJournal
from mtm_knowledge.bootstrap import KnowledgeBootstrap
from runner.mcp_server import MCPHelperServer

CHECK_DIST_SCRIPT = ROOT_DIR / "scripts" / "check-distribution.py"


@pytest.fixture
def base_server(tmp_path: Path) -> MCPHelperServer:
    """Isolated MCPHelperServer instance for testing."""
    return MCPHelperServer(workspace_root=tmp_path)


# ==============================================================================
# Helper for R4 Valid Distribution Environment
# ==============================================================================

def setup_base_distribution(tmp_path: Path, separate_pack: bool = False) -> Tuple[Path, Path]:
    """Create a fully compliant base distribution structure.
    
    If separate_pack is False: pack_dir is ws_dir / "team-sdlc-pack".
    If separate_pack is True: pack_dir is tmp_path / "custom_pack_root" (completely separate).
    """
    ws_dir = tmp_path / "workspace"
    ws_dir.mkdir(parents=True, exist_ok=True)
    mtm_dir = ws_dir / ".mtm"
    mtm_dir.mkdir(parents=True, exist_ok=True)

    if separate_pack:
        pack_dir = tmp_path / "custom_pack_root"
    else:
        pack_dir = ws_dir / "team-sdlc-pack"
    pack_dir.mkdir(parents=True, exist_ok=True)

    runtime_data = {
        "schemaVersion": 1,
        "kind": "mtm-team-runtime",
        "name": "team-sdlc-pack",
        "version": "1.0.0",
        "protocolVersion": 4,
        "provider": "codex",
        "entrypoint": {
            "rootAgentType": "team_orchestrator",
            "script": "runner/mcp_server.py",
        },
    }

    info_data = {
        "schemaVersion": 1,
        "version": "1.0.0",
        "teamId": "T01",
        "teamName": "Team MTM",
    }

    pkg_data = {
        "name": "team-sdlc-pack",
        "version": "1.0.0",
        "files": [
            "agents/",
            "bin/",
            "config/",
            "contracts/",
            "docs/",
            "install.ps1",
            "scripts/check-distribution.mjs",
            "scripts/install.mjs",
            "skills/",
            "src/",
            "README.md",
            "team-pack-info.json",
            "team-runtime.json",
        ],
    }

    # Write manifests
    (pack_dir / "team-runtime.json").write_text(json.dumps(runtime_data, indent=2), encoding="utf-8")
    (pack_dir / "team-pack-info.json").write_text(json.dumps(info_data, indent=2), encoding="utf-8")
    (mtm_dir / "team-runtime.json").write_text(json.dumps(runtime_data, indent=2), encoding="utf-8")
    (mtm_dir / "team-pack-info.json").write_text(json.dumps(info_data, indent=2), encoding="utf-8")
    (ws_dir / "package.json").write_text(json.dumps(pkg_data, indent=2), encoding="utf-8")

    # Profiles
    agents_dir = pack_dir / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    for prof in ["team_orchestrator", "team_repo_scout", "team_feature_worker", "team_quality_checker"]:
        (agents_dir / f"{prof}.toml").write_text(f'name = "{prof}"\nmodel = "default"\n', encoding="utf-8")

    # Skills
    skill_dir = pack_dir / "skills" / "team-orchestrator"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "# Team Orchestrator Skill\n\nThis is a valid distribution skill file for team orchestrator.\n",
        encoding="utf-8",
    )

    return ws_dir, pack_dir


def run_check_distribution(ws_dir: Path, pack_dir: Path | None = None) -> subprocess.CompletedProcess:
    """Invoke scripts/check-distribution.py via subprocess."""
    cmd = [sys.executable, str(CHECK_DIST_SCRIPT), "--team-workspace", str(ws_dir)]
    if pack_dir is not None:
        cmd.extend(["--pack-root", str(pack_dir)])
    return subprocess.run(cmd, capture_output=True, text=True)


# ==============================================================================
# Suite R4: Distribution Checker Layout & Parity Adversarial Stress
# ==============================================================================

class TestChallengerR4DistributionCheckerAdversarial:
    """Adversarial stress testing of R4: distribution checker layout and manifest parity."""

    def test_r4_baseline_passes_cleanly(self, tmp_path: Path):
        """Sanity baseline: compliant workspace & pack pass with exit code 0 and READY."""
        ws_dir, pack_dir = setup_base_distribution(tmp_path)
        res = run_check_distribution(ws_dir, pack_dir)
        assert res.returncode == 0
        assert "ALL_VERIFIED" in res.stdout
        assert "READY" in res.stdout

    @pytest.mark.parametrize(
        "field,mutated_value",
        [
            ("schemaVersion", 2),
            ("kind", "wrong-kind"),
            ("name", "wrong-pack-name"),
            ("version", "2.0.0"),
            ("protocolVersion", 5),
            ("provider", "other-provider"),
            ("entrypoint", {"rootAgentType": "wrong_agent"}),
        ],
    )
    def test_r4_mtm_runtime_manifest_field_mismatches_fail_closed(
        self, tmp_path: Path, field: str, mutated_value: Any
    ):
        """Adversarial: Mutating ANY single field in <TeamWorkspace>/.mtm/team-runtime.json
        causes check-distribution.py to fail closed with non-zero exit code.
        """
        ws_dir, pack_dir = setup_base_distribution(tmp_path)
        mtm_file = ws_dir / ".mtm" / "team-runtime.json"
        data = json.loads(mtm_file.read_text(encoding="utf-8"))
        data[field] = mutated_value
        mtm_file.write_text(json.dumps(data), encoding="utf-8")

        res = run_check_distribution(ws_dir, pack_dir)
        assert res.returncode != 0
        assert "DISTRIBUTION_CHECK_FAILED" in res.stderr
        assert field.lower() in res.stderr.lower() or "entrypoint" in res.stderr.lower()

    @pytest.mark.parametrize(
        "field,mutated_value",
        [
            ("schemaVersion", 2),
            ("version", "2.0.0"),
            ("teamId", "DIFFERENT_TEAM_ID"),
            ("teamName", "Different Team Name"),
        ],
    )
    def test_r4_mtm_pack_info_manifest_field_mismatches_fail_closed(
        self, tmp_path: Path, field: str, mutated_value: Any
    ):
        """Adversarial: Mutating ANY single field in <TeamWorkspace>/.mtm/team-pack-info.json
        causes check-distribution.py to fail closed.
        """
        ws_dir, pack_dir = setup_base_distribution(tmp_path)
        mtm_info_file = ws_dir / ".mtm" / "team-pack-info.json"
        data = json.loads(mtm_info_file.read_text(encoding="utf-8"))
        data[field] = mutated_value
        mtm_info_file.write_text(json.dumps(data), encoding="utf-8")

        res = run_check_distribution(ws_dir, pack_dir)
        assert res.returncode != 0
        assert "DISTRIBUTION_CHECK_FAILED" in res.stderr
        assert field.lower() in res.stderr.lower()

    def test_r4_pack_info_extra_or_missing_keys_fail_closed(self, tmp_path: Path):
        """Adversarial: team-pack-info.json must contain EXACTLY the 4 required keys."""
        ws_dir, pack_dir = setup_base_distribution(tmp_path)
        info_file = pack_dir / "team-pack-info.json"
        data = json.loads(info_file.read_text(encoding="utf-8"))
        data["unauthorizedExtraKey"] = "malicious"
        info_file.write_text(json.dumps(data), encoding="utf-8")

        res = run_check_distribution(ws_dir, pack_dir)
        assert res.returncode != 0
        assert "DISTRIBUTION_CHECK_FAILED" in res.stderr
        assert "exactly 4 keys" in res.stderr

    @pytest.mark.parametrize(
        "target_rel,content",
        [
            (".mtm/team-runtime.json", "{ malformed json :::"),
            (".mtm/team-runtime.json", ""),  # empty file
            (".mtm/team-pack-info.json", "{ invalid json"),
            (".mtm/team-pack-info.json", ""),  # empty file
            ("package.json", "NOT_JSON"),
        ],
    )
    def test_r4_corrupted_json_in_manifests_fails_closed(
        self, tmp_path: Path, target_rel: str, content: str
    ):
        """Adversarial: Corrupted or truncated JSON in any manifest fails closed."""
        ws_dir, pack_dir = setup_base_distribution(tmp_path)
        target = ws_dir / target_rel
        target.write_text(content, encoding="utf-8")

        res = run_check_distribution(ws_dir, pack_dir)
        assert res.returncode != 0
        assert "DISTRIBUTION_CHECK_FAILED" in res.stderr

    def test_r4_missing_entire_mtm_directory_fails_closed(self, tmp_path: Path):
        """Adversarial: Missing .mtm/ directory completely fails closed."""
        ws_dir, pack_dir = setup_base_distribution(tmp_path)
        import shutil
        shutil.rmtree(ws_dir / ".mtm")

        res = run_check_distribution(ws_dir, pack_dir)
        assert res.returncode != 0
        assert "DISTRIBUTION_CHECK_FAILED" in res.stderr
        assert "team-runtime.json" in res.stderr

    def test_r4_missing_mtm_pack_info_fails_closed(self, tmp_path: Path):
        """Adversarial: .mtm/team-runtime.json present but .mtm/team-pack-info.json deleted fails closed."""
        ws_dir, pack_dir = setup_base_distribution(tmp_path)
        (ws_dir / ".mtm" / "team-pack-info.json").unlink()

        res = run_check_distribution(ws_dir, pack_dir)
        assert res.returncode != 0
        assert "DISTRIBUTION_CHECK_FAILED" in res.stderr
        assert "team-pack-info.json" in res.stderr

    def test_r4_missing_root_package_json_fails_closed(self, tmp_path: Path):
        """Adversarial: Missing <TeamWorkspace>/package.json fails closed."""
        ws_dir, pack_dir = setup_base_distribution(tmp_path)
        (ws_dir / "package.json").unlink()

        res = run_check_distribution(ws_dir, pack_dir)
        assert res.returncode != 0
        assert "DISTRIBUTION_CHECK_FAILED" in res.stderr
        assert "package.json" in res.stderr

    def test_r4_root_package_json_missing_files_array_fails_closed(self, tmp_path: Path):
        """Adversarial: Root package.json omitting 'files' array fails closed."""
        ws_dir, pack_dir = setup_base_distribution(tmp_path)
        pkg_file = ws_dir / "package.json"
        pkg_data = json.loads(pkg_file.read_text(encoding="utf-8"))
        del pkg_data["files"]
        pkg_file.write_text(json.dumps(pkg_data), encoding="utf-8")

        res = run_check_distribution(ws_dir, pack_dir)
        assert res.returncode != 0
        assert "DISTRIBUTION_CHECK_FAILED" in res.stderr
        assert "must contain a 'files' array" in res.stderr

    @pytest.mark.parametrize("invalid_files", ["all", {"key": "val"}, 12345, True])
    def test_r4_root_package_json_files_not_a_list_fails_closed(
        self, tmp_path: Path, invalid_files: Any
    ):
        """Adversarial: Root package.json where 'files' is non-list fails closed."""
        ws_dir, pack_dir = setup_base_distribution(tmp_path)
        pkg_file = ws_dir / "package.json"
        pkg_data = json.loads(pkg_file.read_text(encoding="utf-8"))
        pkg_data["files"] = invalid_files
        pkg_file.write_text(json.dumps(pkg_data), encoding="utf-8")

        res = run_check_distribution(ws_dir, pack_dir)
        assert res.returncode != 0
        assert "must contain a 'files' array" in res.stderr

    def test_r4_root_package_json_files_array_missing_mandatory_entry_fails_closed(
        self, tmp_path: Path
    ):
        """Adversarial: Omitting 'team-runtime.json' from 'files' fails closed."""
        ws_dir, pack_dir = setup_base_distribution(tmp_path)
        pkg_file = ws_dir / "package.json"
        pkg_data = json.loads(pkg_file.read_text(encoding="utf-8"))
        pkg_data["files"].remove("team-runtime.json")
        pkg_file.write_text(json.dumps(pkg_data), encoding="utf-8")

        res = run_check_distribution(ws_dir, pack_dir)
        assert res.returncode != 0
        assert "DISTRIBUTION_CHECK_FAILED" in res.stderr
        assert "files" in res.stderr.lower()

    def test_r4_root_package_json_files_array_extra_unauthorized_entry_fails_closed(
        self, tmp_path: Path
    ):
        """Adversarial: Adding unauthorized entry to 'files' fails closed."""
        ws_dir, pack_dir = setup_base_distribution(tmp_path)
        pkg_file = ws_dir / "package.json"
        pkg_data = json.loads(pkg_file.read_text(encoding="utf-8"))
        pkg_data["files"].append("unauthorized_extra_folder/")
        pkg_file.write_text(json.dumps(pkg_data), encoding="utf-8")

        res = run_check_distribution(ws_dir, pack_dir)
        assert res.returncode != 0
        assert "DISTRIBUTION_CHECK_FAILED" in res.stderr
        assert "files" in res.stderr.lower()

    def test_r4_root_package_json_files_array_wrong_ordering_fails_closed(self, tmp_path: Path):
        """Adversarial: Reversing 'files' array violates strict exact parity and fails closed."""
        ws_dir, pack_dir = setup_base_distribution(tmp_path)
        pkg_file = ws_dir / "package.json"
        pkg_data = json.loads(pkg_file.read_text(encoding="utf-8"))
        pkg_data["files"].reverse()
        pkg_file.write_text(json.dumps(pkg_data), encoding="utf-8")

        res = run_check_distribution(ws_dir, pack_dir)
        assert res.returncode != 0
        assert "DISTRIBUTION_CHECK_FAILED" in res.stderr
        assert "files" in res.stderr.lower()

    def test_r4_root_package_json_version_mismatch_fails_closed(self, tmp_path: Path):
        """Adversarial: package.json version diverging from team-runtime.json fails closed."""
        ws_dir, pack_dir = setup_base_distribution(tmp_path)
        pkg_file = ws_dir / "package.json"
        pkg_data = json.loads(pkg_file.read_text(encoding="utf-8"))
        pkg_data["version"] = "9.9.9"
        pkg_file.write_text(json.dumps(pkg_data), encoding="utf-8")

        res = run_check_distribution(ws_dir, pack_dir)
        assert res.returncode != 0
        assert "version" in res.stderr.lower()

    def test_r4_disambiguated_separate_pack_root_passes_when_valid(self, tmp_path: Path):
        """Path permutation: --team-workspace and --pack-root point to separate disjoint paths."""
        ws_dir, pack_dir = setup_base_distribution(tmp_path, separate_pack=True)
        # Verify pack is NOT inside ws_dir
        assert pack_dir.parent == tmp_path
        assert not (ws_dir / "team-sdlc-pack").exists()

        res = run_check_distribution(ws_dir, pack_dir)
        assert res.returncode == 0
        assert "ALL_VERIFIED" in res.stdout
        assert "READY" in res.stdout

    def test_r4_disambiguated_nonexistent_workspace_fails_closed(self, tmp_path: Path):
        """Path permutation: non-existent --team-workspace path fails closed."""
        non_existent_ws = tmp_path / "does_not_exist_ws"
        _, pack_dir = setup_base_distribution(tmp_path, separate_pack=True)

        res = run_check_distribution(non_existent_ws, pack_dir)
        assert res.returncode != 0
        assert "DISTRIBUTION_CHECK_FAILED" in res.stderr

    def test_r4_disambiguated_nonexistent_pack_root_fails_closed(self, tmp_path: Path):
        """Path permutation: non-existent --pack-root path fails closed."""
        ws_dir, _ = setup_base_distribution(tmp_path, separate_pack=True)
        non_existent_pack = tmp_path / "does_not_exist_pack"

        res = run_check_distribution(ws_dir, non_existent_pack)
        assert res.returncode != 0
        assert "DISTRIBUTION_CHECK_FAILED" in res.stderr
        assert "team-runtime.json" in res.stderr


# ==============================================================================
# Suite R5: Submission Journal Concurrency & Classification Stress
# ==============================================================================

class TestChallengerR5SubmissionJournalAdversarial:
    """Adversarial concurrency & error code classification stress on SubmissionJournal."""

    def test_r5_concurrent_409_already_submitted_threads_retains_lock(self, tmp_path: Path):
        """Concurrency stress: 20 simultaneous threads recording 409 ALREADY_SUBMITTED.
        
        Verifies:
        - SQLite WAL mode handles concurrent write contention cleanly without DatabaseLocked error.
        - The entry state settles deterministically to 'ALREADY_SUBMITTED'.
        - The in-flight lock is retained (get_in_flight returns the entry).
        - Attempting to record a new client_submission_id is blocked with ValueError.
        """
        db_path = tmp_path / "concurrent_journal.db"
        match_uuid = str(uuid.uuid4())
        item_key = "CONCURRENT-TASK-409"
        sub_id = str(uuid.uuid4())
        idemp_key = "idemp-concurrent-01"
        body = b'{"concurrent": "body"}'

        # Seed initial in-flight entry
        with SubmissionJournal(db_path=db_path) as j:
            j.record_in_flight(
                match_uuid=match_uuid,
                backlog_item_key=item_key,
                client_submission_id=sub_id,
                idempotency_key=idemp_key,
                request_body_bytes=body,
                git_revision="a" * 40,
            )

        errors: List[Exception] = []

        def worker_record_already_submitted(worker_idx: int) -> None:
            try:
                # Open isolated connection per worker on the same SQLite WAL file
                with SubmissionJournal(db_path=db_path) as worker_j:
                    worker_j.record_already_submitted(
                        match_uuid=match_uuid,
                        backlog_item_key=item_key,
                        http_code=409,
                        error_code="ALREADY_SUBMITTED",
                        error_message=f"Conflict from worker {worker_idx}",
                    )
            except Exception as exc:
                errors.append(exc)

        # Launch 20 concurrent threads hammering record_already_submitted
        threads = [threading.Thread(target=worker_record_already_submitted, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Zero SQLite locking crashes
        assert len(errors) == 0, f"Thread errors encountered: {errors}"

        # Invariant checks
        with SubmissionJournal(db_path=db_path) as j:
            entry = j.get_entry(match_uuid, item_key)
            assert entry is not None
            assert entry["state"] == "ALREADY_SUBMITTED"
            assert entry["last_http_code"] == 409
            assert entry["last_error_code"] == "ALREADY_SUBMITTED"

            # In-flight lock must be strictly preserved
            in_flight = j.get_in_flight(match_uuid, item_key)
            assert in_flight is not None
            assert in_flight["state"] == "ALREADY_SUBMITTED"

            # Attempting to record a NEW client submission ID is BLOCKED
            new_sub_id = str(uuid.uuid4())
            with pytest.raises(ValueError, match="already submitted and locked on server"):
                j.record_in_flight(
                    match_uuid=match_uuid,
                    backlog_item_key=item_key,
                    client_submission_id=new_sub_id,
                    idempotency_key="new-key",
                    request_body_bytes=body,
                    git_revision="b" * 40,
                )

            # Attempting to record altered body bytes is BLOCKED
            with pytest.raises(ValueError, match="already submitted and locked on server"):
                j.record_in_flight(
                    match_uuid=match_uuid,
                    backlog_item_key=item_key,
                    client_submission_id=sub_id,
                    idempotency_key=idemp_key,
                    request_body_bytes=b'{"altered": "payload"}',
                    git_revision="b" * 40,
                )

    def test_r5_already_submitted_strictly_blocks_rejection_transition(self, tmp_path: Path):
        """Adversarial: Once in ALREADY_SUBMITTED, record_rejection CANNOT transition it to REJECTED."""
        db_path = tmp_path / "journal_no_downgrade.db"
        match_uuid = str(uuid.uuid4())
        item_key = "NO-DOWNGRADE-TASK"

        with SubmissionJournal(db_path=db_path) as j:
            j.record_in_flight(
                match_uuid=match_uuid,
                backlog_item_key=item_key,
                client_submission_id=str(uuid.uuid4()),
                idempotency_key="idemp-1",
                request_body_bytes=b'{"foo": "bar"}',
                git_revision="1" * 40,
            )
            j.record_already_submitted(match_uuid, item_key, 409, "ALREADY_SUBMITTED")

            # record_rejection with any error must fail
            with pytest.raises(ValueError, match="Cannot transition .* to REJECTED: entry is in ALREADY_SUBMITTED lock"):
                j.record_rejection(match_uuid, item_key, http_code=400, error_code="BAD_REQUEST")

            with pytest.raises(ValueError, match="Cannot transition .* to REJECTED: entry is in ALREADY_SUBMITTED lock"):
                j.record_rejection(match_uuid, item_key, http_code=422, error_code="VALIDATION_ERROR")

            with pytest.raises(ValueError, match="Cannot transition .* on ALREADY_SUBMITTED"):
                j.record_rejection(match_uuid, item_key, http_code=409, error_code="ALREADY_SUBMITTED")

            # Entry state must remain ALREADY_SUBMITTED
            entry = j.get_entry(match_uuid, item_key)
            assert entry["state"] == "ALREADY_SUBMITTED"

    @pytest.mark.parametrize(
        "http_code,error_code",
        [
            (422, "VALIDATION_ERROR"),
            (409, "IDEMPOTENCY_CONFLICT"),
            (400, "BAD_REQUEST"),
        ],
    )
    def test_r5_safe_rejection_unlocks_payload_for_correction_and_resubmission(
        self, tmp_path: Path, http_code: int, error_code: str
    ):
        """Requirement R5: Safe REJECTED state transition on 422 / 409 IDEMPOTENCY_CONFLICT / 400.
        
        Verifies:
        - Rejection safely transitions state to 'REJECTED'.
        - verify_or_abort permits payload modification while REJECTED.
        - record_in_flight allows registering a new client_submission_id and modified body bytes.
        - State returns to 'IN_FLIGHT'.
        """
        db_path = tmp_path / f"journal_reject_{http_code}.db"
        match_uuid = str(uuid.uuid4())
        item_key = f"TASK-{http_code}-RETRY"
        sub_id_1 = str(uuid.uuid4())
        body_v1 = b'{"answer": "rejected_payload"}'

        with SubmissionJournal(db_path=db_path) as j:
            # 1. First attempt IN_FLIGHT
            j.record_in_flight(
                match_uuid=match_uuid,
                backlog_item_key=item_key,
                client_submission_id=sub_id_1,
                idempotency_key=f"idemp-{sub_id_1}",
                request_body_bytes=body_v1,
                git_revision="a" * 40,
            )

            # 2. Server rejects deterministically
            j.record_rejection(
                match_uuid=match_uuid,
                backlog_item_key=item_key,
                http_code=http_code,
                error_code=error_code,
                error_message=f"Server returned {http_code} {error_code}",
            )

            entry = j.get_entry(match_uuid, item_key)
            assert entry["state"] == "REJECTED"
            assert entry["last_http_code"] == http_code
            assert entry["last_error_code"] == error_code

            # 3. Payload modification is unlocked: verify_or_abort returns True
            body_v2 = b'{"answer": "corrected_and_verified_payload"}'
            assert j.verify_or_abort(match_uuid, item_key, body_v2) is True

            # 4. Resubmission succeeds with new UUID and new payload
            sub_id_2 = str(uuid.uuid4())
            new_sha = j.record_in_flight(
                match_uuid=match_uuid,
                backlog_item_key=item_key,
                client_submission_id=sub_id_2,
                idempotency_key=f"idemp-{sub_id_2}",
                request_body_bytes=body_v2,
                git_revision="b" * 40,
            )

            resubmitted = j.get_entry(match_uuid, item_key)
            assert resubmitted["state"] == "IN_FLIGHT"
            assert resubmitted["client_submission_id"] == sub_id_2
            assert resubmitted["request_body_bytes"] == body_v2
            assert resubmitted["request_body_sha256"] == new_sha

    def test_r5_multi_item_concurrent_stress_mixed_outcomes(self, tmp_path: Path):
        """Stress: 10 parallel threads executing a mix of 409 ALREADY_SUBMITTED,
        422 REJECTED -> correction -> resubmit, and 201 ACCEPTED across 10 items.
        """
        db_path = tmp_path / "mixed_journal.db"
        match_uuid = str(uuid.uuid4())

        def run_item_lifecycle(idx: int) -> str:
            item_key = f"ITEM-{idx:02d}"
            sub_id = str(uuid.uuid4())
            body = f'{{"idx": {idx}}}'.encode("utf-8")

            with SubmissionJournal(db_path=db_path) as j:
                j.record_in_flight(
                    match_uuid=match_uuid,
                    backlog_item_key=item_key,
                    client_submission_id=sub_id,
                    idempotency_key=f"idemp-{idx}",
                    request_body_bytes=body,
                    git_revision="c" * 40,
                )

                if idx % 3 == 0:
                    # Case 0: 409 ALREADY_SUBMITTED
                    j.record_already_submitted(match_uuid, item_key, 409, "ALREADY_SUBMITTED")
                    return "ALREADY_SUBMITTED"
                elif idx % 3 == 1:
                    # Case 1: 422 REJECTED then corrected
                    j.record_rejection(match_uuid, item_key, 422, "VALIDATION_ERROR")
                    corrected_body = f'{{"idx": {idx}, "fixed": true}}'.encode("utf-8")
                    new_sub_id = str(uuid.uuid4())
                    j.record_in_flight(
                        match_uuid=match_uuid,
                        backlog_item_key=item_key,
                        client_submission_id=new_sub_id,
                        idempotency_key=f"idemp-fixed-{idx}",
                        request_body_bytes=corrected_body,
                        git_revision="d" * 40,
                    )
                    j.record_receipt(match_uuid, item_key, receipt_id=f"rec-{idx}", http_code=201)
                    return "ACCEPTED"
                else:
                    # Case 2: Clean 201 ACCEPTED
                    j.record_receipt(match_uuid, item_key, receipt_id=f"rec-{idx}", http_code=201)
                    return "ACCEPTED"

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(run_item_lifecycle, i) for i in range(15)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        assert len(results) == 15
        with SubmissionJournal(db_path=db_path) as j:
            for i in range(15):
                entry = j.get_entry(match_uuid, f"ITEM-{i:02d}")
                assert entry is not None
                if i % 3 == 0:
                    assert entry["state"] == "ALREADY_SUBMITTED"
                else:
                    assert entry["state"] == "ACCEPTED"


# ==============================================================================
# Suite R6: Doctor Fail-Closed Verification Stress
# ==============================================================================

class TestChallengerR6DoctorFailClosedAdversarial:
    """Adversarial stress testing of doctor fail-closed invariants in KnowledgeBootstrap & MCPHelperServer."""

    def test_r6_doctor_exit_0_missing_ready_string_in_bootstrap(self, tmp_path: Path):
        """Adversarial: Doctor script exits 0 but emits deceptive non-matching text
        (e.g. 'All checks passed', 'Status: OK', 'READY' without prefix).
        Must fail closed: passed is False, exit_code is 1.
        """
        repo_dir = tmp_path / "repo_exit0_no_ready"
        env_req = repo_dir / "env-require"
        env_req.mkdir(parents=True, exist_ok=True)
        (env_req / "doctor.ps1").write_text("Write-Output 'Environment status: OK - All ready'\nexit 0\n", encoding="utf-8")
        (env_req / "doctor.sh").write_text("#!/bin/sh\necho 'Environment status: OK - All ready'\nexit 0\n", encoding="utf-8")

        bootstrap = KnowledgeBootstrap(
            repo_path=repo_dir,
            match_code="DOC_R6_01",
            branch="main",
            folder=".",
            checkout_root=repo_dir,
            match_root=repo_dir,
            app_root=repo_dir,
        )

        res = bootstrap.verify_environment(fail_closed=True)
        assert res["passed"] is False
        assert res["exit_code"] != 0
        assert "Environment status: READY" not in res["output"]

        # generate_project_map with run_verification must reflect FAILED
        out_map = tmp_path / "state" / "PROJECT_MAP.md"
        bootstrap.generate_project_map(output_path=out_map, run_verification=True)
        content = out_map.read_text(encoding="utf-8")
        assert "Discovery status: `FAILED`" in content

    def test_r6_doctor_exit_0_missing_ready_string_in_mcp_server(
        self, base_server: MCPHelperServer, offline_git_env: Dict[str, Any], tmp_path: Path
    ):
        """Adversarial: MCPHelperServer configure_session(type='PREPARE_GAME') fails closed
        if doctor script exits 0 but lacks exact 'Environment status: READY'.
        """
        checkout_dir = offline_git_env["checkout_dir"]
        match_code = "DOC_MCP_01"
        repo_url = "https://github.com/example/repo.git"
        offline_git_env["set_remote_url"](repo_url)
        (checkout_dir / match_code).mkdir(parents=True, exist_ok=True)

        env_req = checkout_dir / "env-require"
        env_req.mkdir(parents=True, exist_ok=True)
        (env_req / "doctor.ps1").write_text("Write-Output 'Build succeeded 100%'\nexit 0\n", encoding="utf-8")
        (env_req / "doctor.sh").write_text("#!/bin/sh\necho 'Build succeeded 100%'\nexit 0\n", encoding="utf-8")

        log_file = tmp_path / "doctor_exit0_no_ready.ndjson"
        descriptor = {
            "type": "PREPARE_GAME",
            "matchId": match_code,
            "progressLogFile": str(log_file),
            "checkoutRoot": str(checkout_dir),
            "offline": True,
            "repository": {
                "url": repo_url,
                "branch": "main",
                "folder": match_code,
            },
        }

        with pytest.raises(RuntimeError, match="Environment verification failed"):
            base_server.configure_session(descriptor)

        # Check telemetry logs: BUILD stage must be FAILED, ENVIRONMENT_READY must NOT exist
        events = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        build_event = next((e for e in events if e.get("type") == "PREPARATION_STATUS" and e.get("stage") == "BUILD"), None)
        assert build_event is not None
        assert build_event["status"] == "FAILED"
        assert not any(e.get("type") == "ENVIRONMENT_READY" for e in events)

    def test_r6_doctor_outputs_ready_but_exits_code_1_in_bootstrap(self, tmp_path: Path):
        """Adversarial: Doctor outputs 'Environment status: READY' but exits with code 1.
        Must fail closed: passed is False, exit_code is 1.
        """
        repo_dir = tmp_path / "repo_exit1_with_ready"
        env_req = repo_dir / "env-require"
        env_req.mkdir(parents=True, exist_ok=True)
        (env_req / "doctor.ps1").write_text("Write-Output 'Environment status: READY'\nexit 1\n", encoding="utf-8")
        (env_req / "doctor.sh").write_text("#!/bin/sh\necho 'Environment status: READY'\nexit 1\n", encoding="utf-8")

        bootstrap = KnowledgeBootstrap(
            repo_path=repo_dir,
            match_code="DOC_R6_02",
            branch="main",
            folder=".",
            checkout_root=repo_dir,
            match_root=repo_dir,
            app_root=repo_dir,
        )

        res = bootstrap.verify_environment(fail_closed=True)
        assert res["passed"] is False
        assert res["exit_code"] != 0

    def test_r6_doctor_outputs_ready_but_exits_code_1_in_mcp_server(
        self, base_server: MCPHelperServer, offline_git_env: Dict[str, Any], tmp_path: Path
    ):
        """Adversarial: MCPHelperServer fails closed if doctor outputs READY but exits with code 1."""
        checkout_dir = offline_git_env["checkout_dir"]
        match_code = "DOC_MCP_02"
        repo_url = "https://github.com/example/repo.git"
        offline_git_env["set_remote_url"](repo_url)
        (checkout_dir / match_code).mkdir(parents=True, exist_ok=True)

        env_req = checkout_dir / "env-require"
        env_req.mkdir(parents=True, exist_ok=True)
        (env_req / "doctor.ps1").write_text("Write-Output 'Environment status: READY'\nexit 1\n", encoding="utf-8")
        (env_req / "doctor.sh").write_text("#!/bin/sh\necho 'Environment status: READY'\nexit 1\n", encoding="utf-8")

        log_file = tmp_path / "doctor_exit1_with_ready.ndjson"
        descriptor = {
            "type": "PREPARE_GAME",
            "matchId": match_code,
            "progressLogFile": str(log_file),
            "checkoutRoot": str(checkout_dir),
            "offline": True,
            "repository": {
                "url": repo_url,
                "branch": "main",
                "folder": match_code,
            },
        }

        with pytest.raises(RuntimeError, match="Environment verification failed"):
            base_server.configure_session(descriptor)

        events = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        build_event = next((e for e in events if e.get("type") == "PREPARATION_STATUS" and e.get("stage") == "BUILD"), None)
        assert build_event is not None
        assert build_event["status"] == "FAILED"
        assert not any(e.get("type") == "ENVIRONMENT_READY" for e in events)

    def test_r6_missing_doctor_and_missing_build_command_in_bootstrap(self, tmp_path: Path):
        """Adversarial: Repository with no doctor script and no build command fails closed in KnowledgeBootstrap."""
        repo_dir = tmp_path / "repo_no_script_no_build"
        repo_dir.mkdir(parents=True, exist_ok=True)
        # Create arbitrary non-build file
        (repo_dir / "data.csv").write_text("col1,col2\n1,2\n", encoding="utf-8")

        bootstrap = KnowledgeBootstrap(
            repo_path=repo_dir,
            match_code="DOC_R6_03",
            branch="main",
            folder=".",
            checkout_root=repo_dir,
            match_root=repo_dir,
            app_root=repo_dir,
        )

        res = bootstrap.verify_environment(fail_closed=True)
        assert res["passed"] is False
        assert res["exit_code"] != 0
        assert "No doctor script" in res["output"] or "No doctor" in res["output"]

    def test_r6_missing_doctor_and_missing_build_command_in_mcp_server(
        self, base_server: MCPHelperServer, offline_git_env: Dict[str, Any], tmp_path: Path
    ):
        """Adversarial: Repository with no doctor and no build command fails closed in MCPHelperServer."""
        checkout_dir = offline_git_env["checkout_dir"]
        match_code = "DOC_MCP_03"
        repo_url = "https://github.com/example/repo.git"
        offline_git_env["set_remote_url"](repo_url)
        (checkout_dir / match_code).mkdir(parents=True, exist_ok=True)

        # Unlink all doctor scripts
        for p in checkout_dir.rglob("doctor.*"):
            if p.is_file():
                p.unlink()

        log_file = tmp_path / "no_doctor_no_build.ndjson"
        descriptor = {
            "type": "PREPARE_GAME",
            "matchId": match_code,
            "progressLogFile": str(log_file),
            "checkoutRoot": str(checkout_dir),
            "offline": True,
            "repository": {
                "url": repo_url,
                "branch": "main",
                "folder": match_code,
            },
        }

        with pytest.raises(RuntimeError, match="No doctor script .* or valid build command found"):
            base_server.configure_session(descriptor)

        events = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        build_event = next((e for e in events if e.get("type") == "PREPARATION_STATUS" and e.get("stage") == "BUILD"), None)
        assert build_event is not None
        assert build_event["status"] == "FAILED"
        assert not any(e.get("type") == "ENVIRONMENT_READY" for e in events)

    def test_r6_doctor_valid_exit_0_and_ready_string_passes_cleanly(
        self, base_server: MCPHelperServer, offline_git_env: Dict[str, Any], tmp_path: Path
    ):
        """Positive Control: Doctor script exiting 0 AND outputting 'Environment status: READY' passes."""
        checkout_dir = offline_git_env["checkout_dir"]
        match_code = "DOC_PASS_01"
        repo_url = "https://github.com/example/repo.git"
        offline_git_env["set_remote_url"](repo_url)
        (checkout_dir / match_code).mkdir(parents=True, exist_ok=True)

        env_req = checkout_dir / "env-require"
        env_req.mkdir(parents=True, exist_ok=True)
        (env_req / "doctor.ps1").write_text("Write-Output 'Environment status: READY - System fully verified'\nexit 0\n", encoding="utf-8")
        (env_req / "doctor.sh").write_text("#!/bin/sh\necho 'Environment status: READY - System fully verified'\nexit 0\n", encoding="utf-8")

        log_file = tmp_path / "doctor_pass.ndjson"
        descriptor = {
            "type": "PREPARE_GAME",
            "matchId": match_code,
            "progressLogFile": str(log_file),
            "checkoutRoot": str(checkout_dir),
            "offline": True,
            "repository": {
                "url": repo_url,
                "branch": "main",
                "folder": match_code,
            },
        }

        # Passes cleanly without raising exception
        base_server.configure_session(descriptor)

        events = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        build_event = next((e for e in events if e.get("type") == "PREPARATION_STATUS" and e.get("stage") == "BUILD"), None)
        assert build_event is not None
        assert build_event["status"] == "COMPLETED"

        ready_event = next((e for e in events if e.get("type") == "ENVIRONMENT_READY"), None)
        assert ready_event is not None
        assert ready_event["requestId"] == f"prepare-{match_code}"
        assert ready_event["playerSessionId"]
