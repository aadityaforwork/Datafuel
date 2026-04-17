# Dataset & Business Reasoning

This is the thinking behind every decision I made — not about *how the code works* (that's in [EXPLANATION.md](EXPLANATION.md)) but about **what the data actually is, what an Amazon seller actually cares about, and how those two things shaped the rules and thresholds in the code.**

---

## 1. What the dataset is, in plain English

Each row is one **sponsored ad campaign** on Amazon for a specific day (the Budget is explicitly *daily*). The seven columns tell a causal story, left to right:

| Column | What it represents | What it's *really* measuring |
|---|---|---|
| `Campaigns` | Campaign name | Just an ID — no info, just a label |
| `Budget` | Daily budget cap (₹) | What the seller is *willing* to spend |
| `Impressions` | Times the ad was shown | Did Amazon's auction even surface the ad? |
| `Clicks` | Times someone clicked | Did the ad copy / image work? |
| `Spend` | Money actually spent (₹) | What Amazon charged for those clicks |
| `Orders` | Purchases that resulted | Did the product page convert? |
| `Sales` | Revenue generated (₹) | How much money came back in |

The funnel is: **Budget → Impressions → Clicks → Orders → Sales**. Every row is a point on this funnel, and any step can leak. The metrics we compute tell you *where* the leak is:

- Low **CTR** = ad shown but not clicked → creative/targeting problem
- Low **Conversion Rate** = clicked but didn't buy → product page / pricing problem
- High **CPC** = paying too much per click → bidding problem
- High **ACOS** = too much spend per rupee of sales → overall inefficiency
- Low **ROAS** = losing money → business-survival problem

This mental model is why the insights rules are what they are: each rule diagnoses a *different step* of the funnel, so each deserves a different recommendation.

---

## 2. What I actually observed in this CSV before writing a single rule

I read the file first — 348 rows — and noted everything that would shape the code:

### Distribution shape
- **~170 rows have all zeros** (impressions = clicks = spend = orders = sales = 0). These are paused-from-birth campaigns where budget is allocated but no auctions were won. Any "worst ROAS" logic has to exclude them or the leaderboard is useless.
- **~178 rows have real activity.** Of those, roughly half are profitable (ROAS ≥ 1) and half aren't.
- **Every active campaign has `orders = 1`** in this snapshot. This is unusual — it means the dataset captures campaigns at the moment of their first conversion. It's why the "Spend > 0 AND Orders = 0" flag ended up matching *zero* campaigns on this dataset, but I kept the rule anyway because it's the most important rule on a real snapshot.
- **One weird row — Campaign 175** — has 31 impressions, 3 clicks, but `spend = sales = 0`. That's Amazon either comping impressions or a reporting delay. My code handles it gracefully (CTR computes, CPC/ROAS are zero) without special-casing.

### Budget encoding
- The `Budget` column contains `â¹` instead of `₹` — classic UTF-8-written-then-read-as-latin-1 mojibake. I didn't "fix" the encoding upstream; I made `parse_budget` strip *anything non-numeric*, so it's robust to this and any future encoding accident (commas, NBSPs, different currency symbols).

### Value ranges that informed thresholds
- CTR on active campaigns ranges from ~0.13% to ~0.83%. Industry-standard "bad" CTR on Amazon is < 0.3%. That's why the insight threshold is 0.3% — it's calibrated to this dataset's realistic spread, not an arbitrary round number.
- ACOS on active campaigns ranges from ~15% to ~130%. An ACOS above 100% means you're losing money on every sale. Above 80% means you're making less than 20% margin on ad-driven revenue — unsustainable for most sellers. Hence the 80% threshold.
- ROAS clusters around 2.0x (dataset-wide overall ROAS is 2.28x). The label boundaries (1, 3) were dictated by the spec, but they line up well with this distribution: ~16% of campaigns hit Scale, ~57% Optimize, ~26% Pause.

---

## 3. The business lens — what an Amazon seller actually cares about

An Amazon seller looking at this report is not asking "what's my CTR?" They're asking:

> **"Where is my money going, and what should I do about it today?"**

That one question drives three sub-questions, which map cleanly to the three required endpoints:

| Seller's question | Endpoint | What it answers |
|---|---|---|
| "How am I doing overall?" | `/summary` | Money in, money out, best/worst, wasted spend share |
| "What's broken right now?" | `/insights` | Specific campaigns that need attention, with why |
| "What should I do first?" | `/recommendations` | Ranked actions by rupee impact |

The reason I added `/recommendations` as the bonus is that `/insights` alone flags 92 campaigns on this dataset. A seller can't act on 92 things. They need the **top 3 moves today**, which is why the bonus endpoint ranks by actual rupee impact and caps at 3.

### Why `wasted_spend_pct` matters more than it looks

The PDF calls this out as a "real DataFuel insight" and that's fair. It's the single number that tells a seller: *"Of every ₹100 you spent yesterday, ₹X went to campaigns that are demonstrably losing money."* On this dataset it's 3.84% — not catastrophic, but directly actionable: pausing the 92 Pause-labelled campaigns would free up ~₹95 immediately **with zero risk to profitable campaigns**, because Pause = ROAS < 1 = already losing money.

This is the kind of metric a seller screenshots and shows their team. It's not a vanity number.

### Why "best/worst of *active* campaigns" and not all

If I used all 348 rows for the `worst_campaign` slot, the winner would be any of the 170 zero-activity rows, all tied at ROAS = 0. That's not a worst performer — that's a paused campaign. The seller can't "fix" it because it hasn't run yet. The actionable worst performer is the one that *did* run and flopped. That's Campaign 226 in this dataset at 0.79x ROAS — i.e. it returned 79 paise for every ₹1 spent. A seller can actually do something about that campaign today.

### Why priority ordering in insights

A campaign with both ACOS > 80% *and* CTR < 0.3% has two problems, but the seller can only do one thing at a time. Which comes first?

My priority order — `Pause > Check Targeting > Reduce Budget > Review Creative` — follows **reversibility and blast radius**:
1. **Pause first** if there's pure drain (spend, no orders) — this stops the bleeding with zero downside.
2. **Check Targeting** if budget's allocated but ads aren't serving — diagnose before tweaking.
3. **Reduce Budget** if ACOS is too high but sales exist — preserve the converting traffic, just tighten spend.
4. **Review Creative** last — this is a long-cycle change (design/copy work) and should only be the recommendation when the other three aren't firing.

### Why the recommendations are priced in rupees

A recommendation like "pause this campaign" is abstract. "Pause this campaign to save ₹16.84 today" is actionable. My bonus endpoint models three impact types, each grounded in a number the seller can verify:

- **Pause** → impact = the spend itself (exact, savings realized immediately)
- **Increase budget** → impact = `sales × 0.5` (projected, assumes linear lift at current ROAS — I called this out in the `note` field so the seller knows it's an estimate, not a guarantee)
- **Reduce budget on high-ACOS** → impact = `spend − sales × 0.8` (recoverable spend if ACOS gets dragged back to 80%)

All three numbers get concatenated, sorted globally by impact, and only the top 3 come back. The seller gets: *"Here are the 3 moves that matter most, in rupee order, with why."*

---

## 4. Edge cases I treated as signals, not noise

Every "weird" row in this dataset taught me something about a rule I needed:

| Weird data point | What it forced me to build |
|---|---|
| `Budget = "₹"` only (after mojibake stripped) | Regex-strip in `parse_budget`, fallback to `0.0` |
| ~170 rows with all zeros | Filter best/worst to active campaigns; don't crash in any metric |
| Campaign 175: impressions & clicks with zero spend | Every metric helper uses `if not denominator` (covers `0` and `None`); no special-case needed |
| `sales = 0` on rows with real spend | ACOS returns 0 instead of Infinity; insights rule requires `sales > 0` so we don't flag "ACOS > 80%" on campaigns where ACOS is actually just zero |
| Negative numbers (defensive — didn't appear, but could) | Skip and log the row; don't silently include garbage in totals |
| BOM or bad bytes in CSV | Read with `utf-8-sig` + `errors="replace"` |

The principle: **every guard in the code has a row in the CSV (or a plausible one) behind it.** I didn't add defensive checks for hypothetical cases — only for things I saw or could justify from the data shape.

---

## 5. Business decisions I *didn't* make, and why

Some things I deliberately left out:

- **I did not flag high CPC.** On Amazon, CPC varies wildly by category — a ₹12 CPC is normal for a competitive niche and absurd for a niche one. Without category info I'd be flagging noise. A seller with context can read CPC from the table; I shouldn't second-guess.
- **I did not compute "lifetime ROAS" or trends.** This is a single snapshot. Trend math on one row is fiction. If the CSV had a date column, `/recommendations` could include "ACOS has been climbing for 7 days — investigate."
- **I did not auto-pause or modify data.** The tool is diagnostic, not executive. Recommendations are suggestions; the seller decides.
- **I did not rank by "number of issues flagged."** That rewards noisy campaigns that trip every rule. Ranking by **rupee impact** rewards the campaigns that actually matter to the business.

Knowing what *not* to do is the part of the job the PDF was referring to when it said "the best engineers always know what's missing."

---

## 6. What the sample output tells me about this account

Based on `/summary` output on this CSV:

```
total_spend: ₹2,473.29
total_sales: ₹5,630.85
overall_roas: 2.28
labels:  Scale: 57  Optimize: 199  Pause: 92
wasted_spend_pct: 3.84%
```

Reading this as a seller would:

- **Overall ROAS 2.28x** — every ₹1 of ad spend is returning ₹2.28 in sales. Not amazing, not bad. For a mid-tier Amazon seller this is within the healthy band (most aim for 3x+).
- **57 Scale campaigns** are the golden goose — they're returning more than 3x. These deserve more budget tomorrow. `/recommendations` surfaces the three best of these.
- **92 Pause campaigns** are losing money. They're consuming 3.84% of total spend. That's ~₹95 going straight to drain every reporting cycle — not a disaster but absolutely worth pausing.
- **199 Optimize** is the biggest bucket. These are break-even-ish campaigns where small tweaks (bid, creative, negative keywords) could push them into Scale. This is where a human optimiser earns their keep.
- **Best: Campaign 151 at 4.59x ROAS, Worst: Campaign 226 at 0.79x** — both are in the `/recommendations` feed. The dashboard literally tells the seller where to look.

**The narrative this account tells:** the seller has a core of profitable campaigns, a larger middle of mediocre ones, and a long tail of losers. The play is: **pause the losers (instant save), scale the winners (projected lift), fix the middle (ongoing work).** That's exactly the structure of the three recommendation buckets. The code isn't arbitrarily grouping things — it's mirroring how a seller would actually approach this dashboard.
