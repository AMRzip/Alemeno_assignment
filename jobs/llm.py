import json
import time
from collections import Counter, defaultdict
from decimal import Decimal

from django.conf import settings


CATEGORIES = [
    "Food",
    "Shopping",
    "Travel",
    "Transport",
    "Utilities",
    "Cash Withdrawal",
    "Entertainment",
    "Other",
]


def _gemini_model():
    api_key = getattr(settings, "GEMINI_API_KEY", "")
    if not api_key or api_key == "your-gemini-key-here":
        return None

    import google.generativeai as genai

    genai.configure(api_key=api_key)
    return genai.GenerativeModel("gemini-1.5-flash")


def _json_from_response(text):
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.removeprefix("json").strip()
    return json.loads(cleaned)


def _retry(callable_):
    last_error = None
    for attempt in range(3):
        try:
            return callable_()
        except Exception as exc:
            last_error = exc
            time.sleep(2**attempt)
    raise last_error


def classify_transactions(rows):
    if not rows:
        return {}, None, False

    model = _gemini_model()
    if model:
        prompt = {
            "instruction": (
                "Classify each transaction into one category: "
                + ", ".join(CATEGORIES)
                + ". Return only JSON as {\"items\":[{\"index\":0,\"category\":\"Food\"}]}."
            ),
            "transactions": rows,
        }

        try:
            response = _retry(lambda: model.generate_content(json.dumps(prompt)))
            payload = _json_from_response(response.text)
            mapping = {
                int(item["index"]): item["category"]
                for item in payload.get("items", [])
                if item.get("category") in CATEGORIES
            }
            return mapping, payload, False
        except Exception as exc:
            raw = {"error": str(exc)}
            return _heuristic_categories(rows), raw, True

    return _heuristic_categories(rows), {"provider": "local_heuristic"}, False


def build_summary(transactions, category_breakdown, anomaly_count):
    model = _gemini_model()
    totals = defaultdict(Decimal)
    merchants = Counter()

    for txn in transactions:
        totals[txn.currency] += txn.amount
        merchants[txn.merchant] += 1

    top_merchants = [
        {"merchant": merchant, "transaction_count": count}
        for merchant, count in merchants.most_common(3)
    ]

    base = {
        "total_spend_by_currency": {
            "INR": str(totals.get("INR", Decimal("0"))),
            "USD": str(totals.get("USD", Decimal("0"))),
        },
        "top_3_merchants": top_merchants,
        "anomaly_count": anomaly_count,
        "category_breakdown": category_breakdown,
    }

    if model:
        prompt = {
            "instruction": (
                "Return only JSON with keys total_spend_by_currency, top_3_merchants, "
                "anomaly_count, narrative, risk_level. risk_level must be low, medium, or high."
            ),
            "facts": base,
        }
        try:
            response = _retry(lambda: model.generate_content(json.dumps(prompt)))
            payload = _json_from_response(response.text)
            return _normalize_summary(payload, base), payload
        except Exception as exc:
            fallback = _fallback_summary(base)
            return fallback, {"error": str(exc), "fallback": True}

    fallback = _fallback_summary(base)
    return fallback, {"provider": "local_heuristic"}


def _heuristic_categories(rows):
    mapping = {}
    for row in rows:
        merchant = row.get("merchant", "").lower()
        notes = row.get("notes", "").lower()
        text = f"{merchant} {notes}"
        if any(token in text for token in ["swiggy", "zomato", "restaurant", "cafe"]):
            category = "Food"
        elif any(token in text for token in ["amazon", "flipkart", "myntra"]):
            category = "Shopping"
        elif any(token in text for token in ["irctc", "flight", "hotel"]):
            category = "Travel"
        elif any(token in text for token in ["ola", "uber", "metro"]):
            category = "Transport"
        elif any(token in text for token in ["jio", "recharge", "electricity", "bill"]):
            category = "Utilities"
        elif any(token in text for token in ["atm", "cash"]):
            category = "Cash Withdrawal"
        elif any(token in text for token in ["netflix", "movie", "bookmyshow"]):
            category = "Entertainment"
        else:
            category = "Other"
        mapping[int(row["index"])] = category
    return mapping


def _fallback_summary(base):
    anomaly_count = base["anomaly_count"]
    risk_level = "high" if anomaly_count >= 5 else "medium" if anomaly_count >= 2 else "low"
    top = base["top_3_merchants"]
    top_text = ", ".join(item["merchant"] for item in top) or "no merchants"
    narrative = (
        f"Spending is concentrated across {top_text}. "
        f"The pipeline found {anomaly_count} anomalous transaction(s), giving this job a {risk_level} risk level."
    )
    return {
        "total_spend_by_currency": base["total_spend_by_currency"],
        "top_3_merchants": top,
        "anomaly_count": anomaly_count,
        "narrative": narrative,
        "risk_level": risk_level,
    }


def _normalize_summary(payload, base):
    payload = {**_fallback_summary(base), **payload}
    if payload.get("risk_level") not in {"low", "medium", "high"}:
        payload["risk_level"] = _fallback_summary(base)["risk_level"]
    return payload
