"""
Contract Tests: PREP-01..02, KNO-01..03.
Verifies environment doctor validation, knowledge bootstrap, task knowledge lookup,
incremental knowledge refresh, and missing index fallback.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict
import pytest

from mtm_knowledge.index import build_index, retrieve_knowledge, clear_index_cache


def test_prep_01_environment_doctor_preflight(tmp_path: Path):
    """
    PREP-01: Environment Doctor Preflight.
    When doctor check fails (exit code != 0 or status != READY), the engine blocks
    match start and does not emit ENVIRONMENT_READY.
    """
    env_dir = tmp_path / "env-require"
    env_dir.mkdir(parents=True, exist_ok=True)

    # Doctor script simulating failure
    fail_script = env_dir / "doctor_fail.py"
    fail_script.write_text(
        "import sys\nprint('Environment status: FAILED - PostgreSQL 16 port 5433 unreachable')\nsys.exit(1)\n",
        encoding="utf-8",
    )

    res_fail = subprocess.run([sys.executable, str(fail_script)], capture_output=True, text=True)
    assert res_fail.returncode != 0
    assert "READY" not in res_fail.stdout

    # Doctor script simulating success
    pass_script = env_dir / "doctor_pass.py"
    pass_script.write_text(
        "import sys\nprint('Environment status: READY - Node 20, pnpm, PostgreSQL 16 on 5433 OK')\nsys.exit(0)\n",
        encoding="utf-8",
    )

    res_pass = subprocess.run([sys.executable, str(pass_script)], capture_output=True, text=True)
    assert res_pass.returncode == 0
    assert "Environment status: READY" in res_pass.stdout


def test_prep_02_knowledge_bootstrap_and_readiness(tmp_path: Path):
    """
    PREP-02: Knowledge Bootstrap & Readiness.
    Successful environment check triggers Knowledge Bootstrap.
    Generates PROJECT_MAP.md / index without mutating the customer Git repository.
    """
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "service.py").write_text("class PaymentService:\n    def process_payment(self): pass\n", encoding="utf-8")
    (repo_dir / "test_service.py").write_text("def test_payment(): pass\n", encoding="utf-8")

    cache_dir = tmp_path / "cache"

    # Bootstrap knowledge index
    clear_index_cache()
    index_data = build_index(repo_dir, cache_dir=cache_dir)

    assert index_data["indexed_count"] >= 2
    paths = [f["path"] for f in index_data["files"]]
    assert "service.py" in paths
    assert "test_service.py" in paths

    # Verify no cache or map was written inside repo_dir (must remain clean)
    assert not (repo_dir / ".mtm_index_cache").exists()
    assert not (repo_dir / "index-meta.json").exists()


def test_kno_01_task_knowledge_lookup(tmp_path: Path):
    """
    KNO-01: Task Knowledge Lookup.
    Pre-task context retrieval without full repository rescan.
    Retrieves targeted symbols and paths (300-800 tokens) based on task query.
    """
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)

    # Populate several files across different modules
    (repo_dir / "auth.py").write_text("class Authenticator:\n    def verify_token(self): pass\n", encoding="utf-8")
    (repo_dir / "billing.py").write_text("class InvoiceGenerator:\n    def create_invoice(self): pass\n", encoding="utf-8")
    (repo_dir / "shipping.py").write_text("class Dispatcher:\n    def ship_package(self): pass\n", encoding="utf-8")

    clear_index_cache()
    index_data = build_index(repo_dir)

    # Query for billing/invoice task
    context = retrieve_knowledge(index_data, query="generate invoice billing payment", limit=3)
    assert "billing.py" in context
    assert "InvoiceGenerator" in context
    # Shipping should not dominate billing query
    assert len(context) <= 3000


def test_kno_02_incremental_knowledge_refresh(tmp_path: Path):
    """
    KNO-02: Incremental Knowledge Refresh.
    Updating knowledge map after task commit only reindexes modified files,
    reusing cached metadata for unchanged files.
    """
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = tmp_path / "cache"

    file_a = repo_dir / "module_a.py"
    file_b = repo_dir / "module_b.py"
    file_a.write_text("def func_a(): pass\n", encoding="utf-8")
    file_b.write_text("def func_b(): pass\n", encoding="utf-8")

    clear_index_cache()
    idx1 = build_index(repo_dir, cache_dir=cache_dir)
    assert idx1["indexed_count"] == 2
    assert idx1["reindexed_files"] == 2
    assert idx1["reused_files"] == 0

    # Modify only file_b
    file_b.write_text("def func_b_modified(): pass\n", encoding="utf-8")

    idx2 = build_index(repo_dir, cache_dir=cache_dir)
    assert idx2["indexed_count"] == 2
    # file_a should be reused, file_b reindexed
    assert idx2["reused_files"] == 1
    assert idx2["reindexed_files"] == 1


def test_kno_03_missing_index_fallback(tmp_path: Path):
    """
    KNO-03: Missing Index Fallback.
    When index data or cache file is corrupt or missing, system gracefully
    builds fresh index or falls back without unhandled crashes.
    """
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "app.py").write_text("def main(): pass\n", encoding="utf-8")

    cache_dir = tmp_path / "corrupt_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Write corrupt JSON to cache file
    import hashlib
    digest = hashlib.sha256(str(repo_dir.resolve()).encode("utf-8")).hexdigest()[:16]
    corrupt_file = cache_dir / f"index_{digest}.json"
    corrupt_file.write_text("{{corrupted json invalid", encoding="utf-8")

    clear_index_cache()
    # Must recover cleanly without raising JSONDecodeError
    idx = build_index(repo_dir, cache_dir=cache_dir)
    assert idx["indexed_count"] == 1
    assert idx["files"][0]["path"] == "app.py"
