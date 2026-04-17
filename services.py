"""I have written this file for handling the analysis logic and the summary generation at the same time we can get insights as well."""

import csv
import logging
import os
from typing import Any

from utils import (
    classify_label,
    compute_acos,
    compute_conversion_rate,
    compute_cpc,
    compute_ctr,
    compute_roas,
    parse_budget,
    safe_float,
    safe_int,
)

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
INPUT_CSV = os.path.join(DATA_DIR, "amazon_ads_data.csv")
OUTPUT_CSV = os.path.join(DATA_DIR, "campaigns_analyzed.csv")

REQUIRED_COLUMNS = {
    "Campaigns",
    "Budget",
    "Impressions",
    "Clicks",
    "Spend",
    "Orders",
    "Sales",
}


def _read_rows(path: str) -> list[dict]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"CSV not found at {path}")
    # utf-8-sig strips BOM; errors='replace' keeps bad bytes from crashing.
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        headers = set(reader.fieldnames or [])
        missing = REQUIRED_COLUMNS - headers
        if missing:
            raise ValueError(f"CSV missing required columns: {sorted(missing)}")
        return list(reader)


def analyze_campaigns() -> list[dict[str, Any]]:
    """Load the CSV, clean rows, compute metrics, label, persist, return JSON list."""
    raw_rows = _read_rows(INPUT_CSV)
    analyzed: list[dict[str, Any]] = []
    skipped = 0

    for idx, row in enumerate(raw_rows, start=2):  
        name = (row.get("Campaigns") or "").strip()
        if not name:
            logger.warning("Row %d skipped: missing campaign name", idx)
            skipped += 1
            continue

        budget = parse_budget(row.get("Budget"))
        impressions = safe_int(row.get("Impressions"))
        clicks = safe_int(row.get("Clicks"))
        spend = safe_float(row.get("Spend"))
        orders = safe_int(row.get("Orders"))
        sales = safe_float(row.get("Sales"))

        if any(v < 0 for v in (budget, impressions, clicks, spend, orders, sales)):
            logger.warning("Row %d (%s) skipped: negative value", idx, name)
            skipped += 1
            continue

        ctr = compute_ctr(clicks, impressions)
        cpc = compute_cpc(spend, clicks)
        conv_rate = compute_conversion_rate(orders, clicks)
        roas = compute_roas(sales, spend)
        acos = compute_acos(spend, sales)
        label = classify_label(roas)

        analyzed.append({
            "campaign": name,
            "budget": budget,
            "impressions": impressions,
            "clicks": clicks,
            "spend": round(spend, 4),
            "orders": orders,
            "sales": round(sales, 4),
            "ctr": ctr,
            "cpc": cpc,
            "conversion_rate": conv_rate,
            "roas": roas,
            "acos": acos,
            "label": label,
        })

    logger.info("Analyzed %d campaigns, skipped %d", len(analyzed), skipped)
    _write_analyzed(analyzed)
    return analyzed


def _write_analyzed(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_summary(campaigns: list[dict[str, Any]]) -> dict[str, Any]:
    if not campaigns:
        return {
            "total_spend": 0.0,
            "total_sales": 0.0,
            "overall_roas": 0.0,
            "best_campaign": None,
            "worst_campaign": None,
            "label_breakdown": {"Scale": 0, "Optimize": 0, "Pause": 0},
            "wasted_spend_pct": 0.0,
        }

    total_spend = round(sum(c["spend"] for c in campaigns), 2)
    total_sales = round(sum(c["sales"] for c in campaigns), 2)
    overall_roas = round(total_sales / total_spend, 4) if total_spend else 0.0

    
    active = [c for c in campaigns if c["spend"] > 0]
    if active:
        best = max(active, key=lambda c: c["roas"])
        worst = min(active, key=lambda c: c["roas"])
        best_campaign = {"name": best["campaign"], "roas": best["roas"]}
        worst_campaign = {"name": worst["campaign"], "roas": worst["roas"]}
    else:
        best_campaign = None
        worst_campaign = None

    label_breakdown = {"Scale": 0, "Optimize": 0, "Pause": 0}
    for c in campaigns:
        label_breakdown[c["label"]] = label_breakdown.get(c["label"], 0) + 1

    pause_spend = sum(c["spend"] for c in campaigns if c["label"] == "Pause")
    wasted_spend_pct = round(pause_spend / total_spend * 100, 2) if total_spend else 0.0

    return {
        "total_spend": total_spend,
        "total_sales": total_sales,
        "overall_roas": overall_roas,
        "best_campaign": best_campaign,
        "worst_campaign": worst_campaign,
        "label_breakdown": label_breakdown,
        "wasted_spend_pct": wasted_spend_pct,
    }



ACOS_HIGH = 80.0      
CTR_LOW = 0.3        


def build_insights(campaigns: list[dict[str, Any]]) -> dict[str, Any]:
    """Flag problem campaigns and recommend an action for each.

    Recommendation logic (applied in priority order so the most severe wins):
      1. Spend > 0 & Orders = 0  -> Pause        (pure budget drain)
      2. ROAS = 0 & budget > 0   -> Pause        (active budget, no return)
      3. ACOS > 80%              -> Reduce Budget(profitable but inefficient)
      4. CTR < 0.3%              -> Review Creative (impressions without clicks)
    """
    flagged: list[dict[str, Any]] = []
    wasted_spend_total = 0.0

    for c in campaigns:
        issues: list[dict[str, Any]] = []

        if c["spend"] > 0 and c["orders"] == 0:
            issues.append({
                "issue": "High spend with zero conversions",
                "metric_value": f"Spend: \u20b9{c['spend']:.2f}, Orders: 0",
                "recommendation": "Pause",
                "reason": "No ROI being generated. Pausing stops budget drain immediately.",
                "priority": 1,
            })

        if c["roas"] == 0 and c["budget"] > 0 and c["spend"] == 0 and c["impressions"] == 0:
           
            issues.append({
                "issue": "Active budget but no ad delivery",
                "metric_value": f"Budget: \u20b9{c['budget']:.2f}, Impressions: 0",
                "recommendation": "Check Targeting",
                "reason": "Budget is live but ads aren't serving. Bids or targeting likely too restrictive.",
                "priority": 2,
            })

        if c["acos"] > ACOS_HIGH and c["sales"] > 0:
            issues.append({
                "issue": f"ACOS above {ACOS_HIGH:.0f}% threshold",
                "metric_value": f"ACOS: {c['acos']:.2f}%",
                "recommendation": "Reduce Budget",
                "reason": "Ad spend is eating most of the revenue. Lower bids or tighten keywords.",
                "priority": 3,
            })

        if c["impressions"] > 0 and c["ctr"] < CTR_LOW:
            issues.append({
                "issue": f"CTR below threshold ({c['ctr']:.2f}%)",
                "metric_value": f"CTR: {c['ctr']:.2f}%",
                "recommendation": "Review Creative",
                "reason": "Ad is showing but not compelling clicks. Likely a creative or relevance issue.",
                "priority": 4,
            })

        if issues:
            # Surface the highest-priority issue for the flagged entry.
            primary = min(issues, key=lambda i: i["priority"])
            flagged.append({
                "campaign_name": c["campaign"],
                "issue": primary["issue"],
                "metric_value": primary["metric_value"],
                "recommendation": primary["recommendation"],
                "reason": primary["reason"],
            })
            if primary["recommendation"] == "Pause":
                wasted_spend_total += c["spend"]

    summary_text = (
        f"{len(flagged)} campaigns need attention. "
        f"Estimated wasted spend: \u20b9{wasted_spend_total:.2f}"
    )

    return {
        "flagged_campaigns": flagged,
        "total_flagged": len(flagged),
        "summary": summary_text,
    }


def build_recommendations(
    campaigns: list[dict[str, Any]],
    summary: dict[str, Any],
) -> dict[str, Any]:
    """Top-3 ranked actions a seller should take today, with estimated impact.

    Impact is modelled as the spend recoverable (for pauses) or the sales lift
    from bringing a Scale-candidate to more budget. Everything is rounded and
    labelled so the output stays useful even when input numbers are small.
    """
    actions: list[dict[str, Any]] = []

    drains = [c for c in campaigns if c["spend"] > 0 and c["orders"] == 0]
    drains.sort(key=lambda c: c["spend"], reverse=True)
    for c in drains[:3]:
        actions.append({
            "action": "Pause",
            "campaign": c["campaign"],
            "reason": "Spend with zero conversions — pure drain.",
            "estimated_impact": f"Save \u20b9{c['spend']:.2f} immediately",
            "impact_value": c["spend"],
        })

    
    scalers = [c for c in campaigns if c["label"] == "Scale" and c["spend"] > 0]
    scalers.sort(key=lambda c: c["roas"], reverse=True)
    for c in scalers[:3]:
        projected_lift = round(c["sales"] * 0.5, 2)  
        actions.append({
            "action": "Increase Budget",
            "campaign": c["campaign"],
            "reason": f"ROAS {c['roas']} — strong performer, underfunded.",
            "estimated_impact": f"+\u20b9{projected_lift:.2f} sales at 50% budget increase",
            "impact_value": projected_lift,
        })

    
    acos_fixers = [
        c for c in campaigns
        if c["acos"] > ACOS_HIGH and c["sales"] > 0 and c["orders"] > 0
    ]
    acos_fixers.sort(key=lambda c: c["acos"], reverse=True)
    for c in acos_fixers[:3]:
       
        target_spend = c["sales"] * 0.8
        recoverable = max(0.0, round(c["spend"] - target_spend, 2))
        actions.append({
            "action": "Reduce Budget",
            "campaign": c["campaign"],
            "reason": f"ACOS {c['acos']}% — inefficient but converting.",
            "estimated_impact": f"Recover ~\u20b9{recoverable:.2f} with tighter bids",
            "impact_value": recoverable,
        })

    actions.sort(key=lambda a: a["impact_value"], reverse=True)
    top_three = actions[:3]

    return {
        "top_actions": top_three,
        "total_candidates_evaluated": len(actions),
        "note": "Ranked by estimated rupee impact. Pause actions realize savings immediately; scale/reduce actions are projections.",
    }
