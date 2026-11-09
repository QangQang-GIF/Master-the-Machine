---
name: mtm-cr-worker
description: CR worker - extract the exact contract (paths, signatures, test ids, UI labels) and the current company standard first, implement the pure logic to the standard with boundary tests, then UI, official data and every evidence item.
---

# CR — contract first, standard exactly, evidence for every row

Follow `mtm-init`, `mtm-worker`, `mtm-verification`, `mtm-evidence`. Root owns Start Work, push and Submit.
Lessons from graded matches: a CR loses all scenario points when its fixture is not the official QA store file
(CR-01: 0/120); hidden tests (about half the points) check every rule of the KB standard, including formats and edge
cases (CR-03: 0/55 on three missed normalisation cases); a missing screenshot row is `NOT_RUN` = 0.

## 1. Read and extract the contract (first 3–5 minutes)
From `challenge/<key>.md`, the MVS file, the submit template/schema and provided scripts, write a short checklist:
- **Fixed API**: module paths, exported function names, signatures and field names, handler names, routes,
  `data-testid`s, button labels, file names (e.g. `reconciliation-YYYY-MM.csv`). Use them verbatim.
- **Standard**: which company standard defines the behaviour ("current revision", "maintained by the team in the
  internal systems"). `kb_search` it, read the CURRENT revision fully and save it as evidence. Every rule in it
  (eligibility, amounts, rounding, date counting, ordering, encoding/BOM, header, quoting, status values, number
  formats, null cases) becomes a test case. Never assume a format; never reuse a standard from an earlier match.
- **Grading map**: which TC is replayed by a test file, which is checked from evidence content, which from a
  screenshot only, what the hidden tests call, and whether test count / type check are graded.
- **Data**: the official test data for this item and how it must be loaded (seed script, loader, "fresh budget").
- **Dependencies**: notes such as "only right on source that also contains BUG-xx fixes"; tell Root early.
- **Access**: data restricted to signed-in staff etc. is a requirement with its own scenario.

## 2. Logic first (it carries most points)
- Implement the calculation as the named pure function, exactly to the standard. Keep it free of UI/DB so hidden
  tests can call it directly.
- Copy the official `*-mvs.test.ts` unchanged to the stated path and make it pass.
- Write NEW tests from the standard, one per rule and boundary (e.g. 0/1/30/31/60/61/90/91 days, month/leap-year
  crossing, every currency, empty input, special characters and Unicode round-trip, invalid input → null).
  The grader requires the whole suite passing and more tests than the original suite.

## 3. Then UI / server wiring
- Add the screen/route/handler with the exact labels, test ids and placement from the brief; keep existing
  controls working. Respect authentication requirements on every new path.
- Build and serve the app with the project's own commands (`env-require/README.md` or the provided run-checks),
  rebuild after every source change.

## 4. Official data and evidence
- Fetch the official fixture with `fetch_fixture` / the QA store and save it unchanged under the evidence name.
  Load it exactly as described (seed script, fresh budget, import settings).
- Produce each evidence file with the provided scripts/commands (logs, CSV downloads, signed-out checks) and the
  required screenshots at the stated viewport. Generated outputs (CSV, summaries) must come from your submitted
  source on the official data, not be written by hand.
- Report summary fields (e.g. `auditSummary`) from that same run.

## 5. Hand off
- Keep the template rows and fields; `PASS` only with existing evidence and a passing check; honest
  `FAIL`/`NOT_RUN`; `status` DONE only when all rows pass. Engineering record: design, standard revision applied,
  edge cases handled.
- Commit source, new tests, the MVS test copy, evidence and `submit.json`; remove temporary seed copies the brief says
  not to commit. Run the whole suite and the type check before writing `worker_result.json`.
- Deadline items: logic + MVS log + submit.json first, then UI evidence, then extra tests.
