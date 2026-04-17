# Walkthrough — How to Explain Every Step

A complete narration of what I built, in the order I built it, with the reasoning behind every decision and answers to the questions that are likely to come up in the discussion call.

---

## 1. How I approached the problem

Before touching code I read the PDF end to end and listed the deliverables:

1. Fix 3 bugs in a starter `utils.py`.
2. Build `/analyze` — read CSV, clean, compute metrics, label, save output CSV.
3. Build `/summary` — account-level rollup.
4. Build `/insights` — flag problem campaigns with recommendations.
5. Build one bonus endpoint.
6. Respect the expected folder structure (`main.py`, `services.py`, `utils.py`, `models.py`, `data/`).

I locked the architecture before writing code:

- **`utils.py`** — pure helpers: parsing + safe math. No I/O, no business rules.
- **`services.py`** — business logic: loading the CSV, computing per-row results, account rollup, insight rules, recommendation ranking.
- **`models.py`** — Pydantic response models so every endpoint is type-checked and self-documenting in Swagger.
- **`main.py`** — FastAPI only. Routes delegate, no logic.

This separation is what the PDF explicitly says it evaluates ("Separation of concerns is part of what we evaluate"), so I treated it as a hard rule.

---

## 2. Step 0 — Fixing the three bugs

Starter code:

```python
def parse_budget(budget_str):
    return float(budget_str.replace("\u20b9", ""))

def compute_acos(spend, sales):
    return round(spend / sales, 4)

def compute_ctr(clicks, impressions):
    ctr = clicks / impressions * 100
    return ctr
```

### Bug 1 — `parse_budget`
**What breaks:** `budget_str.replace(...)` throws `AttributeError` if `budget_str is None`. `float("")` throws `ValueError` for empty strings. `float("")` also breaks if the cell is literally just `₹` because after stripping, nothing is left.

**How I found it:** the comment told me where to look, but I also ran through mental test cases — `None`, `""`, `"₹"`, `"₹100.00"`.

**Fix:** I didn't just handle the three listed cases — I used a regex `re.sub(r"[^\d.\-]", "", str(budget_str))` so the function tolerates **anything** that isn't a digit, dot, or minus. This future-proofs against other currency symbols, whitespace, commas like `"₹1,200.00"`, or mojibake like `"â¹100.00"` (which is what the raw CSV actually contains — more on that in §5). On an empty/degenerate result I return `0.0` instead of raising. I also wrapped `float()` in a try/except as a final safety net.

### Bug 2 — `compute_acos`
**What breaks:** `ZeroDivisionError` when `sales == 0`. Also — and this is the subtle one — the spec's formula table says `ACOS = Spend / Sales × 100`, but the starter returns `spend / sales` (no `× 100`). So the starter function also had a silent **logic bug**. I fixed both: guard `sales == 0` → return `0.0`, and multiply by 100 so the output is a percentage.

**Why I return 0 and not `None` / `inf`:** the PDF is explicit — "Division by zero must never return Infinity, NaN, or throw an exception." `0.0` is the documented contract for every metric's zero case.

### Bug 3 — `compute_ctr`
**Crash bug:** `ZeroDivisionError` when `impressions == 0` (paused campaigns have 0 impressions).

**Logic bug:** the function returned an unrounded float while the other metrics round. So CTR would come out looking like `0.4761904761904762` while ACOS and ROAS came out rounded. I rounded to 4 decimals for consistency across every metric.

**Fix:** guard `impressions == 0` → `0.0`; round the result to 4 decimals.

---

## 3. Rewriting `utils.py` into a full helper library

Once the bugs were fixed I realised `utils.py` needed more than just the three starter functions — every endpoint needs safe versions of all five metrics. So I added:

- `safe_float(value, default=0.0)` — wraps `float()` with a try/except and also filters out `NaN`/`inf`. This is the first line of defence when reading CSV cells — the file might contain anything.
- `safe_int(value, default=0)` — calls `safe_float` and casts.
- `compute_cpc(spend, clicks)` — `spend / clicks`, returns 0 when clicks is 0.
- `compute_conversion_rate(orders, clicks)` — `orders / clicks × 100`, returns 0 when clicks is 0.
- `compute_roas(sales, spend)` — `sales / spend`, returns 0 when spend is 0.
- `classify_label(roas)` — the label rule straight from the PDF: `>3 → Scale`, `1–3 → Optimize`, `<1 → Pause`.

**Pattern across all metric functions:** single-line guard `if not denominator: return 0.0`, then the formula, then `round(..., 4)`. Same shape every time — easy to read, easy to verify against the spec table.

I use `if not impressions` rather than `if impressions == 0` so it also handles `None` safely if anything upstream ever passes one.

---

## 4. `services.py` — the business logic layer

### `_read_rows(path)`
- Checks the file exists (raises `FileNotFoundError` which the route converts to HTTP 404).
- Opens with `encoding="utf-8-sig"` — the `-sig` variant strips a UTF-8 BOM if the file has one (Excel commonly adds it).
- `errors="replace"` means a single bad byte won't crash the whole read.
- Validates that all 7 required columns are present; missing columns raise `ValueError` → HTTP 400.

### `analyze_campaigns()`
The heart of `/analyze`. For every row:
1. **Clean** — `parse_budget` for budget, `safe_int` for counts, `safe_float` for money columns.
2. **Validate** — if any numeric value is negative (impossible in real data) the row is logged and skipped.
3. **Compute** all five metrics using the helpers from `utils.py`.
4. **Label** using `classify_label`.
5. **Append** to the output list.

I used `enumerate(..., start=2)` for logging so skipped-row messages reference the actual line in the CSV (1 for header + 1-indexed rows).

Malformed rows are **logged and skipped**, never raised — that's the PDF's explicit rule ("log what was skipped, do not crash").

At the end I write `data/campaigns_analyzed.csv` using the stdlib `csv.DictWriter`. I used stdlib on purpose — pandas is in `requirements.txt` but isn't needed for what is fundamentally a row-by-row transform, and stdlib keeps the dependency surface small.

### `build_summary(campaigns)`
- `total_spend` / `total_sales` are plain sums, rounded to 2 decimals (money).
- `overall_roas = total_sales / total_spend`, guarded for zero.
- **Best/worst campaign:** I explicitly filter to `c["spend"] > 0` first. Otherwise the dataset's ~170 zero-activity rows (all with `ROAS = 0`) would tie for "worst" and `min()` would pick one arbitrarily. That's a misleading answer. The right "worst" is the worst-performing *active* campaign.
- **`label_breakdown`:** pre-seeded with `{"Scale": 0, "Optimize": 0, "Pause": 0}` so the response shape is always stable even if a label has zero campaigns.
- **`wasted_spend_pct`:** the sum of spend on Pause-labelled campaigns divided by total spend, × 100. The PDF flags this as a "real DataFuel insight" — so I made sure it's computed correctly and rounded to 2 decimals.

Handles the empty-campaigns case with an early return that still respects the response shape — `best_campaign`/`worst_campaign` are `Optional` in the Pydantic model.

### `build_insights(campaigns)`
The four flag rules from the PDF, implemented with **priority ordering** so only the most severe recommendation surfaces per campaign:

| Priority | Trigger | Recommendation |
|---|---|---|
| 1 | Spend > 0 AND Orders = 0 | **Pause** |
| 2 | Budget > 0, Impressions = 0, Spend = 0 | **Check Targeting** |
| 3 | ACOS > 80% with Sales > 0 | **Reduce Budget** |
| 4 | Impressions > 0 AND CTR < 0.3% | **Review Creative** |

Why priorities instead of listing every issue?
- A campaign that has *both* ACOS > 80% and CTR < 0.3% should be acted on by reducing budget, not by "reviewing the creative" — the bigger problem always wins.
- In the response each flagged campaign has exactly one issue/recommendation, which matches the example shape in the PDF.

I collect all matching issues per campaign and use `min(issues, key=priority)` to pick the most severe. Keeping all issues before picking makes the rule set easy to extend — to add a new flag you just add another `if` block.

The `summary` string mirrors the example in the PDF: `"{N} campaigns need attention. Estimated wasted spend: ₹{X}"`.

I also rewrote rule 2 slightly from the PDF: "ROAS = 0 but budget active" alone was ambiguous (every Pause-labelled row has ROAS = 0). I narrowed it to "budget > 0, no impressions, no spend" — that's the actionable case where the budget is allocated but the ad isn't even showing. A recommendation of "Check Targeting" makes sense there; "Pause" does not, because nothing is being spent yet.

### `build_recommendations(campaigns, summary)` — the bonus
I chose a `/recommendations` endpoint because it answers the question a seller actually has: *"What should I do right now?"* `/insights` surfaces 92 flagged campaigns on this dataset — that's noise. This endpoint ranks the top 3 actions by **estimated rupee impact** and returns one line each.

Three candidate buckets, each sorted by how impactful the action would be:

1. **Pause biggest drains** — campaigns with spend > 0 and orders = 0, sorted by spend descending. Impact = the spend itself (immediate savings).
2. **Increase budget on Scale winners** — ROAS > 3 campaigns with actual spend, sorted by ROAS. Impact = `sales × 0.5` (modelling a 50% budget bump → roughly 50% more sales at the same ROAS; a conservative projection).
3. **Reduce budget on high-ACOS converters** — ACOS > 80% with sales > 0 and orders > 0. Impact = `spend − sales × 0.8` — i.e. how much spend you recover by pulling ACOS down to the 80% threshold.

All three buckets are concatenated and then sorted globally by `impact_value` — so a large drain beats a medium scaler beats a small fix. Top 3 returned.

Why this design:
- **Grounded in real money.** The impact numbers are in rupees, not abstract scores.
- **Actionable.** Each action names a campaign and an action verb ("Pause", "Increase Budget", "Reduce Budget").
- **Honest about uncertainty.** Pause impacts are exact (you save what you were spending); scale and reduce are projections, and the `note` field says so.

---

## 5. Data-quality gotchas I handled

- **The `₹` comes through as `â¹` in the raw CSV.** Classic UTF-8-written-then-read-as-latin-1 mojibake. I didn't try to re-decode — the `parse_budget` regex strips every non-numeric character, so it doesn't matter what encoding artifact shows up there.
- **~170 of the 348 rows have all zeros** (paused campaigns). Every metric zero-guard in `utils.py` was necessary — not hypothetical.
- **One row (Campaign 175)** has `31` impressions and `3` clicks but `0` spend / `0` sales. That's an unusual shape. My code handles it: CTR computes (~9.68%), CPC is 0 (clicks > 0 but spend = 0), ROAS/ACOS are 0, label is "Pause". Nothing crashes.
- **BOMs and stray bytes.** `utf-8-sig` + `errors="replace"` covers both.
- **Missing/empty columns.** Validated in `_read_rows` → clean 400 error, not a traceback.

---

## 6. `models.py` — response shapes

Every endpoint returns a Pydantic model so:
1. FastAPI auto-generates accurate OpenAPI docs at `/docs`.
2. Invalid response data raises at serialisation time (catches bugs early).
3. The response contract is declarative — anyone reading `models.py` sees the exact shape.

`best_campaign` / `worst_campaign` are `Optional[CampaignRef]` to handle the empty-dataset case cleanly. `LabelBreakdown` is its own model with the three fixed fields, not a free-form dict, so missing-label bugs would surface immediately.

---

## 7. `main.py` — routes only

Four routes, each one-liner delegation to a service function:

- `GET /` — index listing the endpoints.
- `GET /analyze` — returns the full analyzed list (and writes the output CSV as a side effect).
- `GET /summary` — calls `analyze_campaigns()` then `build_summary()`.
- `GET /insights` — calls `analyze_campaigns()` then `build_insights()`.
- `GET /recommendations` — calls `analyze_campaigns()`, `build_summary()`, `build_recommendations()`.

All four wrap `analyze_campaigns()` through a `_load()` helper that converts `FileNotFoundError → 404` and `ValueError → 400`. Routes never see a raw traceback.

**On re-reading the CSV per request:** fine for this assessment — file is 348 rows, read takes milliseconds. In production I'd cache with `functools.lru_cache` invalidated on file mtime, or move to a proper data layer — but that's over-engineering for a 2-hour task, and I'd rather ship clean code than premature caching.

---

## 8. Answers to questions the interviewer is likely to ask

**Q: Why stdlib `csv` and not pandas?**
A: The transform is row-by-row with no vectorised math. Pandas adds a 30MB dep and a `DataFrame → dict` conversion step for zero benefit here. I used pandas only if it earned its weight, which in this case it didn't.

**Q: Why regex-strip in `parse_budget` instead of just `.replace("₹", "")`?**
A: The raw file has mojibake (`â¹`), and real-world Amazon exports can include commas (`₹1,200.00`), whitespace, or NBSPs. The regex handles all of these with one line. It's also resilient to future locale changes.

**Q: What if a campaign has `clicks > impressions`?**
A: Nothing in the code explicitly blocks it — CTR would be > 100%. I didn't add a validator because the real source of truth is Amazon's reporting; if their data is internally inconsistent, silently rejecting it is worse than surfacing it. I'd log it at WARN level in a production version.

**Q: Why prioritise insights instead of returning all matching issues?**
A: The PDF example shows one issue per flagged campaign. A seller acting on recommendations needs a single verb, not a list. Adding secondary issues would be noise.

**Q: Why filter `spend > 0` in best/worst?**
A: Otherwise the 170+ zero-activity rows all tie at ROAS=0 for worst, and `min()` picks an arbitrary one. That's a garbage answer in a real dashboard. "Worst *active* campaign" is the actionable answer.

**Q: Why `if not denominator` instead of `if denominator == 0`?**
A: Catches `None` as well as `0`. Defensive against upstream changes. Costs nothing.

**Q: Why do you round everything to 4 decimals?**
A: Matches the starter's `round(spend / sales, 4)` pattern, keeps metrics visually consistent, and 4 decimals is plenty for percentages and ratios.

**Q: What's `wasted_spend_pct` telling me?**
A: Of every rupee spent, what share went to campaigns that are losing money (ROAS < 1). On this dataset it's 3.84% — not catastrophic, but directly actionable: pausing Pause-labelled campaigns would free up ~₹95 immediately without touching profitable ones.

**Q: What would you add next if you had another hour?**
A: (1) unit tests for `utils.py` — edge cases are where bugs hide. (2) A `/campaigns/{name}` detail endpoint. (3) A trend-simulation endpoint: "if ACOS drops 10% across the board, what does overall ROAS become?"

---

## 9. How to run & demo

```bash
venv/Scripts/python.exe -m pip install -r requirements.txt
venv/Scripts/uvicorn.exe main:app --reload
```

Open http://127.0.0.1:8000/docs — every endpoint is interactive, every response model is documented. During the demo I'd hit them in this order:

1. `/analyze` — show the computed metrics, point to `data/campaigns_analyzed.csv` being created.
2. `/summary` — highlight `wasted_spend_pct`, `best_campaign`, `worst_campaign`.
3. `/insights` — show the priority-ordered flags.
4. `/recommendations` — the bonus endpoint, show top 3 actions ranked by rupee impact.

---

## 10. The mental framing I'd open the call with

> "The PDF said this tests how I think, build, and debug — not what I memorised. So I treated each step as a design decision with a trade-off: stdlib vs pandas, crash vs log-and-skip, one issue per campaign vs many, filtered vs unfiltered best/worst. I'll walk through each file in the order the data flows — utils, services, models, main — and explain every choice."
