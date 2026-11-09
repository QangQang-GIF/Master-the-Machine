---
name: mtm-task-worker
description: TASK worker - identify the task kind (source trace, data computation, document/image reading, artifact/pack, test-suite), answer only from the canonical scope, compute with scripts, and match each field's exact normalization and JSON type.
---

# TASK — exact answers from the canonical scope

Follow `mtm-init`, `mtm-worker`, `mtm-verification`, `mtm-evidence`. Root owns Start Work, push and Submit.
Most TASK fields are compared by exact equality and scored one by one: the value, its JSON type, its normalization
(string vs integer, sorted order, set vs list, minor units) all matter. A file that fails the schema scores 0.

## 1. Read and classify (first 2 minutes)
From `challenge/<key>.md` and the submit template/schema, note for EVERY field: name, JSON type, normalization
("unsigned decimal integer string", "sorted in code point order", "set", "minor units", "as written"), and the
exact definition in the Question/Report section. Then classify the task:

| Kind | Recognise it by | Method |
|---|---|---|
| Source trace | "answer from the issued source", "Canonical scope: <dir>" | Read only that code path; reproduce with a tiny script or test when possible |
| Data computation | archive/dump in `data/`, rules in the KB, description in the test data store | Script over the full file |
| Document / image | "it exists only as an image", voucher, scan | Read the image; apply the document's own rules |
| Artifact / pack | "commit files + manifest", size limits, "do not change the source" | Generate with a script; verify with the unmodified source |
| Test suite | "design integration test cases from the spec", detection of broken builds | Assert only what the spec states |
| Other (dependency audit, config) | specific checks in the brief | Follow the provided check scripts |

## 2. Rules that decide the score
- **Canonical scope only**: answer from the issued source/data the brief names, not from docs, billing rules or
  assumptions. "As computed by the issued source" means run or trace that code, including its quirks.
- **Do not change the application source** unless the brief asks (trace, data, image and pack tasks forbid it).
- **Rules from the KB**: when the brief says "the company's current rules", `kb_search` the current revision and
  apply every clause (deduplication, reversals, per-bank profiles, what counts as noise). Save the articles.
- **Official data**: `get_test_data <key>` / `fetch_fixture` for the description and data; never invent data.

## 3. Method per kind
- **Source trace**: find the function(s) in the canonical scope, list the constants and branches that apply to the
  scenario, and confirm by executing a minimal script/test against the issued code (through ProjectRuntime).
  For multi-step scenarios (ledger after several actions) replay the steps in code and read the stored result.
- **Data computation**: decompress outside `src/` (never commit the decompressed file); write
  `answers/<key>/evidence/compute.py` that loads the full file, applies the rules literally and prints every
  field; save its output. Check totals two ways (e.g. sum of per-account values = overall value).
- **Document / image**: transcribe the relevant parts into `evidence/transcription.md` first (ids, amounts, struck-
  through lines, legends), then apply the document's own rules in a script.
- **Artifact / pack**: generate files with a script, respect volume/size limits, and verify the manifest by running
  the unmodified issued source on the files (import counts, balances) before committing.
- **Test suite**: one case per spec rule id; assert only behaviour the spec states; run on the current app; where a
  case fails because the app violates the spec, fix the app (never the case); list every case in `submit.json`
  exactly as required (`file`, `title`, `rules`).

## 4. Fill and verify submit.json
- Types exactly as the contract says; strings without prefixes, quotes, spaces, units or prose; lists sorted when
  asked; sets complete with nothing extra; integers in minor units.
- Validate `submit.json` against the schema with a script before committing.
- `status` DONE only when every field is computed from complete data/scope; otherwise honest PARTIAL.
- Commit `answers/<key>/` (script, outputs, evidence) and any required artifacts; write `worker_result.json`.
- Short deadline (e.g. 8 minutes): skip everything except computing and committing the fields.
