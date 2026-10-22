"""AI Arena benchmark suite.

  python -m arena setup [--suites ...] [--msb full|mini|flash]   download sources, build the catalog
  python -m arena stats                                           issues per suite x language x type
  python -m arena sample --name dev --per-suite 4                 fixed eval set (same items for every agent)
  python -m arena sample --size xs|s|m|full                       nested preset sets (5 / 10 / 20 / 40 items)
  python -m arena check --set dev                                 infra check: reference patch passes, empty patch fails
  python -m arena eval --agent codex-medium --set dev             run an agent (real runs, real grading)
  python -m arena report --set dev                                agent scores per suite and in total
  python -m arena dashboard --size xs --agents a b                live page (items, match score, agent at work)
"""
import argparse
import json
import random
import sys
from collections import Counter, defaultdict

from arena import core, metrics
from arena.adapters import registry


def cmd_setup(args):
    suites = registry()
    names = list(suites) if args.suites is None else args.suites  # `--suites` alone: catalog only
    core.SOURCES.mkdir(parents=True, exist_ok=True)
    for name in names:
        print(f"== {name}: {suites[name].source}", flush=True)
        n = suites[name].setup(args)
        print(f"   {n} items", flush=True)
    rows, seen, dup = [], set(), Counter()
    for name in suites:
        path = core.SOURCES / f"{name}.jsonl"
        if path.exists():
            for r in core.read_jsonl(path):
                # Multi-SWE-bench's Python part is SWE-bench Verified, and some of it overlaps
                # Multilingual: an issue counts once, in the first suite that has it.
                if r["instance_id"] in seen:
                    dup[name] += 1
                    continue
                seen.add(r["instance_id"])
                r["id"] = f"{name}/{r['instance_id']}"
                rows.append({k: r.get(k) for k in core.CATALOG_FIELDS})
    for name, n in dup.items():
        print(f"   {name}: {n} duplicates of earlier suites left out")
    print(f"catalog: {core.write_jsonl(core.CATALOG, rows)} items -> {core.CATALOG}")


# ---------------------------------------------------------------- tables

def table(headers, rows) -> str:
    cells = [[str(c) for c in headers]] + [[str(c) for c in r] for r in rows]
    w = [max(len(r[i]) for r in cells) for i in range(len(headers))]
    line = lambda r: "| " + " | ".join(c.ljust(w[i]) for i, c in enumerate(r)) + " |"
    return "\n".join([line(cells[0]), "|" + "|".join("-" * (x + 2) for x in w) + "|"] + [line(r) for r in cells[1:]])


def lang_summary(counter: Counter) -> str:
    return ", ".join(f"{core.language_name(k)} {v}" for k, v in counter.most_common())


def cmd_stats(args):
    cat = core.load_catalog()
    if not cat:
        sys.exit("no catalog: run `python -m arena setup` first")
    suites = registry()
    by = defaultdict(list)
    for r in cat:
        by[r["suite"]].append(r)
    rows = []
    for name in suites:
        items = by.get(name, [])
        if not items:
            continue
        t = Counter(r["type"] for r in items)
        rows.append([suites[name].title, len(items), len({r["repo"] for r in items}),
                     *[t.get(x, 0) for x in core.TYPES], lang_summary(Counter(r["language"] for r in items))])
    t = Counter(r["type"] for r in cat)
    rows.append(["**Total**", len(cat), len({r["repo"] for r in cat}), *[t.get(x, 0) for x in core.TYPES],
                 lang_summary(Counter(core.language_name(r["language"]) for r in cat))])
    out = "## Benchmark suites\n\n" + table(["Suite", "Issues", "Repos", *core.TYPES, "Languages"], rows)
    by_stack = defaultdict(list)
    for r in cat:
        by_stack[r["stack"]].append(r)
    srows = []
    for stack in list(core.FPT_STACKS) + sorted(x for x in by_stack if x not in core.FPT_STACKS):
        items = by_stack.get(stack, [])
        t = Counter(r["type"] for r in items)
        apps = len({r["repo"] for r in items if r["repo"] in core.APP_REPOS})
        srows.append([core.stack_name(stack) + (" (FPT)" if stack in core.FPT_STACKS else ""), len(items),
                      len({r["repo"] for r in items}), apps, *[t.get(x, 0) for x in core.TYPES],
                      "**missing**" if not items else ""])
    out += ("\n\n## Stacks\n\nFPT = the company's main stacks; every preset set keeps a quota of each. "
            "Apps = repos that are products rather than libraries (preferred when sampling).\n\n"
            + table(["Stack", "Issues", "Repos", "Apps", *core.TYPES, ""], srows))
    print(out)
    (core.DATA / "stats.md").write_text(out + "\n", encoding="utf-8")


# ---------------------------------------------------------------- eval sets

# Presets. Each size keeps a quota of every FPT stack (Java, .NET, React, Angular, COBOL: the likeliest
# match products) plus a quota of other stacks, both balanced over the item types; app repos are preferred
# to libraries. Sizes nest (xs < s < m < full), so a larger set only adds items to the smaller one, and a
# later `eval` on it skips what is already graded. Items whose reference patch failed `check`
# (data/results/oracle.jsonl) are never picked. A stack with no items in the catalog (today: .NET,
# Angular) leaves its slot empty and is reported.
SIZES = {
    "xs": {"per_fpt_stack": 1, "others": 0, "timeout_min": 15, "skip_stacks": ["java"]},  # Java builds are slow
    "s": {"per_fpt_stack": 1, "others": 5, "timeout_min": 15},
    "m": {"per_fpt_stack": 2, "others": 10, "timeout_min": 30},
    "full": {"per_fpt_stack": 4, "others": 20, "timeout_min": 30},
}
SIZE_ORDER = list(SIZES)
# Never picked for a preset set (with the reason).
EXCLUDED_ITEMS = {
    "multi-swe/elastic__logstash-15928": "MVS check rebuilds and runs the whole logstash suite: >12 min per check",
}
TYPE_CYCLE = ("BUG", "CR", "PI", "TASK")
# Deadlines (minutes from reveal, as in a match): every second item of a type gets one; PI never does.
DEADLINE_MIN = {"BUG": 12, "CR": 15, "TASK": 10, "PI": None}


def assign_deadlines(items, index):
    seen, out = defaultdict(int), {}
    for i in items:
        t = index[i]["type"]
        if DEADLINE_MIN.get(t) is not None and seen[t] % 2 == 0:
            out[i] = DEADLINE_MIN[t]
        seen[t] += 1
    return out


def oracle_results():
    path = core.RESULTS / "oracle.jsonl"
    return {r["id"]: r for r in core.read_jsonl(path)} if path.exists() else {}


def broken_items():
    return {i for i, r in oracle_results().items() if not r["resolved"]}


def checked_items():
    return {i for i, r in oracle_results().items() if r["resolved"]}


def evaluated_items():
    """Items some agent has already been graded on (results are reused, so sets keep them when rules change)."""
    from arena.runner import INFRA_AGENTS
    out = set()
    for path in core.RESULTS.glob("*.jsonl") if core.RESULTS.exists() else []:
        if path.stem not in INFRA_AGENTS:
            out |= {r["id"] for r in core.read_jsonl(path)}
    return out


def shuffled_buckets(rows, key, rng):
    """{key: [ids]} in a seed-fixed order; app repos float to the front of each bucket."""
    checked, evaluated = checked_items(), evaluated_items()
    buckets = defaultdict(list)
    for r in rows:
        buckets[key(r)].append(r)
    out = {}
    for k in sorted(buckets):
        rs = sorted(buckets[k], key=lambda r: r["id"])
        rng.shuffle(rs)
        # Stable sort, shuffled within: suites whose `check` runs only the MVS tests first (Multi-SWE-bench
        # runs a repo's whole suite per check: 10+ min on logstash/material-ui), then app repos first.
        # Then items whose infrastructure is already verified by `check` (pulled image, reference patch
        # passes), so a set keeps the items that were already run when the rules change.
        rs.sort(key=lambda r: (r["suite"] in core.HEAVY_SUITES, r["repo"] not in core.APP_REPOS,
                               r["id"] not in evaluated, r["id"] not in checked))
        out[k] = [r["id"] for r in rs]
    return out


def take_cycling(buckets, order, n, taken):
    """Pop up to n ids cycling over `order` (keys of buckets), skipping ids already taken."""
    picked = []
    while len(picked) < n:
        progressed = False
        for k in order:
            while buckets.get(k) and len(picked) < n:
                i = buckets[k].pop(0)
                if i not in taken:
                    picked.append(i)
                    taken.add(i)
                    progressed = True
                    break
        if not progressed:
            break
    return picked


def build_presets(cat, seed, types=None):
    """All four nested sets at once. Returns {size: {"items": [...], "missing": [stacks]}}."""
    broken = broken_items()
    rows = [r for r in cat if r["id"] not in broken and r["id"] not in EXCLUDED_ITEMS
            and (not types or r["type"] in types)]
    by_stack = defaultdict(list)
    for r in rows:
        by_stack[r["stack"]].append(r)
    other_stacks = sorted(s for s in by_stack if s not in core.FPT_STACKS and s not in core.UNAVAILABLE_STACKS)
    other_ids = {r["id"] for s in other_stacks for r in by_stack[s]}
    sets, taken, prev = {}, set(), []
    for size in SIZE_ORDER:
        quota = SIZES[size]
        items, missing = list(prev), []
        for n_stack, stack in enumerate(core.FPT_STACKS):
            if stack in quota.get("skip_stacks", ()):
                continue
            if not by_stack.get(stack):
                missing.append(stack)
                continue
            ids = {r["id"] for r in by_stack[stack]}
            have = sum(1 for i in prev if i in ids)
            # Each stack starts its type cycle one step later than the previous one, and continues where
            # the smaller set stopped: xs already spans several types, and a stack spreads over all four.
            # One random stream per (size, stack): changing one stack's rules leaves the others' picks alone.
            b = shuffled_buckets(by_stack[stack], lambda r: r["type"], random.Random(f"{seed}:{size}:{stack}"))
            k = (have + n_stack) % 4
            order = [t for t in TYPE_CYCLE[k:] + TYPE_CYCLE[:k] if t in b]
            items += take_cycling(b, order, quota["per_fpt_stack"] - have, taken)
        have_other = sum(1 for i in prev if i in other_ids)
        need = quota["others"] - have_other
        if need > 0:
            # Others: cycle the types; inside a type cycle the stacks, so a small set still spans languages.
            per_type = defaultdict(list)
            for s in other_stacks:
                per_type_rows = [r for r in by_stack[s]]
                for r in per_type_rows:
                    per_type[r["type"]].append(r)
            rng = random.Random(f"{seed}:{size}:others")
            buckets = {t: shuffled_buckets(per_type[t], lambda r: r["stack"], rng) for t in TYPE_CYCLE}
            picked, t_i, stack_pos, idle = [], have_other, have_other, 0
            while len(picked) < need and idle < 4:
                t = TYPE_CYCLE[t_i % 4]
                t_i += 1
                b = buckets[t]
                got = []
                for _ in range(len(other_stacks)):  # next stack (shared cursor) that has an item of this type
                    k = other_stacks[stack_pos % len(other_stacks)]
                    stack_pos += 1
                    got = take_cycling(b, [k], 1, taken)
                    if got:
                        break
                picked += got
                idle = 0 if got else idle + 1
            items += picked
        sets[size] = {"items": items, "missing": missing}
        prev = items
    return sets


def cmd_sample(args):
    cat = core.load_catalog()
    if not cat:  # never overwrite the committed sets with an empty pick
        sys.exit("no catalog: run `python -m arena setup` first")
    if args.size:
        sets = build_presets(cat, args.seed, args.types)
        index = {r["id"]: r for r in cat}
        deadlines = assign_deadlines(sets["full"]["items"], index)  # same deadline for an item in every size
        for size in SIZE_ORDER:
            meta = {"size": size, **{k: v for k, v in SIZES[size].items() if k != "timeout_min"},
                    "excluded": EXCLUDED_ITEMS,
                    "missing_stacks": sets[size]["missing"],
                    "deadlines": {i: deadlines[i] for i in sets[size]["items"] if i in deadlines}}
            save_set(argparse.Namespace(name=size, seed=args.seed), cat, sets[size]["items"], meta,
                     quiet=size != args.size)
        if sets[args.size]["missing"]:
            print("no items in the catalog for:", ", ".join(core.stack_name(x) for x in sets[args.size]["missing"]),
                  "(slots left empty)")
        return
    args.name = args.name or "dev"
    rng = random.Random(args.seed)
    chosen = sample_per_suite(cat, rng, args.per_suite, args.suites, args.types)
    save_set(args, cat, chosen, {"per_suite": args.per_suite})


def sample_per_suite(cat, rng, per_suite, suites, types):
    """per_suite items of each suite, round-robin over language x type so a small set still spreads out."""
    chosen = []
    for name in suites or sorted({r["suite"] for r in cat}):
        buckets = defaultdict(list)
        for r in cat:
            if r["suite"] == name and (not types or r["type"] in types):
                buckets[(r["language"], r["type"])].append(r["id"])
        keys = sorted(buckets)
        rng.shuffle(keys)
        for k in keys:
            buckets[k].sort()
            rng.shuffle(buckets[k])
        picked = []
        while len(picked) < per_suite and any(buckets[k] for k in keys):
            for k in keys:
                if buckets[k] and len(picked) < per_suite:
                    picked.append(buckets[k].pop())
        chosen += picked
    return chosen


def save_set(args, cat, chosen, meta, quiet=False):
    core.SETS.mkdir(parents=True, exist_ok=True)
    path = core.SETS / f"{args.name}.json"
    path.write_text(json.dumps({"seed": args.seed, **meta, "items": chosen}, indent=1), encoding="utf-8")
    index = {r["id"]: r for r in cat}
    if not quiet:
        dl = meta.get("deadlines", {})
        print(table(["Item", "Type", "Stack", "Deadline"],
                    [[i, index[i]["type"], core.stack_name(index[i]["stack"]), f"{dl[i]} min" if i in dl else "-"]
                     for i in chosen]))
        print(f"{len(chosen)} items -> {path}")
    return path


def load_set_meta(name):
    path = core.SETS / f"{name}.json"
    if not path.exists():
        sys.exit(f"no eval set {name}: run `python -m arena sample --name {name}` first")
    return json.loads(path.read_text(encoding="utf-8"))


def load_set(name):
    return load_set_meta(name)["items"]


def run_agent(agent_name, items, timeout_min, redo=False):
    from arena import runner
    agents = runner.load_agents()
    if agent_name not in agents:
        sys.exit(f"unknown agent {agent_name}; agents.json has: {', '.join(agents)}")
    suites = registry()
    done = set() if redo else runner.done_ids(agent_name)
    core.RESULTS.mkdir(parents=True, exist_ok=True)
    results = []
    for i, item in enumerate(items, 1):
        if item in done:
            print(f"[{i}/{len(items)}] {item}: already graded, skipped")
            continue
        print(f"[{i}/{len(items)}] {item} ({agent_name})", flush=True)
        try:
            r = runner.eval_item(suites, agent_name, agents[agent_name], item, timeout_min)
        except Exception as e:
            print(f"    ERROR {type(e).__name__}: {e}", flush=True)
            continue
        results.append(r)
        print(f"    {'RESOLVED' if r['resolved'] else 'not resolved'}  "
              f"(agent {r['agent_seconds']}s, grade {r['grade_seconds']}s)", flush=True)
    return results


def apply_size(args):
    """--size small|full picks the preset set (and its timeout unless --timeout-min is given)."""
    if getattr(args, "size", None):
        args.set = args.size
        if getattr(args, "timeout_min", 0) is None:
            args.timeout_min = SIZES[args.size]["timeout_min"]
    if getattr(args, "timeout_min", 0) is None:
        args.timeout_min = SIZES["full"]["timeout_min"]


def cmd_eval(args):
    apply_size(args)
    items = args.ids or load_set(args.set)
    run_agent(args.agent, items[: args.limit] if args.limit else items, args.timeout_min, args.redo)
    cmd_report(argparse.Namespace(set=args.set if not args.ids else None, agents=[args.agent], infra=False,
                                  all_items=True, size=None))


def cmd_check(args):
    apply_size(args)
    items = args.ids or load_set(args.set)
    ok = run_agent("oracle", items, 0, args.redo)
    bad = run_agent("nop", items, 0, args.redo)
    broken = [r["id"] for r in ok if not r["resolved"]] + [r["id"] for r in bad if r["resolved"]]
    print(f"\ninfra check: reference patch resolved {sum(r['resolved'] for r in ok)}/{len(ok)}, "
          f"empty patch resolved {sum(r['resolved'] for r in bad)}/{len(bad)}")
    if broken:
        print("items to look at (logs in data/results/logs/):", *broken, sep="\n  ")


# ---------------------------------------------------------------- report

def cmd_report(args):
    from arena import runner
    apply_size(args)
    meta = load_set_meta(args.set) if args.set else {}
    items = set(meta["items"]) if args.set else None
    names = args.agents or sorted(p.stem for p in core.RESULTS.glob("*.jsonl"))
    if not args.infra:
        names = [n for n in names if n not in runner.INFRA_AGENTS]
    suites = registry()
    agents = json.loads(runner.AGENTS_FILE.read_text(encoding="utf-8"))
    rows, used, loaded = [], [], []
    for name in names:
        path = core.RESULTS / f"{name}.jsonl"
        if not path.exists():
            continue
        last = {}
        for r in core.read_jsonl(path):
            if items is None or r["id"] in items:
                last[r["id"]] = r  # a re-run replaces the earlier result
        if last:
            loaded.append((name, last))
    if len(loaded) > 1 and not args.all_items:
        # Compare agents on the same items only: the intersection of what each one has done.
        common = set.intersection(*(set(last) for _, last in loaded))
        loaded = [(n, {k: v for k, v in last.items() if k in common}) for n, last in loaded]
    for name, last in loaded:
        res = list(last.values())
        if not res:
            continue
        cells = []
        for s in suites:
            sr = [r for r in res if r["suite"] == s]
            if s not in used and sr:
                used.append(s)
            cells.append(sr)
        rows.append((name, cells, res))

    def cell(rs):
        if not rs:
            return "-"
        n, ok = len(rs), sum(r["resolved"] for r in rs)
        score = round(1000 * sum(r["points"] for r in rs) / sum(r["max_points"] for r in rs))
        return f"{ok}/{n} ({100 * ok // n}%) · {score}"

    order = [s for s in suites if s in used]
    out_rows = []
    for name, cells, res in rows:
        by = dict(zip(suites, cells))
        tok = sum((r.get("usage") or {}).get("input_tokens", 0) + (r.get("usage") or {}).get("output_tokens", 0)
                  for r in res)
        a = agents.get(name, {})
        label = f"{name} ({a.get('model', '')} {a.get('effort', '')})".replace(" ()", "").replace("( ", "(")
        out_rows.append([label, *[cell(by[s]) for s in order], cell(res),
                         f"{tok / 1e6:.1f}M" if tok else "-"])
    head = ["Agent", *[suites[s].title for s in order], "Total", "Tokens"]
    by_type = []
    for name, cells, res in rows:
        by_type.append([name, *[cell([r for r in res if r["type"] == t]) for t in core.TYPES]])
    prices = {n: agents.get(n, {}).get("price") for n, _, _ in rows}
    eff = [metrics.efficiency_row(n, metrics.summarize(res, prices[n])) for n, _, res in rows]
    speed = []
    for name, _, res in rows:
        cells = []
        for t in core.TYPES:
            st = metrics.summarize([r for r in res if r["type"] == t], prices[name])
            cells.append(f"{st['median_min']:.1f} min · {metrics.fmt_k(st['billable_item'])}" if st else "-")
        speed.append([name, *cells])
    per_agent = {n: res for n, _, res in rows}
    match_rows, match_detail = metrics.match_table(per_agent, meta.get("deadlines"))
    common_note = (f"{len(rows[0][2])} items common to every agent shown.\n\n"
                   if len(rows) > 1 and not args.all_items else "")
    out = ("## Agent scores\n\n" + common_note + "Cell = resolved/attempted (%) · MTM score out of 1,000 "
           f"(points: {', '.join(f'{k} {v}' for k, v in core.POINTS.items())}).\n\n"
           + table(head, out_rows)
           + "\n\n### MTM match score (Game Rules)\n\nOfficial = points of PASSED items; FAILED, unfinished or "
             "submitted after the item's deadline = 0; PI points by speed rank among the agents shown "
             "(100/90/75/60%). Tie-breakers (TB, only decide a tie): +10 per 1% faster last completion / "
             "fewer billable tokens than the average of the agents shown.\n\n"
           + table(metrics.MATCH_HEAD, match_rows) + "\n\n<details><summary>Per item</summary>\n\n"
           + table(metrics.MATCH_DETAIL_HEAD, match_detail) + "\n\n</details>"
           + "\n\n### By item type\n\n" + table(["Agent", *core.TYPES], by_type)
           + "\n\n### Speed and cost\n\nBillable tokens = uncached input + output (what drives Codex credits).\n\n"
           + table(metrics.EFFICIENCY_HEAD, eff)
           + "\n\n### Median minutes · billable tokens per item, by type\n\n"
           + table(["Agent", *core.TYPES], speed)
           + "\n\n### Routing advice\n\nBest = most expected points per agent-minute; frugal = fewest billable "
             "tokens within 10 points of the best resolve rate.\n\n"
           + table(["Type", "Best", "Frugal", "Why"], metrics.advise(per_agent, prices)))
    print(out)
    (core.DATA / "report.md").write_text(out + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(prog="python -m arena", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("setup")
    p.add_argument("--suites", nargs="*")
    p.add_argument("--msb", default="full", choices=["full", "mini", "flash"])
    sub.add_parser("stats")
    p = sub.add_parser("sample")
    p.add_argument("--name", help="set name (default: the --size name, else dev)")
    p.add_argument("--size", choices=list(SIZES), help="preset size; writes all four nested sets xs/s/m/full and prints this one")
    p.add_argument("--per-suite", type=int, default=4)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--suites", nargs="*")
    p.add_argument("--types", nargs="*", choices=core.TYPES)
    for name in ("eval", "check"):
        p = sub.add_parser(name)
        p.add_argument("--set", default="dev")
        p.add_argument("--size", choices=list(SIZES), help="use a preset set (xs, s, m, full)")
        p.add_argument("--ids", nargs="*", help="suite/instance_id items instead of a set")
        p.add_argument("--redo", action="store_true", help="grade again even if a result exists")
        if name == "eval":
            p.add_argument("--agent", required=True)
            p.add_argument("--limit", type=int)
            p.add_argument("--timeout-min", type=int, help="per item (default: xs/s 15, m/full 30)")
    p = sub.add_parser("report")
    p.add_argument("--set")
    p.add_argument("--size", choices=list(SIZES), help="report on a preset set (xs, s, m, full)")
    p.add_argument("--agents", nargs="*")
    p.add_argument("--infra", action="store_true", help="also show the oracle/nop infra agents")
    p.add_argument("--all-items", action="store_true", help="do not restrict agents to their common items")
    if sys.argv[1:2] == ["dashboard"]:
        from arena import dashboard
        return dashboard.main(sys.argv[2:])
    args = ap.parse_args()
    {"setup": cmd_setup, "stats": cmd_stats, "sample": cmd_sample, "eval": cmd_eval,
     "check": cmd_check, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
