## Agent scores

8 items common to every agent shown.

Cell = resolved/attempted (%) · MTM score out of 1,000 (points: PI 250, CR 300, BUG 200, TASK 150).

| Agent                             | SWE-bench Multilingual | SWE-bench Pro (public V2) | COBOL (COBOLEval + legacy batch programs) | Total             | Tokens |
|-----------------------------------|------------------------|---------------------------|-------------------------------------------|-------------------|--------|
| agent-test-0 (gpt-6.1-sol low)    | 4/4 (100%) · 1000      | 3/3 (100%) · 1000         | 1/1 (100%) · 1000                         | 8/8 (100%) · 1000 | 1.9M   |
| codex-lean-low (gpt-6.1-sol low)  | 4/4 (100%) · 1000      | 3/3 (100%) · 1000         | 1/1 (100%) · 1000                         | 8/8 (100%) · 1000 | 1.2M   |
| codex-lean2-low (gpt-6.1-sol low) | 4/4 (100%) · 1000      | 3/3 (100%) · 1000         | 1/1 (100%) · 1000                         | 8/8 (100%) · 1000 | 1.1M   |

### MTM match score (Game Rules)

Official = points of PASSED items; FAILED, unfinished or submitted after the item's deadline = 0; PI points by speed rank among the agents shown (100/90/75/60%). Tie-breakers (TB, only decide a tie): +10 per 1% faster last completion / fewer billable tokens than the average of the agents shown.

| Agent           | Official | /1000 | PASSED | Late | Last done (min) | Credits (billable) | TB time | TB credit | TB total |
|-----------------|----------|-------|--------|------|-----------------|--------------------|---------|-----------|----------|
| codex-lean-low  | 1725     | 986   | 8/8    | 0    | 3.8             | 135k               | +0      | +121      | +121     |
| codex-lean2-low | 1725     | 986   | 8/8    | 0    | 3.7             | 131k               | +27     | +146      | +173     |
| agent-test-0    | 1626     | 929   | 8/8    | 0    | 3.9             | 195k               | -27     | -267      | -294     |

<details><summary>Per item</summary>

| Item                                             | Type | Agent           | Min | Deadline | Billable | Points | Result        |
|--------------------------------------------------|------|-----------------|-----|----------|----------|--------|---------------|
| cobol-carddemo-16                                | BUG  | agent-test-0    | 0.7 | 12       | 20k      | 200    | PASSED        |
| cobol-carddemo-16                                | BUG  | codex-lean-low  | 0.7 | 12       | 7k       | 200    | PASSED        |
| cobol-carddemo-16                                | BUG  | codex-lean2-low | 0.6 | 12       | 10k      | 200    | PASSED        |
| apache__druid-16875                              | BUG  | agent-test-0    | 3.9 | -        | 19k      | 200    | PASSED        |
| apache__druid-16875                              | BUG  | codex-lean-low  | 3.8 | -        | 28k      | 200    | PASSED        |
| apache__druid-16875                              | BUG  | codex-lean2-low | 3.7 | -        | 21k      | 200    | PASSED        |
| immutable-js__immutable-js-2005                  | PI   | agent-test-0    | 1.8 | -        | 37k      | 188    | PI rank x0.75 |
| immutable-js__immutable-js-2005                  | PI   | codex-lean-low  | 1.1 | -        | 17k      | 250    | PI rank x1.0  |
| immutable-js__immutable-js-2005                  | PI   | codex-lean2-low | 1.1 | -        | 19k      | 225    | PI rank x0.9  |
| jekyll__jekyll-9141                              | BUG  | agent-test-0    | 1.2 | -        | 13k      | 200    | PASSED        |
| jekyll__jekyll-9141                              | BUG  | codex-lean-low  | 0.9 | -        | 14k      | 200    | PASSED        |
| jekyll__jekyll-9141                              | BUG  | codex-lean2-low | 0.8 | -        | 13k      | 200    | PASSED        |
| micropython__micropython-13039                   | BUG  | agent-test-0    | 1.5 | 12       | 33k      | 200    | PASSED        |
| micropython__micropython-13039                   | BUG  | codex-lean-low  | 1.0 | 12       | 23k      | 200    | PASSED        |
| micropython__micropython-13039                   | BUG  | codex-lean2-low | 0.9 | 12       | 13k      | 200    | PASSED        |
| instance_ansible__ansible-379058e10f3dbc0fdcaf80 | TASK | agent-test-0    | 2.7 | 10       | 25k      | 150    | PASSED        |
| instance_ansible__ansible-379058e10f3dbc0fdcaf80 | TASK | codex-lean-low  | 1.9 | 10       | 13k      | 150    | PASSED        |
| instance_ansible__ansible-379058e10f3dbc0fdcaf80 | TASK | codex-lean2-low | 1.9 | 10       | 11k      | 150    | PASSED        |
| instance_element-hq__element-web-ee13e23b156fbad | PI   | agent-test-0    | 2.3 | -        | 23k      | 188    | PI rank x0.75 |
| instance_element-hq__element-web-ee13e23b156fbad | PI   | codex-lean-low  | 2.1 | -        | 17k      | 225    | PI rank x0.9  |
| instance_element-hq__element-web-ee13e23b156fbad | PI   | codex-lean2-low | 1.6 | -        | 29k      | 250    | PI rank x1.0  |
| instance_navidrome__navidrome-9c3b4561652a158469 | CR   | agent-test-0    | 1.1 | 15       | 24k      | 300    | PASSED        |
| instance_navidrome__navidrome-9c3b4561652a158469 | CR   | codex-lean-low  | 0.9 | 15       | 15k      | 300    | PASSED        |
| instance_navidrome__navidrome-9c3b4561652a158469 | CR   | codex-lean2-low | 1.0 | 15       | 15k      | 300    | PASSED        |

</details>

### By item type

| Agent           | BUG               | CR                | TASK              | PI                |
|-----------------|-------------------|-------------------|-------------------|-------------------|
| agent-test-0    | 4/4 (100%) · 1000 | 1/1 (100%) · 1000 | 1/1 (100%) · 1000 | 2/2 (100%) · 1000 |
| codex-lean-low  | 4/4 (100%) · 1000 | 1/1 (100%) · 1000 | 1/1 (100%) · 1000 | 2/2 (100%) · 1000 |
| codex-lean2-low | 4/4 (100%) · 1000 | 1/1 (100%) · 1000 | 1/1 (100%) · 1000 | 2/2 (100%) · 1000 |

### Speed and cost

Billable tokens = uncached input + output (what drives Codex credits).

| Agent           | Resolved   | Score | Median min | P90 min | 1st edit min | Timeouts | Tokens/item | Billable/item | Billable/resolved | USD/resolved | Pts/min | Pts/1M tok | MVS green→failed | Cmds/item |
|-----------------|------------|-------|------------|---------|--------------|----------|-------------|---------------|-------------------|--------------|---------|------------|------------------|-----------|
| agent-test-0    | 8/8 (100%) | 1000  | 1.5        | 2.1     | 0.4          | 0        | 241k        | 24k           | 24k               | -            | 138     | 8994       | 0%               | 8         |
| codex-lean-low  | 8/8 (100%) | 1000  | 0.9        | 1.7     | 0.5          | 0        | 155k        | 17k           | 17k               | -            | 174     | 12960      | 0%               | 6         |
| codex-lean2-low | 8/8 (100%) | 1000  | 0.9        | 1.4     | 0.3          | 0        | 135k        | 16k           | 16k               | -            | 190     | 13341      | 0%               | 6         |

### Median minutes · billable tokens per item, by type

| Agent           | BUG           | CR            | TASK          | PI            |
|-----------------|---------------|---------------|---------------|---------------|
| agent-test-0    | 1.3 min · 21k | 1.0 min · 24k | 2.1 min · 25k | 1.8 min · 30k |
| codex-lean-low  | 0.9 min · 18k | 0.8 min · 15k | 1.4 min · 13k | 1.3 min · 17k |
| codex-lean2-low | 0.8 min · 14k | 0.9 min · 15k | 1.4 min · 11k | 1.1 min · 24k |

### Routing advice

Best = most expected points per agent-minute; frugal = fewest billable tokens within 10 points of the best resolve rate.

| Type | Best            | Frugal          | Why                                                                         |
|------|-----------------|-----------------|-----------------------------------------------------------------------------|
| BUG  | codex-lean2-low | codex-lean2-low | codex-lean2-low: 100% in 0.8 min median; codex-lean2-low: 14k billable/item |
| CR   | -               | -               | not enough data (need ≥2 items per agent)                                   |
| TASK | -               | -               | not enough data (need ≥2 items per agent)                                   |
| PI   | codex-lean2-low | codex-lean-low  | codex-lean2-low: 100% in 1.1 min median; codex-lean-low: 17k billable/item  |
