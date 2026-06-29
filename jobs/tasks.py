import csv
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .llm import build_summary, classify_transactions
from .models import Job, JobSummary, Transaction


DOMESTIC_ONLY_MERCHANTS = {"swiggy", "ola", "irctc"}


@shared_task(bind=True)
def process_transaction_job(self, job_id):
    job = Job.objects.get(id=job_id)
    job.status = Job.Status.PROCESSING
    job.error_message = ""
    job.save(update_fields=["status", "error_message"])

    try:
        with job.raw_file.open("r") as handle:
            reader = csv.DictReader(handle)
            raw_rows = list(reader)

        cleaned_rows = _clean_rows(raw_rows)
        llm_rows = [
            {
                "index": index,
                "txn_id": row["txn_id"],
                "merchant": row["merchant"],
                "amount": str(row["amount"]),
                "currency": row["currency"],
                "notes": row["notes"],
            }
            for index, row in enumerate(cleaned_rows)
            if row["category_was_missing"]
        ]
        llm_categories, llm_raw, llm_failed = classify_transactions(llm_rows)
        medians = _account_medians(cleaned_rows)

        transaction_objects = []
        for index, row in enumerate(cleaned_rows):
            is_anomaly, reason = _anomaly_for(row, medians)
            llm_category = llm_categories.get(index, "") if row["category_was_missing"] else ""
            transaction_objects.append(
                Transaction(
                    job=job,
                    txn_id=row["txn_id"],
                    date=row["date"],
                    merchant=row["merchant"],
                    amount=row["amount"],
                    currency=row["currency"],
                    status=row["status"],
                    category=row["category"],
                    account_id=row["account_id"],
                    notes=row["notes"],
                    is_anomaly=is_anomaly,
                    anomaly_reason=reason,
                    llm_category=llm_category,
                    llm_raw_response=llm_raw if row["category_was_missing"] else None,
                    llm_failed=llm_failed if row["category_was_missing"] else False,
                )
            )

        with transaction.atomic():
            job.transactions.all().delete()
            Transaction.objects.bulk_create(transaction_objects)
            persisted = list(job.transactions.all())
            category_breakdown = _category_breakdown(persisted)
            anomaly_count = sum(1 for txn in persisted if txn.is_anomaly)
            summary_payload, summary_raw = build_summary(
                persisted, category_breakdown, anomaly_count
            )
            JobSummary.objects.update_or_create(
                job=job,
                defaults={
                    "total_spend_inr": Decimal(
                        str(summary_payload["total_spend_by_currency"].get("INR", "0"))
                    ),
                    "total_spend_usd": Decimal(
                        str(summary_payload["total_spend_by_currency"].get("USD", "0"))
                    ),
                    "top_merchants": summary_payload.get("top_3_merchants", []),
                    "anomaly_count": anomaly_count,
                    "per_category_spend": category_breakdown,
                    "narrative": summary_payload.get("narrative", ""),
                    "risk_level": summary_payload.get("risk_level", "low"),
                    "llm_raw_response": summary_raw,
                },
            )
            job.status = Job.Status.COMPLETED
            job.row_count_raw = len(raw_rows)
            job.row_count_clean = len(cleaned_rows)
            job.completed_at = timezone.now()
            job.save(
                update_fields=[
                    "status",
                    "row_count_raw",
                    "row_count_clean",
                    "completed_at",
                ]
            )
    except Exception as exc:
        job.status = Job.Status.FAILED
        job.error_message = str(exc)
        job.completed_at = timezone.now()
        job.save(update_fields=["status", "error_message", "completed_at"])
        raise


def _clean_rows(raw_rows):
    seen = set()
    cleaned = []

    for raw in raw_rows:
        key = tuple((name, (raw.get(name) or "").strip()) for name in sorted(raw.keys()))
        if key in seen:
            continue
        seen.add(key)

        category = (raw.get("category") or "").strip()
        cleaned.append(
            {
                "txn_id": (raw.get("txn_id") or "").strip(),
                "date": _parse_date((raw.get("date") or "").strip()),
                "merchant": (raw.get("merchant") or "").strip(),
                "amount": _parse_amount(raw.get("amount") or "0"),
                "currency": (raw.get("currency") or "").strip().upper(),
                "status": (raw.get("status") or "").strip().upper(),
                "category": category or "Uncategorised",
                "category_was_missing": not category,
                "account_id": (raw.get("account_id") or "").strip(),
                "notes": (raw.get("notes") or "").strip(),
            }
        )

    return cleaned


def _parse_date(value):
    for fmt in ("%d-%m-%Y", "%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _parse_amount(value):
    cleaned = str(value).replace("$", "").replace(",", "").strip()
    try:
        return Decimal(cleaned).quantize(Decimal("0.01"))
    except InvalidOperation:
        return Decimal("0.00")


def _account_medians(rows):
    amounts_by_account = defaultdict(list)
    for row in rows:
        amounts_by_account[row["account_id"]].append(row["amount"])

    medians = {}
    for account_id, amounts in amounts_by_account.items():
        ordered = sorted(amounts)
        midpoint = len(ordered) // 2
        if len(ordered) % 2:
            medians[account_id] = ordered[midpoint]
        else:
            medians[account_id] = (ordered[midpoint - 1] + ordered[midpoint]) / 2
    return medians


def _anomaly_for(row, medians):
    reasons = []
    median = medians.get(row["account_id"], Decimal("0"))
    if median and row["amount"] > median * 3:
        reasons.append(f"Amount exceeds 3x account median ({median})")

    merchant_key = row["merchant"].strip().lower()
    if row["currency"] == "USD" and any(
        brand in merchant_key for brand in DOMESTIC_ONLY_MERCHANTS
    ):
        reasons.append("USD used for domestic-only merchant")

    return bool(reasons), "; ".join(reasons)


def _category_breakdown(transactions):
    breakdown = defaultdict(lambda: defaultdict(Decimal))
    for txn in transactions:
        category = txn.llm_category or txn.category
        breakdown[category][txn.currency] += txn.amount

    return {
        category: {currency: str(amount) for currency, amount in currencies.items()}
        for category, currencies in breakdown.items()
    }
