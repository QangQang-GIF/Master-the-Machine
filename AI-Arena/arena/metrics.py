"""Metrics over eval results, for choosing a model/effort per item type.

Quality:    resolved rate, MTM score /1000 (points weighted by type), MVS-green-but-failed rate
Speed:      median / p90 agent minutes, minutes to first edit, timeouts
Cost:       tokens per item (uncached input + output is what drives Codex credits; cached input
            is cheap), tokens per resolved item, optional USD from `price` in agents.json
Efficiency: points per agent-minute, points per 1M billable tokens

`match_table` scores each agent the way the Game Master scores a team (MTM_Game_Rules.html): sum of
the points of PASSED items, 0 for FAILED or missed deadlines, PI points by speed rank among the agents
shown (100/90/75/60%), and the two tie-breakers (last completion time, Codex credits) against the
average of the agents shown, +10 per 1%.

`advise` turns the per-type numbers into a routing table: for each item type, the agent with
the best expected points per minute (PI is a speed race), and the cheapest agent whose resolve
rate is within 10 points of the best (credits are the tie-breaker).
"""
import statistics

from arena import core


def billable(u: dict) -> int:
    u = u or {}
    return max(u.get("input_tokens", 0) - u.get("cached_input_tokens", 0), 0) + u.get("output_tokens", 0)


def total_tokens(u: dict) -> int:
    u = u or {}
    return u.get("input_tokens", 0) + u.get("output_tokens", 0)


def usd(u: dict, price: dict | None):
    if not price:
        return None
    u = u or {}
    cached = u.get("cached_input_tokens", 0)
    return ((u.get("input_tokens", 0) - cached) * price.get("input", 0) + cached * price.get("cached_input", 0)
            + u.get("output_tokens", 0) * price.get("output", 0)) / 1e6


def pct(values, q):
    v = sorted(values)
    return v[min(len(v) - 1, int(round(q * (len(v) - 1))))] if v else None


def summarize(rs: list[dict], price=None) -> dict:
    n = len(rs)
    if not n:
        return {}
    ok = [r for r in rs if r["resolved"]]
    mins = [r["agent_seconds"] / 60 for r in rs]
    bill = [billable(r.get("usage")) for r in rs]
    cost = [usd(r.get("usage"), price) for r in rs]
    green = [r for r in rs if r.get("mvs_green")]
    edits = [r["first_edit_seconds"] / 60 for r in rs if r.get("first_edit_seconds")]
    pts = sum(r["points"] for r in rs)
    return {
        "n": n, "resolved": len(ok), "rate": len(ok) / n,
        "score": round(1000 * pts / sum(r["max_points"] for r in rs)),
        "median_min": statistics.median(mins), "p90_min": pct(mins, 0.9),
        "first_edit_min": statistics.median(edits) if edits else None,
        "timeouts": sum(bool(r.get("timed_out")) for r in rs),
        "tokens_item": statistics.mean(total_tokens(r.get("usage")) for r in rs),
        "billable_item": statistics.mean(bill),
        "billable_resolved": sum(bill) / len(ok) if ok else None,
        "usd_item": statistics.mean(cost) if price else None,
        "usd_resolved": sum(cost) / len(ok) if price and ok else None,
        "pts_per_min": pts / max(sum(mins), 1e-9),
        "pts_per_mtok": pts / max(sum(bill) / 1e6, 1e-9),
        "green_failed": (sum(not r["resolved"] for r in green) / len(green)) if green else None,
        "commands": statistics.mean((r.get("actions") or {}).get("commands", 0) for r in rs),
    }


def fmt_k(x):
    return "-" if x is None else f"{x / 1e6:.2f}M" if x >= 1e6 else f"{x / 1e3:.0f}k"


def fmt(x, spec):
    return "-" if x is None else format(x, spec)


EFFICIENCY_HEAD = ["Agent", "Resolved", "Score", "Median min", "P90 min", "1st edit min", "Timeouts",
                   "Tokens/item", "Billable/item", "Billable/resolved", "USD/resolved", "Pts/min", "Pts/1M tok",
                   "MVS green→failed", "Cmds/item"]


def efficiency_row(name, s):
    return [name, f"{s['resolved']}/{s['n']} ({100 * s['rate']:.0f}%)", s["score"], fmt(s["median_min"], ".1f"),
            fmt(s["p90_min"], ".1f"), fmt(s["first_edit_min"], ".1f"), s["timeouts"], fmt_k(s["tokens_item"]),
            fmt_k(s["billable_item"]), fmt_k(s["billable_resolved"]), fmt(s["usd_resolved"], ".2f"),
            fmt(s["pts_per_min"], ".0f"), fmt(s["pts_per_mtok"], ".0f"),
            "-" if s["green_failed"] is None else f"{100 * s['green_failed']:.0f}%", fmt(s["commands"], ".0f")]


def advise(per_agent: dict[str, list[dict]], prices: dict, min_n=2) -> list[list]:
    """per_agent: name -> results. One row per item type with the recommended agents."""
    rows = []
    for t in core.TYPES:
        cand = {}
        for name, rs in per_agent.items():
            s = summarize([r for r in rs if r["type"] == t], prices.get(name))
            if s and s["n"] >= min_n:
                cand[name] = s
        if not cand:
            rows.append([t, "-", "-", f"not enough data (need ≥{min_n} items per agent)"])
            continue
        # Expected points per minute of agent time; for PI the speed tiers make time count twice.
        def value(s):
            return s["rate"] * core.POINTS[t] / max(s["median_min"], 0.5)
        best = max(cand, key=lambda n: value(cand[n]))
        top_rate = max(s["rate"] for s in cand.values())
        frugal = min((n for n, s in cand.items() if s["rate"] >= top_rate - 0.10),
                     key=lambda n: cand[n]["billable_item"])
        b = cand[best]
        rows.append([t, best, frugal, f"{best}: {100 * b['rate']:.0f}% in {b['median_min']:.1f} min median; "
                     f"{frugal}: {fmt_k(cand[frugal]['billable_item'])} billable/item"])
    return rows


# ---------------------------------------------------------------- match score (Game Rules)

PI_RANK = (1.0, 0.9, 0.75, 0.6)  # fastest, 2nd, 3rd, others


def item_minutes(r) -> float:
    """Reveal to graded, the clock the rules use for PI and deadlines (the item is revealed when the agent starts)."""
    return (r["agent_seconds"] + r.get("grade_seconds", 0)) / 60


def match_table(per_agent: dict[str, list[dict]], deadlines: dict | None = None) -> tuple[list, list]:
    """Rows of the MTM match table, one per agent, and the per-item detail rows."""
    deadlines = deadlines or {}
    items = sorted({r["id"] for rs in per_agent.values() for r in rs})
    by = {name: {r["id"]: r for r in rs} for name, rs in per_agent.items()}
    # PI speed rank among the agents that PASSED the item (the "teams" of this match).
    pi_mult = {}
    for i in items:
        passed = sorted((item_minutes(by[n][i]), n) for n in by if i in by[n] and by[n][i]["resolved"]
                        and by[n][i]["type"] == "PI")
        for rank, (_, n) in enumerate(passed):
            pi_mult[(n, i)] = PI_RANK[min(rank, len(PI_RANK) - 1)]
    stats = {}
    for name, rs in per_agent.items():
        official, late, maxp, last = 0, 0, 0, 0.0
        for r in rs:
            maxp += r["max_points"]
            if not r["resolved"]:
                continue
            dl = deadlines.get(r["id"])
            if dl is not None and r["agent_seconds"] / 60 > dl:  # submitted after CLOSED: rejected
                late += 1
                continue
            mult = pi_mult.get((name, r["id"]), 1.0) if r["type"] == "PI" else 1.0
            official += round(r["max_points"] * mult)
            last = max(last, item_minutes(r))
        stats[name] = {"official": official, "per_1000": round(1000 * official / maxp) if maxp else 0,
                       "passed": sum(r["resolved"] for r in rs), "n": len(rs), "late": late,
                       "last_min": last, "billable": sum(billable(r.get("usage")) for r in rs)}
    # Tie-breakers: vs the average of the agents that have done the same items (the teams of one match),
    # +10 per 1% better. Blank until at least two agents have finished the same set of items.
    full = {n for n, rs in per_agent.items() if {r["id"] for r in rs} == set(items)}
    comparable = full if len(full) >= 2 else set()
    avg_last = statistics.mean(stats[n]["last_min"] for n in comparable) if comparable else 0
    avg_bill = statistics.mean(stats[n]["billable"] for n in comparable) if comparable else 0
    rows = []
    for name, s in sorted(stats.items(), key=lambda kv: -kv[1]["official"]):
        if name in comparable:
            tb_time = round(10 * 100 * (avg_last - s["last_min"]) / avg_last) if avg_last else 0
            tb_credit = round(10 * 100 * (avg_bill - s["billable"]) / avg_bill) if avg_bill else 0
            tb = [f"{tb_time:+d}", f"{tb_credit:+d}", f"{tb_time + tb_credit:+d}"]
        else:
            tb = ["-", "-", "-"]
        rows.append([name, s["official"], s["per_1000"], f"{s['passed']}/{s['n']}", s["late"],
                     f"{s['last_min']:.1f}", fmt_k(s["billable"]), *tb])
    detail = []
    for i in items:
        for name in per_agent:
            r = by[name].get(i)
            if not r:
                continue
            dl = deadlines.get(i)
            minutes = item_minutes(r)
            if not r["resolved"]:
                got, note = 0, "FAILED"
            elif dl is not None and r["agent_seconds"] / 60 > dl:
                got, note = 0, f"late (deadline {dl} min)"
            else:
                mult = pi_mult.get((name, i), 1.0) if r["type"] == "PI" else 1.0
                got, note = round(r["max_points"] * mult), (f"PI rank x{mult}" if r["type"] == "PI" else "PASSED")
            detail.append([i.split("/", 1)[1][:48], r["type"], name, f"{minutes:.1f}",
                           "-" if dl is None else dl, fmt_k(billable(r.get("usage"))), got, note])
    return rows, detail


MATCH_HEAD = ["Agent", "Official", "/1000", "PASSED", "Late", "Last done (min)", "Credits (billable)",
              "TB time", "TB credit", "TB total"]
MATCH_DETAIL_HEAD = ["Item", "Type", "Agent", "Min", "Deadline", "Billable", "Points", "Result"]
