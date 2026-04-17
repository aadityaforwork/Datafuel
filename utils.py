"""Data cleaning and metric helpers.

The three starter functions (parse_budget, compute_acos, compute_ctr) had
intentional bugs. Fixes are explained in the README under "Bug Fixes".
"""

import re


def parse_budget(budget_str):
    if budget_str is None:
        return 0.0
    cleaned = re.sub(r"[^\d.\-]", "", str(budget_str))
    if not cleaned or cleaned in {".", "-", "-."}:
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def safe_float(value, default=0.0):
    if value is None:
        return default
    try:
        result = float(value)
    except (ValueError, TypeError):
        return default
    if result != result or result in (float("inf"), float("-inf")):
        return default
    return result


def safe_int(value, default=0):
    return int(safe_float(value, default))


def compute_ctr(clicks, impressions):
    if not impressions:
        return 0.0
    return round(clicks / impressions * 100, 4)


def compute_cpc(spend, clicks):
    if not clicks:
        return 0.0
    return round(spend / clicks, 4)


def compute_conversion_rate(orders, clicks):
    if not clicks:
        return 0.0
    return round(orders / clicks * 100, 4)


def compute_roas(sales, spend):
    if not spend:
        return 0.0
    return round(sales / spend, 4)


def compute_acos(spend, sales):
    if not sales:
        return 0.0
    return round(spend / sales * 100, 4)


def classify_label(roas):
    if roas > 3:
        return "Scale"
    if roas >= 1:
        return "Optimize"
    return "Pause"
