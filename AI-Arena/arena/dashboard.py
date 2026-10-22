"""Live dashboard for an eval: who is working on what, and the MTM match table as results land.

  python -m arena dashboard [--size xs] [--agents agent-test-0 agent-test-1] [--port 8780]

Reads what `eval` writes (data/results/<agent>.jsonl for graded items, data/runs/<agent>/<item>/agent.jsonl
for the agent at work) on every poll; nothing is cached, so it can be started before or during an eval.
The set and the agents can be switched on the page (or with ?set=...&agents=a,b).
"""
import argparse
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arena import core, metrics  # noqa: E402
from arena.runner import INFRA_AGENTS, load_agents  # noqa: E402

TAIL_BYTES = 200_000


def set_meta(name):
    path = core.SETS / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"items": [], "deadlines": {}}


def results(agent, items):
    path = core.RESULTS / f"{agent}.jsonl"
    last = {}
    if path.exists():
        for r in core.read_jsonl(path):
            if r["id"] in items:
                last[r["id"]] = r
    return last


def live_run(agent, item_id):
    """What the agent is doing right now, from its Codex event log (None if it has not started)."""
    root = Path(load_agents().get(agent, {}).get("run_root") or core.RUNS)
    run_dir = root / agent / item_id.replace("/", "__")
    log = run_dir / "agent.jsonl"
    plain = run_dir / "agent.log"  # harnesses without an event stream (hermes): running, no detail
    if not log.exists() and plain.exists():
        started = plain.stat().st_ctime
        return {"started": started, "elapsed_min": (time.time() - started) / 60, "commands": 0, "edits": 0,
                "events": [], "message": "(no event stream for this harness)", "usage": {},
                "running_cmd": None, "running_sec": None, "billable": None,
                "idle_sec": time.time() - plain.stat().st_mtime}
    if not log.exists():
        if run_dir.exists() and time.time() - run_dir.stat().st_mtime < 900:
            return {"preparing": True, "started": run_dir.stat().st_mtime,
                    "elapsed_min": (time.time() - run_dir.stat().st_mtime) / 60}
        return None
    started = log.stat().st_ctime
    events, usage, cmds, files, msgs, started_cmds = [], {}, 0, 0, [], {}
    with log.open("rb") as f:
        f.seek(max(0, log.stat().st_size - TAIL_BYTES))
        tail = f.read().decode("utf-8", errors="replace").splitlines()
    for line in tail:
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "item.started" and ev.get("item", {}).get("type") == "command_execution":
            started_cmds[ev["item"].get("id")] = str(ev["item"].get("command", ""))[:160]
        item = ev.get("item", {}) if ev.get("type") == "item.completed" else {}
        if item.get("type") == "command_execution":
            started_cmds.pop(item.get("id"), None)
            cmds += 1
            events.append({"kind": "cmd", "text": str(item.get("command", ""))[:160], "exit": item.get("exit_code")})
        elif item.get("type") == "file_change":
            files += 1
            events.append({"kind": "edit", "text": ", ".join(c.get("path", "") for c in item.get("changes", []))[:160]})
        elif item.get("type") == "agent_message":
            msgs.append(item.get("text", "")[:240])
        elif ev.get("type") == "turn.completed":
            for k, v in ev.get("usage", {}).items():
                usage[k] = usage.get(k, 0) + v
    running = list(started_cmds.values())[-1] if started_cmds else None
    return {"started": started, "elapsed_min": (time.time() - started) / 60, "commands": cmds, "edits": files,
            "events": events[-8:], "message": msgs[-1] if msgs else "", "usage": usage,
            "running_cmd": running, "running_sec": time.time() - log.stat().st_mtime if running else None,
            "billable": metrics.billable(usage) if usage else None,
            "idle_sec": time.time() - log.stat().st_mtime}


def catalog_lists():
    sets = sorted(p.stem for p in core.SETS.glob("*.json")) if core.SETS.exists() else []
    agents = [a for a in load_agents() if a not in INFRA_AGENTS]
    with_results = {p.stem for p in core.RESULTS.glob("*.jsonl")} if core.RESULTS.exists() else set()
    return sets, [{"name": a, "has_results": a in with_results} for a in agents]


def state(set_name, agent_names):
    meta = set_meta(set_name)
    items = meta["items"]
    deadlines = meta.get("deadlines", {})
    index = {r["id"]: r for r in core.load_catalog()}
    agents_cfg = load_agents()
    per_agent, cells, live = {}, {}, {}
    for a in agent_names:
        done = results(a, set(items))
        per_agent[a] = list(done.values())
        cells[a] = {}
        for i in items:
            r = done.get(i)
            if r:
                dl = deadlines.get(i)
                late = r["resolved"] and dl is not None and r["agent_seconds"] / 60 > dl
                cells[a][i] = {"status": "done", "resolved": r["resolved"], "late": late,
                               "minutes": metrics.item_minutes(r), "agent_min": r["agent_seconds"] / 60,
                               "billable": metrics.billable(r.get("usage")), "timed_out": r.get("timed_out"),
                               "commands": (r.get("actions") or {}).get("commands")}
            else:
                lr = live_run(a, i)
                if lr and lr.get("preparing"):
                    cells[a][i] = {"status": "preparing", **lr}
                elif lr:
                    cells[a][i] = {"status": "running", **lr}
                    if lr["idle_sec"] < 600:
                        live[a] = {"item": i, **lr}
                else:
                    cells[a][i] = {"status": "pending"}
    rows, detail = metrics.match_table(per_agent, deadlines)
    short = {i.split("/", 1)[1][:48]: i for i in items}
    for row in detail:
        a, got, note = row[2], row[6], row[7]
        i = short.get(row[0])
        if i and cells.get(a, {}).get(i, {}).get("status") == "done":
            cells[a][i].update({"points": got, "note": note})
    sets, agents_all = catalog_lists()
    max_points = sum(core.POINTS[index[i]["type"]] for i in items if i in index)
    totals = {a: {"done": sum(1 for c in cells[a].values() if c["status"] == "done"),
                  "running": sum(1 for c in cells[a].values() if c["status"] in ("running", "preparing")),
                  "billable": sum(c.get("billable") or 0 for c in cells[a].values()),
                  "minutes": sum(c.get("agent_min") or c.get("elapsed_min") or 0 for c in cells[a].values())}
              for a in agent_names}
    return {
        "max_points": max_points, "totals": totals,
        "stacks": sorted({core.stack_name(index[i]["stack"]) for i in items if i in index}),
        "set": set_name, "note": meta.get("note", ""), "now": time.time(), "sets": sets, "agents_all": agents_all,
        "items": [{"id": i, "short": i.split("/", 1)[1][:60], "type": index[i]["type"],
                   "stack": core.stack_name(index[i]["stack"]), "points": core.POINTS[index[i]["type"]],
                   "deadline": deadlines.get(i), "repo": index[i]["repo"]} for i in items if i in index],
        "agents": [{"name": a, "model": agents_cfg.get(a, {}).get("model", ""),
                    "effort": agents_cfg.get(a, {}).get("effort", ""), "note": agents_cfg.get(a, {}).get("note", "")}
                   for a in agent_names],
        "match": [dict(zip(metrics.MATCH_HEAD, r)) for r in rows],
        "cells": cells, "live": live,
    }


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Arena dashboard</title>
<style>
:root{--bg:#0f1419;--panel:#171d25;--line:#2a3340;--fg:#e6edf3;--dim:#8b98a8;--ok:#2ea043;--bad:#da3633;--run:#d29922;--pend:#30363d;--lead:#58a6ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,Segoe UI,sans-serif;padding:16px 20px}
h1{font-size:20px;margin:0}h2{font-size:13px;color:var(--dim);margin:22px 0 8px;text-transform:uppercase;letter-spacing:.06em}
.sub{color:var(--dim);font-size:12px}.bar{display:flex;flex-wrap:wrap;gap:14px;align-items:center;margin:8px 0 4px}
select,label{font:inherit}select{background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:4px 8px}
.chk{display:inline-flex;gap:6px;align-items:center;background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:4px 8px;font-size:13px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;position:relative}
.card.lead{border-color:var(--lead);box-shadow:0 0 0 1px var(--lead) inset}.card .name{font-weight:700;font-size:15px}.card .cfg{color:var(--dim);font-size:12px}
.card .score{font-size:34px;font-weight:800;line-height:1.1;margin:6px 0 2px}.card .score small{font-size:13px;color:var(--dim);font-weight:500}
.kv{display:grid;grid-template-columns:1fr 1fr;gap:2px 10px;font-size:12px;color:var(--dim);margin-top:6px}.kv b{color:var(--fg);font-weight:600}
.crown{position:absolute;top:10px;right:12px;font-size:11px;color:var(--lead);border:1px solid var(--lead);border-radius:4px;padding:1px 6px}
table{border-collapse:collapse;width:100%;background:var(--panel);border:1px solid var(--line);border-radius:8px;overflow:hidden}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:middle;font-variant-numeric:tabular-nums}
th{color:var(--dim);font-weight:600;font-size:12px;background:#131921}td.num,th.num{text-align:right}tr:last-child td{border-bottom:0}
.tag{display:inline-block;padding:1px 7px;border-radius:4px;background:#263040;font-size:11px;color:var(--dim);margin-right:4px;font-weight:600}
.tag.PI{background:#3b2a4f;color:#d2a8ff}.tag.BUG{background:#4a2326;color:#ff9b9b}.tag.CR{background:#1f3a4d;color:#79c0ff}.tag.TASK{background:#2a3b22;color:#9be28a}
.cell{border-radius:8px;padding:7px 10px;min-width:170px;border-left:5px solid var(--pend);background:#1d2430}
.cell .pts{font-size:20px;font-weight:800;line-height:1.1}.cell .pts small{font-size:12px;color:var(--dim);font-weight:500}
.cell .st{font-size:12px;font-weight:700;letter-spacing:.03em}.cell .meta{font-size:12px;color:var(--dim)}
.cell.ok{border-left-color:var(--ok);background:rgba(46,160,67,.14)}.cell.ok .st{color:#3fb950}
.cell.bad{border-left-color:var(--bad);background:rgba(218,54,51,.14)}.cell.bad .st{color:#ff7b72}
.cell.run{border-left-color:var(--run);background:rgba(210,153,34,.14)}.cell.run .st{color:#e3b341}
.cell.pend{color:var(--dim)}.cell.pend .st{color:var(--dim)}
.pulse{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--run);margin-right:6px;animation:p 1.2s infinite}@keyframes p{50%{opacity:.2}}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--dim);margin-top:8px}.legend i{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:5px;vertical-align:-2px}
.live{display:grid;grid-template-columns:repeat(auto-fit,minmax(380px,1fr));gap:12px}.box{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.ev{font:12px/1.4 ui-monospace,Consolas,monospace;color:var(--dim);white-space:pre-wrap;word-break:break-all;margin:2px 0}.ev.cmd{color:#c9d1d9}.ev b{color:var(--run)}
.foot{color:var(--dim);font-size:12px;margin-top:16px}.empty{color:var(--dim);padding:10px}
.strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-top:10px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 12px}.stat .v{font-size:22px;font-weight:800;line-height:1.1}.stat .l{font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:.05em}
.rank{display:inline-block;min-width:28px;text-align:center;border-radius:6px;padding:2px 6px;font-weight:800;font-size:13px;background:#263040;color:var(--dim);margin-right:8px}.rank.r1{background:var(--lead);color:#0b1220}
.barw{height:8px;background:#263040;border-radius:4px;margin:8px 0 2px;overflow:hidden}.barw i{display:block;height:100%;background:var(--lead);border-radius:4px}
.prog{font-size:12px;color:var(--dim)}
</style></head><body>
<div class="bar"><h1>Arena dashboard</h1><span class="sub" id="clock"></span></div>
<div class="bar">
 <label>Set <select id="setsel"></select></label>
 <span id="agentsel"></span>
 <span class="sub" id="setnote"></span>
</div>
<div class="strip" id="strip"></div>
<h2>Match score (Game Rules)</h2><div class="cards" id="cards"></div>
<div class="sub" style="margin-top:8px">Official = points of PASSED items (FAILED, unfinished or after the deadline = 0); PI by speed rank among the agents shown: 1st 100%, 2nd 90%, 3rd 75%, others 60%. TB = tie-breakers, only decide a tie: +10 per 1% faster last completion / fewer billable tokens than the others' average.</div>
<h2>Items</h2><div id="grid"></div>
<div class="legend"><span><i style="background:var(--ok)"></i>PASSED, points earned</span><span><i style="background:var(--bad)"></i>FAILED / late / timeout = 0</span><span><i style="background:var(--run)"></i>agent working</span><span><i style="background:var(--pend)"></i>not run yet</span><span>min = agent time until it stopped · tok = billable tokens (uncached input + output)</span></div>
<h2>Live</h2><div class="live" id="live"></div>
<div class="foot" id="foot"></div>
<script>
const P=new URLSearchParams(location.search);let SET=P.get('set')||'',AG=(P.get('agents')||'').split(',').filter(Boolean);
const k=x=>x==null?'-':(x>=1e6?(x/1e6).toFixed(2)+'M':x>=1e3?Math.round(x/1e3)+'k':x);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function push(){const u=new URL(location);u.searchParams.set('set',SET);u.searchParams.set('agents',AG.join(','));history.replaceState(null,'',u);tick();}
function cell(c,it){
 if(!c||c.status==='pending')return `<div class="cell pend"><div class="st">NOT RUN</div><div class="meta">waiting for eval</div></div>`;
 if(c.status==='preparing')return `<div class="cell run"><div class="st"><span class="pulse"></span>PREPARING</div><div class="meta">copying the product code · ${c.elapsed_min.toFixed(1)} min</div></div>`;
 if(c.status==='running')return `<div class="cell run"><div class="st"><span class="pulse"></span>WORKING ${c.elapsed_min.toFixed(1)} min</div><div class="meta">${c.commands} cmds · ${c.edits} edits${c.billable!=null?' · '+k(c.billable)+' tok':''}</div></div>`;
 const ok=c.resolved&&!c.late;const st=c.timed_out?'TIMEOUT':c.late?'LATE (0 pts)':c.resolved?(c.note&&c.note.startsWith('PI rank')?'PASSED · '+c.note.replace('PI rank ','PI '):'PASSED'):'FAILED';
 return `<div class="cell ${ok?'ok':'bad'}"><div class="pts">${c.points??0} <small>/ ${it.points}</small></div><div class="st">${esc(st)}</div><div class="meta">${c.agent_min.toFixed(1)} min · ${k(c.billable)} tok${c.commands!=null?' · '+c.commands+' cmds':''}</div></div>`}
function card(r,a,lead,rank,maxp,tot){const cfg=a?`${a.model} · effort ${a.effort}${a.note?' · '+a.note:''}`:'';const pct=maxp?Math.round(100*r.Official/maxp):0;
 return `<div class="card ${lead?'lead':''}">${lead?'<span class="crown">leader</span>':''}<div class="name"><span class="rank ${rank===1?'r1':''}">#${rank}</span>${esc(r.Agent)}</div><div class="cfg">${esc(cfg)}</div><div class="score">${r.Official} <small>official · ${r['/1000']}/1000</small></div><div class="kv"><span>PASSED <b>${r.PASSED}</b></span><span>late <b>${r.Late}</b></span><span>last done <b>${r['Last done (min)']} min</b></span><span>credits <b>${r['Credits (billable)']}</b></span><span>TB time <b>${r['TB time']}</b></span><span>TB credit <b>${r['TB credit']}</b></span></div><div class="barw"><i style="width:${pct}%"></i></div><div class="prog">${pct}% of ${maxp} max points · ${tot?tot.done:0} graded${tot&&tot.running?` · ${tot.running} working`:''}</div></div>`}
async function tick(){let s;try{const r=await fetch(`/state?set=${encodeURIComponent(SET)}&agents=${encodeURIComponent(AG.join(','))}`,{cache:'no-store'});s=await r.json()}catch(e){document.getElementById('foot').textContent='server unreachable';return}
 if(s.error){document.getElementById('foot').textContent=s.error;return}
 if(!SET){SET=s.set}if(!AG.length){AG=s.agents.map(a=>a.name)}
 document.getElementById('clock').textContent=`${new Date(s.now*1000).toLocaleTimeString()} · refreshes every 2 s`;
 const sel=document.getElementById('setsel');if(sel.options.length!==s.sets.length){sel.innerHTML=s.sets.map(x=>`<option ${x===s.set?'selected':''}>${esc(x)}</option>`).join('');sel.onchange=()=>{SET=sel.value;push()}}
 const ag=document.getElementById('agentsel');if(ag.children.length!==s.agents_all.length){ag.innerHTML=s.agents_all.map(a=>`<label class="chk"><input type="checkbox" value="${esc(a.name)}" ${AG.includes(a.name)?'checked':''}> ${esc(a.name)}${a.has_results?'':' <span class="sub">(no results)</span>'}</label>`).join(' ');ag.querySelectorAll('input').forEach(i=>i.onchange=()=>{AG=[...ag.querySelectorAll('input:checked')].map(x=>x.value);push()})}
 document.getElementById('setnote').textContent=`${s.items.length} items${s.note?' · '+s.note:''}`;
 const cfg=Object.fromEntries(s.agents.map(a=>[a.name,a]));const top=s.match.length&&s.match[0].Official>0?s.match[0].Official:null;
 const bill=Object.values(s.totals).reduce((x,t)=>x+t.billable,0),mins=Object.values(s.totals).reduce((x,t)=>x+t.minutes,0),graded=Object.values(s.totals).reduce((x,t)=>x+t.done,0);
 document.getElementById('strip').innerHTML=[['items in set',s.items.length],['max points',s.max_points],['stacks',s.stacks.join(', ')||'-'],['graded (all agents)',`${graded} / ${s.items.length*s.agents.length}`],['billable tokens (all agents)',k(bill)],['agent minutes (all agents)',mins.toFixed(1)]].map(([l,v])=>`<div class="stat"><div class="v">${esc(v)}</div><div class="l">${l}</div></div>`).join('');
 document.getElementById('cards').innerHTML=s.match.length?s.match.map((r,i)=>card(r,cfg[r.Agent],top!=null&&r.Official===top&&s.match.filter(x=>x.Official===top).length===1,i+1,s.max_points,s.totals[r.Agent])).join(''):'<div class="empty">no graded item in this set yet</div>';
 document.getElementById('grid').innerHTML=`<table><tr><th>Item</th><th>Type</th><th>Stack</th><th class="num">Pts</th><th>Deadline</th>${s.agents.map(a=>`<th>${esc(a.name)}</th>`).join('')}</tr>${s.items.map(it=>`<tr><td><div>${esc(it.short)}</div><div class="sub">${esc(it.repo)}</div></td><td><span class="tag ${it.type}">${it.type}</span></td><td>${esc(it.stack)}</td><td class="num">${it.points}</td><td>${it.deadline?it.deadline+' min':'<span class="sub">none</span>'}</td>${s.agents.map(a=>`<td>${cell(s.cells[a.name]&&s.cells[a.name][it.id],it)}</td>`).join('')}</tr>`).join('')||'<tr><td class="empty">empty set</td></tr>'}</table>`;
 const L=Object.entries(s.live);document.getElementById('live').innerHTML=L.length?L.map(([a,l])=>`<div class="box"><b>${esc(a)}</b> → ${esc(l.item)} <span class="sub">${l.elapsed_min.toFixed(1)} min · ${l.commands} cmds done · ${l.edits} edits</span>${l.message?`<div class="ev">“${esc(l.message)}”</div>`:''}${l.events.map(e=>`<div class="ev ${e.kind}">${e.kind==='cmd'?'$ ':'✎ '}${esc(e.text)}${e.kind==='cmd'?` <b>→ ${e.exit??'…'}</b>`:''}</div>`).join('')}${l.running_cmd?`<div class="ev cmd"><span class="pulse"></span>$ ${esc(l.running_cmd)} <b>running ${Math.round(l.running_sec)}s</b></div>`:''}</div>`).join(''):'<div class="empty">no agent working right now</div>';
 document.getElementById('foot').textContent='data: arena/data/results/<agent>.jsonl (graded) and arena/data/runs/<agent>/<item>/agent.jsonl (live)';}
tick();setInterval(tick,2000);
</script></body></html>"""


def make_handler(default_set, default_agents):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urlparse(self.path)
            qs = parse_qs(u.query)
            if u.path == "/state":
                set_name = qs.get("set", [""])[0] or default_set
                agents = [a for a in qs.get("agents", [""])[0].split(",") if a] or default_agents
                try:
                    body = json.dumps(state(set_name, agents)).encode()
                    code, ctype = 200, "application/json"
                except Exception as e:  # keep the page alive while files are being written
                    body, code, ctype = json.dumps({"error": repr(e)}).encode(), 500, "application/json"
            else:
                body, code, ctype = PAGE.encode(), 200, "text/html; charset=utf-8"
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return H


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--set")
    ap.add_argument("--size", choices=["xs", "s", "m", "full"])
    ap.add_argument("--agents", nargs="+", default=["agent-test-0", "agent-test-1"])
    ap.add_argument("--port", type=int, default=8780)
    args = ap.parse_args(argv)
    set_name = args.set or args.size or "xs"
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(set_name, args.agents))
    print(f"dashboard on http://127.0.0.1:{args.port}/  (set {set_name}, agents {', '.join(args.agents)})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
