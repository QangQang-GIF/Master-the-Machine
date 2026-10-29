"""Secret-free DEV cycle state and ten-minute observer gate.

The supervisor diagnoses Engine failures between frozen matches. This ledger
contains metrics only, never task text, source patches, answers, or credentials.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from typing import Any


def classify_runtime_failure(snapshot: dict[str, Any], cli: dict[str, Any]) -> dict[str, Any]:
    """Classify terminal run failures, not expected pre-fix test/grep exits."""
    if cli.get("approvals") or cli.get("status") == "WAITING_APPROVAL":
        return {"runtimeFailed": False, "hostApprovalRequired": True, "category": "HOST_APPROVAL"}
    if cli.get("status") in ("FAILED", "INTERRUPTED"):
        return {"runtimeFailed": True, "hostApprovalRequired": False, "category": "CLI_FAILURE"}
    if any(row.get("status") in ("BLOCKED", "FAILED", "TIMED_OUT") and not row.get("receiptId")
           for row in snapshot.get("backlogs", [])):
        return {"runtimeFailed": True, "hostApprovalRequired": False, "category": "UNSUBMITTED_ITEM_FAILURE"}
    if any(row.get("status") in ("FAILED", "BLOCKED") for row in (snapshot.get("preparation") or {}).values()):
        return {"runtimeFailed": True, "hostApprovalRequired": False, "category": "PREPARATION_FAILURE"}
    return {"runtimeFailed": False, "hostApprovalRequired": False, "category": "NONE"}


class TrainingLoop:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS cycle (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS policy (id INTEGER PRIMARY KEY CHECK(id=1), abort_on_error INTEGER NOT NULL)")

    def _db(self):
        return sqlite3.connect(self.path, timeout=10)

    def authorize_abort_on_error(self, enabled: bool = True) -> None:
        """Call only after the human authorizes abandoning failed practice runs."""
        with self._db() as db:
            db.execute("INSERT INTO policy VALUES(1,?) ON CONFLICT(id) DO UPDATE SET abort_on_error=excluded.abort_on_error", (int(enabled),))

    def abort_on_error(self) -> bool:
        with self._db() as db:
            row = db.execute("SELECT abort_on_error FROM policy WHERE id=1").fetchone()
        return bool(row and row[0])

    def confirm_stopped_for_dev(self, cli_status: dict[str, Any]) -> dict[str, Any]:
        if not self.abort_on_error():
            raise ValueError("Human authorization to abandon failed rounds is required")
        if cli_status.get("status") != "STOPPED" or cli_status.get("pid"):
            raise ValueError("Player must confirm STOPPED with no live process before DEV changes")
        state = self.state()
        state["phase"], state["abandoned"] = "DEV_REVIEW_REQUIRED", True
        state["cliStatus"] = "STOPPED"
        with self._db() as db:
            db.execute("UPDATE cycle SET data=? WHERE id=1", (json.dumps(state),))
        return state

    def start_round(self, match_id: str, revision: str, now: float | None = None) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Z0-9]{8}", match_id) or not re.fullmatch(r"[0-9a-f]{7,40}", revision):
            raise ValueError("Expected public match ID and verified source revision")
        now = time.time() if now is None else now
        state = {"matchId": match_id, "revision": revision, "phase": "FROZEN_MATCH",
                 "officialStatus": "UNKNOWN", "nextObservation": now + 600,
                 "awardedPoints": None, "totalPoints": None, "targetReached": False,
                 "observations": 0}
        with self._db() as db:
            db.execute("INSERT INTO cycle VALUES(1,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (json.dumps(state),))
        return state

    def state(self) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute("SELECT data FROM cycle WHERE id=1").fetchone()
        return json.loads(row[0]) if row else {"phase": "DEV"}

    def reserve_observation(self, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM cycle WHERE id=1").fetchone()
            state = json.loads(row[0]) if row else None
            if not state or state["targetReached"] or now < state["nextObservation"]:
                return {"readAllowed": False, "state": state}
            state["nextObservation"] = now + 600
            state["observations"] += 1
            state["lastObservation"] = now
            db.execute("UPDATE cycle SET data=? WHERE id=1", (json.dumps(state),))
        return {"readAllowed": True, "state": state}

    def record(self, observation: dict[str, Any]) -> dict[str, Any]:
        allowed = {"matchId", "officialStatus", "cliStatus", "preparationFailed", "hostApprovalRequired", "runtimeFailed",
                   "awardedPoints", "totalPoints", "scoreSource"}
        if not isinstance(observation, dict) or set(observation) - allowed:
            raise ValueError("Only scalar public status/score metrics may enter the cycle ledger")
        for key in ("officialStatus", "cliStatus"):
            if key in observation and (not isinstance(observation[key], str) or not re.fullmatch(r"[A-Z_]{1,64}", observation[key])):
                raise ValueError("Status must be a compact public enum")
        for key in ("preparationFailed", "hostApprovalRequired", "runtimeFailed"):
            if key in observation and not isinstance(observation[key], bool):
                raise ValueError("Flags must be booleans")
        state = self.state()
        if observation.get("matchId") != state.get("matchId"):
            raise ValueError("Observation belongs to another match")
        status = observation.get("officialStatus", "UNKNOWN")
        state.update({k: observation[k] for k in ("officialStatus", "cliStatus") if k in observation})
        if observation.get("scoreSource") == "GAME_MASTER":
            score, maximum = observation.get("awardedPoints"), observation.get("totalPoints")
            if (isinstance(score, (int, float)) and not isinstance(score, bool)
                    and isinstance(maximum, (int, float)) and not isinstance(maximum, bool)
                    and math.isfinite(score) and math.isfinite(maximum)
                    and maximum > 0 and 0 <= score <= maximum):
                state["awardedPoints"], state["totalPoints"] = score, maximum
                state["targetReached"] = status == "FINISHED" and score == maximum
        if state["targetReached"]:
            state["phase"] = "TARGET_REACHED"
        elif observation.get("hostApprovalRequired"):
            state["phase"] = "HOST_APPROVAL_REQUIRED"
        elif observation.get("runtimeFailed") and self.abort_on_error():
            state["phase"] = "STOP_FOR_DEV"
        elif status in ("FINISHED", "CANCELLED") or (status == "PREPARING" and observation.get("preparationFailed")):
            state["phase"] = "DEV_REVIEW_REQUIRED"
        else:
            state["phase"] = "FROZEN_MATCH"
        with self._db() as db:
            db.execute("UPDATE cycle SET data=? WHERE id=1", (json.dumps(state),))
        return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("state/dev-cycle/supervisor.db"))
    parser.add_argument("action", choices=["start", "reserve", "record", "status", "authorize-abort"])
    parser.add_argument("--match-id")
    parser.add_argument("--revision")
    args = parser.parse_args()
    loop = TrainingLoop(args.db)
    if args.action == "start":
        result = loop.start_round(args.match_id or "", args.revision or "")
    elif args.action == "reserve":
        result = loop.reserve_observation()
    elif args.action == "record":
        import sys
        result = loop.record(json.load(sys.stdin))
    elif args.action == "authorize-abort":
        loop.authorize_abort_on_error()
        result = {"abortOnError": True, "requiresStoppedBeforeDev": True}
    else:
        result = loop.state()
    print(json.dumps(result))


if __name__ == "__main__":
    main()
