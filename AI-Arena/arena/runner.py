"""Run one agent on the items of an eval set and grade every patch.

Per item: pull the image, hand over the code (work/, a fresh git repo), write TASK.md, start
the bench, run the agent, take `git diff`, grade it with the suite's hidden tests and append
the result to data/results/<agent>.jsonl. Items that already have a result are skipped, so an
interrupted eval resumes where it stopped.

Built-in agents `oracle` (the reference patch) and `nop` (empty patch) only check the
infrastructure; their scores are not agent results.
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from arena import core
from arena.bench import Bench, serve, strip_paths

AGENT_DIR = core.ROOT / "agent"
AGENTS_FILE = core.ROOT / "agents.json"
INFRA_AGENTS = ("oracle", "nop")
HARNESS_DIR = core.ROOT / "harness"  # files other harnesses need in the run dir (see harness/README.md)


def load_agents() -> dict:
    agents = json.loads(AGENTS_FILE.read_text(encoding="utf-8"))
    for name in INFRA_AGENTS:
        agents[name] = {"runner": name}
    return agents


def task_md(row) -> str:
    typ = row["type"]
    text = row["pi_description"] if typ == "PI" else row["description"]
    lines = [f"# {typ}: {row['title']}", "", f"Product: {row['repo']} ({core.language_name(row['language'])})", ""]
    if typ == "PI":
        lines += ["Production incident reported by the customer. This is all the information there is;",
                  "investigate the code to find and fix the cause.", ""]
    lines += ["## Description", "", text.strip(), ""]
    if row.get("mvs"):
        lines += ["## MVS (must pass)", "", *[f"- `{t}`" for t in row["mvs"]["tests"]], "",
                  "The test code is in `mvs.tests.patch`. Run `python tools/bench.py check` to verify.", ""]
    return "\n".join(lines)


def prepare_run(suite, row, run_dir: Path, port: int):
    core.rmtree(run_dir)
    (run_dir / "tools").mkdir(parents=True)
    work = run_dir / "work"
    skipped = suite.export(row, work)
    core.git_init_base(work)
    shutil.copy(AGENT_DIR / "AGENTS.md", run_dir / "AGENTS.md")
    shutil.copy(AGENT_DIR / "tools" / "bench.py", run_dir / "tools" / "bench.py")
    (run_dir / "TASK.md").write_text(task_md(row), encoding="utf-8")
    if row.get("mvs"):
        (run_dir / "mvs.tests.patch").write_text(row["mvs"]["test_patch"], encoding="utf-8", newline="\n")
    (run_dir / "bench.json").write_text(json.dumps({"bench_url": f"http://127.0.0.1:{port}"}), encoding="utf-8")
    return work, skipped


def codex_cmd(agent, run_dir, prompt):
    cmd = ["codex", "exec", "--json", "--ignore-user-config", "--skip-git-repo-check",
           "-C", str(run_dir), "-s", "workspace-write",
           "-c", "sandbox_workspace_write.network_access=true",
           "-c", 'approval_policy="never"',
           "-m", agent["model"], "-c", f'model_reasoning_effort="{agent.get("effort", "medium")}"']
    if sys.platform == "win32":  # the elevated Windows sandbox fails to set up on some machines
        cmd += ["-c", 'windows.sandbox="unelevated"']
    for k, v in (agent.get("codex_config") or {}).items():  # e.g. MCP servers a harness adds
        cmd += ["-c", f"{k}={json.dumps(v)}"]
    return cmd + [prompt]


KICKOFF = "Read AGENTS.md and TASK.md, then do the backlog item now. Work autonomously until it is done."


def kickoff_for(agent, run_dir: Path) -> str:
    """The first user message. `inline_task` puts TASK.md into it (saves the agent a read round-trip)."""
    if not agent.get("inline_task"):
        return KICKOFF
    task = (run_dir / "TASK.md").read_text(encoding="utf-8")
    return ("Do the backlog item below now, following AGENTS.md (already in your context: do not read AGENTS.md "
            "or TASK.md again). Send no message before the final one.\n\n" + task)


def run_codex(agent, run_dir: Path, timeout_s: int) -> dict:
    usage = {}
    acts = {"commands": 0, "bench_check": 0, "bench_exec": 0, "file_changes": 0, "turns": 0}
    first_edit = None
    proc = subprocess.Popen(codex_cmd(agent, run_dir, kickoff_for(agent, run_dir)), stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
    timer = threading.Timer(timeout_s, proc.kill)  # a silent agent (waiting on a tool) still stops
    timer.start()
    t0 = time.time()
    with (run_dir / "agent.jsonl").open("w", encoding="utf-8") as log:
        for line in proc.stdout:
            log.write(line)
            log.flush()  # the dashboard tails this file
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "turn.completed":
                acts["turns"] += 1
                for k, v in ev.get("usage", {}).items():
                    usage[k] = usage.get(k, 0) + v
            item = ev.get("item", {}) if ev.get("type") == "item.completed" else {}
            if item.get("type") == "command_execution":
                cmd = str(item.get("command", ""))
                acts["commands"] += 1
                acts["bench_check"] += "bench.py check" in cmd
                acts["bench_exec"] += "bench.py exec" in cmd
                print(f"    [cmd] {cmd[:140]} -> {item.get('exit_code')}", flush=True)
            elif item.get("type") == "file_change":
                acts["file_changes"] += 1
                first_edit = first_edit or round(time.time() - t0)
    proc.wait()
    timer.cancel()
    mvs_green = None
    if (run_dir / "evidence.txt").exists():  # result of the agent's last `bench.py check`
        mvs_green = bool(json.loads((run_dir / "evidence.txt").read_text(encoding="utf-8")).get("ok"))
    return {"usage": usage, "timed_out": time.time() - t0 >= timeout_s, "exit_code": proc.returncode,
            "actions": acts, "first_edit_seconds": first_edit, "mvs_green": mvs_green}


def run_hermes(agent, run_dir: Path, timeout_s: int) -> dict:
    """Hermes Agent (Nous Research) one-shot: `hermes --in <run_dir> -z <prompt>`; AGENTS.md in the cwd
    is loaded by Hermes itself. Token usage comes from --usage-file; Hermes prints only the final reply,
    so there is no per-command event stream (actions stay 0)."""
    usage_file = run_dir / "hermes_usage.json"
    cmd = [agent.get("command", "hermes"), "--in", str(run_dir), "-z", KICKOFF,
           "--model", agent["model"], "--provider", agent.get("provider", "openai-codex"),
           "--reasoning", agent.get("effort", "medium"), "--usage-file", str(usage_file)]
    env = {k: v for k, v in os.environ.items() if k not in ("HF_TOKEN", "OPENROUTER_API_KEY")}  # keep the provider explicit
    if agent.get("home"):
        env["HERMES_HOME"] = agent["home"]
    t0 = time.time()
    with (run_dir / "agent.log").open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                cwd=str(run_dir), shell=(sys.platform == "win32"), env=env)
        try:
            code = proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = proc.wait()
    usage, extra = {}, {}
    if usage_file.exists():
        u = json.loads(usage_file.read_text(encoding="utf-8"))
        # Hermes' input_tokens already includes the cached part (cache_read_tokens), like Codex's.
        usage = {"input_tokens": u.get("input_tokens") or 0,
                 "cached_input_tokens": u.get("cache_read_tokens") or 0,
                 "output_tokens": u.get("output_tokens") or 0,
                 "reasoning_output_tokens": u.get("reasoning_tokens") or 0}
        extra = {"api_calls": u.get("api_calls"), "estimated_cost_usd": u.get("estimated_cost_usd")}
    return {"usage": usage, "timed_out": time.time() - t0 >= timeout_s, "exit_code": code,
            "actions": {"commands": 0, "bench_check": 0, "bench_exec": 0, "file_changes": 0, "turns": 1},
            "first_edit_seconds": None, "mvs_green": _mvs_green(run_dir), **extra}


def run_opencode(agent, run_dir: Path, timeout_s: int) -> dict:
    """OpenCode headless: `opencode run --dir <run_dir> --format json --auto`. The JSON event stream carries
    tool calls and, on step/message end, token usage (summed here); AGENTS.md in the dir is read by OpenCode."""
    cmd = [agent.get("command", "opencode"), "run", "--dir", str(run_dir), "--format", "json", "--auto",
           "--model", agent["model"]]
    if agent.get("effort"):
        cmd += ["--variant", agent["effort"]]
    cmd += [KICKOFF]
    env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY",)}  # subscription login, not a key
    usage, acts, first_edit = {}, {"commands": 0, "bench_check": 0, "bench_exec": 0, "file_changes": 0, "turns": 0}, None
    t0 = time.time()
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            cwd=str(run_dir), text=True, encoding="utf-8", errors="replace", env=env,
                            shell=(sys.platform == "win32"))
    timer = threading.Timer(timeout_s, proc.kill)
    timer.start()
    with (run_dir / "agent.jsonl").open("w", encoding="utf-8") as log:
        for line in proc.stdout:
            log.write(line)
            log.flush()
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            _opencode_event(ev, usage, acts)
            if acts["file_changes"] and first_edit is None:
                first_edit = round(time.time() - t0)
    proc.wait()
    timer.cancel()
    return {"usage": usage, "timed_out": time.time() - t0 >= timeout_s, "exit_code": proc.returncode,
            "actions": acts, "first_edit_seconds": first_edit, "mvs_green": _mvs_green(run_dir)}


def run_antigravity(agent, run_dir: Path, timeout_s: int) -> dict:
    """Antigravity CLI (Google): `agy -p <prompt> --dangerously-skip-permissions --output-format json`"""
    prompt = kickoff_for(agent, run_dir)
    cmd = ["agy", "-p", prompt, "--dangerously-skip-permissions", "--output-format", "json"]
    if agent.get("model"):
        cmd += ["--model", agent["model"]]
    if agent.get("effort"):
        cmd += ["--effort", agent["effort"]]

    env = dict(os.environ)
    env["PATH"] = str(core.PROJECT / ".venv" / "Scripts") + os.pathsep + r"C:\Users\truon\AppData\Local\Programs\Python\Python312" + os.pathsep + env.get("PATH", "")

    t0 = time.time()
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            cwd=str(run_dir), text=True, encoding="utf-8", errors="replace", env=env,
                            shell=(sys.platform == "win32"))
    try:
        stdout, _ = proc.communicate(timeout=timeout_s)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, _ = proc.communicate()
        code = -1

    timed_out = time.time() - t0 >= timeout_s
    with (run_dir / "agent.jsonl").open("w", encoding="utf-8") as log:
        log.write(stdout)

    usage = {}
    turns = 1
    try:
        lines = [l.strip() for l in stdout.strip().splitlines() if l.strip()]
        last_json = None
        for l in reversed(lines):
            if l.startswith("{") and l.endswith("}"):
                try:
                    last_json = json.loads(l)
                    break
                except Exception:
                    pass
        if last_json and "usage" in last_json:
            u = last_json["usage"]
            usage = {
                "input_tokens": u.get("input_tokens") or 0,
                "cached_input_tokens": u.get("cache_read_tokens") or 0,
                "output_tokens": u.get("output_tokens") or 0,
                "reasoning_output_tokens": u.get("thinking_tokens") or 0,
            }
            turns = last_json.get("num_turns", 1)
    except Exception:
        pass

    acts = {"commands": 0, "bench_check": 0, "bench_exec": 0, "file_changes": 0, "turns": turns}
    return {"usage": usage, "timed_out": timed_out, "exit_code": code,
            "actions": acts, "first_edit_seconds": None, "mvs_green": _mvs_green(run_dir)}


def _opencode_event(ev: dict, usage: dict, acts: dict):
    """Fold one OpenCode JSON event into usage/actions; tolerant of shape differences between versions."""
    def walk(o):
        if isinstance(o, dict):
            yield o
            for v in o.values():
                yield from walk(v)
        elif isinstance(o, list):
            for v in o:
                yield from walk(v)
    for d in walk(ev):
        tok = d.get("tokens")
        if isinstance(tok, dict) and ("input" in tok or "output" in tok):
            cache = tok.get("cache") or {}
            usage["input_tokens"] = usage.get("input_tokens", 0) + int(tok.get("input") or 0) + int(cache.get("read") or 0)
            usage["cached_input_tokens"] = usage.get("cached_input_tokens", 0) + int(cache.get("read") or 0)
            usage["output_tokens"] = usage.get("output_tokens", 0) + int(tok.get("output") or 0)
            usage["reasoning_output_tokens"] = usage.get("reasoning_output_tokens", 0) + int(tok.get("reasoning") or 0)
            acts["turns"] += 1
        tool = d.get("tool")
        if isinstance(tool, str) and d.get("state", {}).get("status") == "completed" if isinstance(d.get("state"), dict) else False:
            if tool in ("bash", "shell"):
                acts["commands"] += 1
                cmdline = str((d.get("state") or {}).get("input", {}).get("command", ""))
                acts["bench_check"] += "bench.py check" in cmdline
                acts["bench_exec"] += "bench.py exec" in cmdline
            elif tool in ("edit", "write", "patch", "multiedit"):
                acts["file_changes"] += 1


def prepare_ruflo(run_dir: Path):
    """Codex + Ruflo: copy the project files `npx ruflo init --codex` generates (its AGENTS.md, .agents/
    skills + config, .codex/) into the run dir and append our item guide to its AGENTS.md."""
    src = HARNESS_DIR / "ruflo"
    ours = (run_dir / "AGENTS.md").read_text(encoding="utf-8")
    for d in (".agents", ".codex"):
        if (src / d).exists():
            shutil.copytree(src / d, run_dir / d, dirs_exist_ok=True)
    (run_dir / "AGENTS.md").write_text((src / "AGENTS.md").read_text(encoding="utf-8").rstrip() + "\n\n---\n\n"
                                       + ours, encoding="utf-8")


def _mvs_green(run_dir: Path):
    if (run_dir / "evidence.txt").exists():  # result of the agent's last `bench.py check`
        return bool(json.loads((run_dir / "evidence.txt").read_text(encoding="utf-8")).get("ok"))
    return None


def done_ids(agent_name: str) -> set:
    path = core.RESULTS / f"{agent_name}.jsonl"
    return {r["id"] for r in core.read_jsonl(path)} if path.exists() else set()


def eval_item(suites, agent_name, agent, item_id, timeout_min=30) -> dict:
    suite_name, iid = item_id.split("/", 1)
    suite = suites[suite_name]
    row = core.load_row(suite_name, iid)
    for img in suite.images(row):
        core.ensure_image(img)
    t0 = time.time()
    info = {}
    if agent["runner"] == "oracle":
        patch = row.get("patch") or ""
    elif agent["runner"] == "nop":
        patch = ""
    else:
        # A harness may need a run root of its own (OpenCode decodes "%20" in paths, so it cannot work
        # under D:\Projects\AI%20Arena): agents.json "run_root".
        run_dir = Path(agent["run_root"]) / agent_name / item_id.replace("/", "__") if agent.get("run_root")             else core.RUNS / agent_name / item_id.replace("/", "__")
        work = run_dir / "work"
        bench = Bench(suite, row, work)
        srv = serve(bench)
        try:
            _, skipped = prepare_run(suite, row, run_dir, srv.server_address[1])
            if agent.get("agents_md"):  # a variant of the worker guide, e.g. agent/AGENTS_lean.md
                shutil.copy(AGENT_DIR / agent["agents_md"], run_dir / "AGENTS.md")
            if skipped:
                info["export_skipped"] = len(skipped)
            print(f"    agent working in {run_dir}", flush=True)
            if agent["runner"] == "codex":
                if agent.get("harness") == "ruflo":
                    prepare_ruflo(run_dir)
                info.update(run_codex(agent, run_dir, timeout_min * 60))
            elif agent["runner"] == "hermes":
                info.update(run_hermes(agent, run_dir, timeout_min * 60))
            elif agent["runner"] == "opencode":
                info.update(run_opencode(agent, run_dir, timeout_min * 60))
            elif agent["runner"] == "antigravity":
                info.update(run_antigravity(agent, run_dir, timeout_min * 60))
            else:
                raise ValueError(f"unknown runner {agent['runner']}")
            patch = strip_paths(core.git_diff(work), row)
            (run_dir / "patch.diff").write_text(patch, encoding="utf-8", newline="\n")
        finally:
            srv.shutdown()
    agent_s = round(time.time() - t0)
    g = suite.grade(row, patch)
    log_dir = core.RESULTS / "logs" / agent_name
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{item_id.replace('/', '__')}.log").write_text(g["log"], encoding="utf-8")
    result = {"id": item_id, "suite": suite_name, "type": row["type"], "language": row["language"],
              "resolved": g["resolved"], "points": core.POINTS[row["type"]] if g["resolved"] else 0,
              "max_points": core.POINTS[row["type"]], "agent_seconds": agent_s,
              "grade_seconds": round(time.time() - t0) - agent_s,
              "patch_lines": patch.count("\n"), "detail": g.get("detail"), **info,
              "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    with (core.RESULTS / f"{agent_name}.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(result) + "\n")
    return result
