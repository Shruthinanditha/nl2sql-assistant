# Evaluation: how accurate is the assistant?

**Result:** accuracy on 40 test questions went from **92.5% (baseline)** to an average of **96.7%** over three
runs of the final setup (95.0%, 97.5%, 97.5%). On 8 separate held-out questions it went from **87.5% to 100%**.
Every unsafe or unanswerable question was refused in every run (**100%**), and there were no false refusals.

All numbers below are real measurements (model `gemini-3.5-flash-lite`, free tier, temperature 0, 7 Nov 2026).
The raw results of every run are saved as JSON in [`eval/results/`](../eval/results/).

---

## 1. Method

### The question set: [`eval/questions.yaml`](../eval/questions.yaml)

40 questions, each with a **reference SQL query written and checked by hand**:

| Difficulty | Count | Examples of what is tested |
|---|---|---|
| easy | 10 | filters, counts, top-1, `GROUP BY`, status values |
| medium | 12 | joins, relative dates ("last month", "last 30 days"), `COUNT DISTINCT`, percentages, category revenue from `order_items` |
| hard | 10 | window functions (`LAG`, running total, `ROW_NUMBER`), anti-joins (`NOT EXISTS`), `HAVING COUNT(DISTINCT ...)`, NULL handling, share of total, ties |
| ambiguous | 2 | "Who are our best customers?", "How are sales doing this year?" (one reasonable reading is the reference) |
| unsafe / unanswerable | 6 | delete request, password hashes, hidden email column, profit (no cost data), prompt injection, `sqlite_master` |

Rules to keep it honest:
- None of the questions are in the prompt's few-shot examples.
- Improvements must be **general** rules or descriptions. They never contain answers copied from the test set.
- A separate **held-out set** ([`eval/holdout.yaml`](../eval/holdout.yaml), 8 questions) was written after studying the
  baseline failures but **before** changing anything. It tests the same skills in different wording. If the improvements
  only "taught to the test", the held-out score would not move.

### Scoring: execution accuracy

[`eval/run_eval.py`](../eval/run_eval.py) runs every question through the **real pipeline** (LLM → validator → read-only
database) and compares the **result rows** with the reference result, not the SQL text. Two different queries that return
the same answer are both correct. The comparison is relaxed in a fair way:

- column **names** are ignored (`revenue` vs `total_revenue`),
- **extra columns** are allowed (for example the AI also shows the city),
- row **order** is ignored unless it is part of the answer (rankings),
- numbers may differ by **0.01** (rounding).

Every reference column must appear in the result with the same values in the same rows, and the row count must match.
Unsafe questions are correct only if the assistant returns **no data** (status `cannot_answer` or `blocked`).

### Checking the checker

- **Unit tests** for the scorer: [`tests/test_eval_scoring.py`](../tests/test_eval_scoring.py).
- **Dry run** (`--dry-run`): a fake LLM answers every question with the *reference SQL itself*. That must score 100%.
  The first dry run scored 95%, and it found a real **validator bug**: `NOT EXISTS` was wrongly blocked (sqlglot represents
  `EXISTS` as a function node). After the fix, the dry run scored 100%.

### Cost

Each run of the main set uses about 40 requests and about 76,000–82,000 input tokens (~1,900–2,050 per question). The
whole Phase 8 used **256 requests and about 0.5 million input tokens: ₹0 on the free tier.** The script pauses 4 seconds
between questions to stay under the requests-per-minute limit.

---

## 2. Results: one change at a time

| # | Setup | Overall | Easy | Medium | Hard | Ambig. | Unsafe refused | Failed questions |
|---|---|---|---|---|---|---|---|---|
| 0 | **Baseline** (self-repair off) | **92.5%** | 10/10 | 12/12 | 7/10 | 2/2 | 6/6 | H2, H3, H10 |
| 1 | + self-repair on | 87.5% | 10/10 | 11/12 | 7/10 | 1/2 | 6/6 | M8, H2, H3, H10, A2 |
| 2 | + business rule: "counting orders includes every status" | 92.5% | 10/10 | 12/12 | 8/10 | 1/2 | 6/6 | H2, H10, A2 |
| 3 | + prompt rules: "which X → list the X items" and "previous-period comparisons start one period earlier; NULL is not 0" | 95.0% | 10/10 | 12/12 | 8/10 | 2/2 | 6/6 | H5, H10 |
| 3 | final setup, run 2 | 97.5% | 10/10 | 12/12 | 9/10 | 2/2 | 6/6 | H10 |
| 3 | final setup, run 3 | 97.5% | 10/10 | 12/12 | 9/10 | 2/2 | 6/6 | H10 |

**Final setup: mean 96.7% over 3 runs (min 95.0%, max 97.5%).** Answerable questions: 94.1% / 97.1% / 97.1%.

### Held-out set (8 questions never used to design the changes)

| Setup | Score | Failed |
|---|---|---|
| Baseline | 87.5% (7/8) | HO4: previous-period comparison |
| Final | **100% (8/8)** | – |

The held-out improvement shows the new rules are general, not tailored to the test questions.

### Latency and tokens

| | Baseline | Final (3 runs) |
|---|---|---|
| Average time per question | 4.16 s | 2.10 s / 1.29 s / 3.03 s |
| 90th percentile | 9.45 s | 3.58 s / 1.54 s / 6.28 s |
| Input tokens per run | 75,428 | 81,868 (+8.5%, the longer prompt) |

Latency varies mostly with Google's load. Slow answers are automatic retries after "overloaded" (503) responses. The
validator and database together take well under 0.05 s per question.

---

## 3. What each change taught us

### Baseline failures, diagnosed

| Question | What the AI did | Root cause |
|---|---|---|
| H3 "customers who placed an order in each of the last 3 months" | added `status = 'completed'` | over-applied the "revenue = completed orders" rule to plain order counting |
| H10 "which products share the most common price?" | returned the price and a count | described the products instead of listing them |
| H2 "monthly revenue + change vs previous month" | January's change = `0` | turned a missing value into a misleading zero, and ignored December of the previous year |

### Change 1: self-repair (no effect here, and that's a finding)

**Repairs used: 0.** Self-repair only triggers when SQL **fails** (a syntax error or an unknown column). Every baseline
failure was SQL that **ran fine but answered wrongly**, which repair cannot detect. It stays switched on because it helps
with real errors (proven in `tests/test_pipeline.py`), but it does not raise accuracy on this set.

The score still *dropped* to 87.5%, because M8 and A2 failed although nothing relevant had changed. That's
**run-to-run variance**: even at temperature 0 the model does not always write identical SQL. Lesson: **one run is not
enough to judge a change.** The final setup was therefore measured three times.

### Change 2: a clearer business rule (schema descriptions)

Added to [`data/schema_descriptions.yaml`](../data/schema_descriptions.yaml):
> Counting orders, or asking which customers placed / made an order, includes ALL statuses ... Only filter on status when
> the question mentions a status, revenue, sales, spending or units sold.

This fixed **H3 and M8**, both the same root cause, and broke nothing.

### Change 3: two general prompt rules

Added to rule 6 in [`src/prompts.py`](../src/prompts.py): "which X" questions return one row per X; previous-period
comparisons are calculated over data that includes the period before the range and filtered afterwards; a NULL is never
turned into 0 unless asked. This fixed **H2**, and on the held-out set it fixed **HO4** (same skill, different wording).

---

## 4. Remaining failures (honest notes)

- **H10** ("Which products share the most common price?"): the final setup now returns the product names, but packed into
  one comma-separated cell (`GROUP_CONCAT`) instead of one row per product. A human would probably accept this, but the
  scorer counts it as wrong. We did **not** loosen the scorer after seeing this answer.
- **H5** failed once (run 1 of the final setup): "more than a year ago" was read as "before 1 January this year". It passed
  in the other five runs, so it's variance. A rule for "more than N ago" could be added and measured next.
- **Ambiguous questions** depend on interpretation: A2 ("How are sales doing?") was answered with yearly totals instead of
  a monthly breakdown in two runs.

## 5. Corrections to the answer key (done openly)

Reviewing failures also means reviewing the **reference answers**. Two were debatable and were corrected. Saved runs
were then **re-scored without new API calls** (`--rescore`), so all numbers above use the corrected references:

- **H2**: the first reference left January's change empty. The better answer compares January with December of the
  previous year, which is in the data. The baseline answer (0 for January) is wrong under both versions, so the baseline
  score did not change.
- **HO1** ("orders of each customer from Pune"): the first reference used an inner join and dropped a Pune customer
  with 0 orders. The AI's `LEFT JOIN` answer was the better reading. The held-out baseline went from 75% to 87.5% after
  re-scoring.

## 6. Limitations of this evaluation

- 40 + 8 questions on one synthetic database. Real company schemas are larger and messier.
- The question writer (me) also designed the schema, so the questions may be easier than real users' questions.
- Variance: ±2.5 percentage points between identical runs. Differences smaller than that are not meaningful.
- Only one model was measured. Comparing `gemini-3.5-flash` (a slower "thinking" model) is a possible next experiment:
  `python eval/run_eval.py --model gemini-3.5-flash --label flash --delay 6`.

## 7. Reproduce

```powershell
python eval/run_eval.py --dry-run --label dryrun                     # checks the scorer (no API)
python eval/run_eval.py --label baseline --no-repair                 # ~40 requests, ~3 min
python eval/run_eval.py --questions eval/holdout.yaml --label holdout
python eval/run_eval.py --rescore eval/results/<file>.json           # re-check a saved run (no API)
```

Results depend on the day you run them (relative dates), on the model version, and on run-to-run variance.
