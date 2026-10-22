## Agent scores

2 items common to every agent shown.

Cell = resolved/attempted (%) · MTM score out of 1,000 (points: PI 250, CR 300, BUG 200, TASK 150).

| Agent                                 | SWE-bench Pro (public V2) | COBOL (COBOLEval + legacy batch programs) | Total             | Tokens |
|---------------------------------------|---------------------------|-------------------------------------------|-------------------|--------|
| agent-test-0 (gpt-6.1-sol low)        | 1/1 (100%) · 1000         | 1/1 (100%) · 1000                         | 2/2 (100%) · 1000 | 0.5M   |
| codex-ruflo-low (gpt-6.1-sol low)     | 1/1 (100%) · 1000         | 1/1 (100%) · 1000                         | 2/2 (100%) · 1000 | 0.6M   |
| hermes-low (gpt-6.1-sol low)          | 1/1 (100%) · 1000         | 1/1 (100%) · 1000                         | 2/2 (100%) · 1000 | 0.6M   |
| opencode-low (openai/gpt-6.1-sol low) | 1/1 (100%) · 1000         | 1/1 (100%) · 1000                         | 2/2 (100%) · 1000 | 0.4M   |

### MTM match score (Game Rules)

Official = points of PASSED items; FAILED, unfinished or submitted after the item's deadline = 0; PI points by speed rank among the agents shown (100/90/75/60%). Tie-breakers (TB, only decide a tie): +10 per 1% faster last completion / fewer billable tokens than the average of the agents shown.

| Agent           | Official | /1000 | PASSED | Late | Last done (min) | Credits (billable) | TB time | TB credit | TB total |
|-----------------|----------|-------|--------|------|-----------------|--------------------|---------|-----------|----------|
| agent-test-0    | 450      | 1000  | 2/2    | 0    | 2.3             | 43k                | +91     | +391      | +482     |
| codex-ruflo-low | 425      | 944   | 2/2    | 0    | 2.4             | 78k                | +58     | -102      | -44      |
| opencode-low    | 388      | 862   | 2/2    | 0    | 2.6             | 90k                | -28     | -267      | -295     |
| hermes-low      | 350      | 778   | 2/2    | 0    | 2.8             | 73k                | -120    | -21       | -141     |

<details><summary>Per item</summary>

| Item                                             | Type | Agent           | Min | Deadline | Billable | Points | Result        |
|--------------------------------------------------|------|-----------------|-----|----------|----------|--------|---------------|
| cobol-carddemo-16                                | BUG  | agent-test-0    | 0.7 | 12       | 20k      | 200    | PASSED        |
| cobol-carddemo-16                                | BUG  | codex-ruflo-low | 0.7 | 12       | 45k      | 200    | PASSED        |
| cobol-carddemo-16                                | BUG  | hermes-low      | 1.0 | 12       | 33k      | 200    | PASSED        |
| cobol-carddemo-16                                | BUG  | opencode-low    | 0.9 | 12       | 39k      | 200    | PASSED        |
| instance_element-hq__element-web-ee13e23b156fbad | PI   | agent-test-0    | 2.3 | -        | 23k      | 250    | PI rank x1.0  |
| instance_element-hq__element-web-ee13e23b156fbad | PI   | codex-ruflo-low | 2.4 | -        | 33k      | 225    | PI rank x0.9  |
| instance_element-hq__element-web-ee13e23b156fbad | PI   | hermes-low      | 2.8 | -        | 40k      | 150    | PI rank x0.6  |
| instance_element-hq__element-web-ee13e23b156fbad | PI   | opencode-low    | 2.6 | -        | 51k      | 188    | PI rank x0.75 |

</details>

### By item type

| Agent           | BUG               | CR | TASK | PI                |
|-----------------|-------------------|----|------|-------------------|
| agent-test-0    | 1/1 (100%) · 1000 | -  | -    | 1/1 (100%) · 1000 |
| codex-ruflo-low | 1/1 (100%) · 1000 | -  | -    | 1/1 (100%) · 1000 |
| hermes-low      | 1/1 (100%) · 1000 | -  | -    | 1/1 (100%) · 1000 |
| opencode-low    | 1/1 (100%) · 1000 | -  | -    | 1/1 (100%) · 1000 |

### Speed and cost

Billable tokens = uncached input + output (what drives Codex credits).

| Agent           | Resolved   | Score | Median min | P90 min | 1st edit min | Timeouts | Tokens/item | Billable/item | Billable/resolved | USD/resolved | Pts/min | Pts/1M tok | MVS green→failed | Cmds/item |
|-----------------|------------|-------|------------|---------|--------------|----------|-------------|---------------|-------------------|--------------|---------|------------|------------------|-----------|
| agent-test-0    | 2/2 (100%) | 1000  | 1.2        | 1.9     | -            | 0        | 245k        | 22k           | 22k               | -            | 181     | 10402      | 0%               | 8         |
| codex-ruflo-low | 2/2 (100%) | 1000  | 1.3        | 2.0     | 0.5          | 0        | 299k        | 39k           | 39k               | -            | 173     | 5750       | 0%               | 9         |
| hermes-low      | 2/2 (100%) | 1000  | 1.7        | 2.4     | -            | 0        | 324k        | 36k           | 36k               | -            | 136     | 6206       | 0%               | 0         |
| opencode-low    | 2/2 (100%) | 1000  | 1.5        | 2.2     | -            | 0        | 213k        | 45k           | 45k               | -            | 147     | 5003       | 0%               | 2         |

### Median minutes · billable tokens per item, by type

| Agent           | BUG           | CR | TASK | PI            |
|-----------------|---------------|----|------|---------------|
| agent-test-0    | 0.6 min · 20k | -  | -    | 1.9 min · 23k |
| codex-ruflo-low | 0.6 min · 45k | -  | -    | 2.0 min · 33k |
| hermes-low      | 0.9 min · 33k | -  | -    | 2.4 min · 40k |
| opencode-low    | 0.9 min · 39k | -  | -    | 2.2 min · 51k |

### Routing advice

Best = most expected points per agent-minute; frugal = fewest billable tokens within 10 points of the best resolve rate.

| Type | Best | Frugal | Why                                       |
|------|------|--------|-------------------------------------------|
| BUG  | -    | -      | not enough data (need ≥2 items per agent) |
| CR   | -    | -      | not enough data (need ≥2 items per agent) |
| TASK | -    | -      | not enough data (need ≥2 items per agent) |
| PI   | -    | -      | not enough data (need ≥2 items per agent) |
