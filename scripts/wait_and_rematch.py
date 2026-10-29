#!/usr/bin/env python3
"""
Automated Rematch Monitor and Initiator for MTM 2026.
Polls until the active match finishes, triggers rematch, and initializes MTM Player with Luna Low.
"""

import time
import json
import urllib.request
import urllib.error
import sys
import subprocess
import os

ORIGIN = "https://masterthemachine.tech"
COOKIES_FILE = "/tmp/gm_cookies.txt"
CURRENT_MATCH_ID = os.environ.get("MTM_MATCH_ID", "")
TEAM_ID = os.environ.get("MTM_TEAM_ID", "")
API_KEY = os.environ.get("MTM_API_KEY", "")
LOCAL_PLAYER_URL = "http://127.0.0.1:54321"

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def try_rematch():
    url = f"{ORIGIN}/api/v1/match/{CURRENT_MATCH_ID}/rematch"
    cmd = [
        "curl", "-s", "-i", "-b", COOKIES_FILE, "-X", "POST", url,
        "-H", f"Origin: {ORIGIN}",
        "-H", "Content-Type: application/json"
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out = res.stdout
    if "HTTP/2 201" in out or "HTTP/1.1 201" in out:
        # Find JSON payload
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("{") and "publicId" in line:
                return json.loads(line)
    return None

def get_match_status():
    url = f"{ORIGIN}/match/{CURRENT_MATCH_ID}"
    cmd = ["curl", "-s", "-b", COOKIES_FILE, url]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    html = res.stdout
    if "FINISHED" in html.upper():
        return "FINISHED"
    import re
    m = re.search(r'(\d+:\d+\s*/\s*\d+:\d+\s*left)', html)
    clock = m.group(1) if m else "unknown"
    return f"IN_PROGRESS ({clock})"

def initialize_player(new_match_id):
    log(f"Submitting new match {new_match_id} to local MTM Player with gpt-6-luna (effort: low)...")
    payload = {
        "teamWorkspace": "/Volumes/WorkSpace/Project/Master-the-Machine",
        "teamId": TEAM_ID,
        "matchId": new_match_id,
        "gameMasterApiUrl": ORIGIN,
        "gameMasterSecretKey": API_KEY,
        "teamLoginAccount": os.environ.get("MTM_USERNAME", ""),
        "teamPassword": os.environ.get("MTM_PASSWORD", ""),
        "allowedModels": ["gpt-6-luna"],
        "orchestratorModel": "gpt-6-luna",
        "orchestratorReasoningEffort": "low",
        "approvalMode": "auto_review",
        "streamingEnabled": False,
        "clearChallenge": False
    }
    req = urllib.request.Request(
        f"{LOCAL_PLAYER_URL}/init/submit",
        data=json.dumps(payload).encode('utf-8'),
        headers={
            "Origin": LOCAL_PLAYER_URL,
            "Content-Type": "application/json"
        }
    )
    with urllib.request.urlopen(req) as r:
        resp_data = r.read().decode('utf-8')
        log(f"MTM Player response: {resp_data}")

def main():
    if not all((CURRENT_MATCH_ID, TEAM_ID, API_KEY, os.environ.get("MTM_USERNAME"), os.environ.get("MTM_PASSWORD"))):
        raise SystemExit("Explicit MTM_* environment credentials and match assignment required; nothing started.")
    log(f"Monitoring match {CURRENT_MATCH_ID} for rematch readiness...")
    poll_count = 0
    while True:
        status = get_match_status()
        log(f"Match status: {status}")
        
        # Attempt rematch
        rematch_data = try_rematch()
        if rematch_data:
            new_match_id = rematch_data.get("publicId")
            new_match_uuid = rematch_data.get("id")
            log(f"RE-MATCH CREATED! New Match ID: {new_match_id} (UUID: {new_match_uuid})")
            
            # Initialize MTM Player
            initialize_player(new_match_id)
            log("SUCCESS! Rematch initiated and MTM Player initialized.")
            break
        
        poll_count += 1
        time.sleep(20)

if __name__ == "__main__":
    main()
