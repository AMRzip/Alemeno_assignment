import uuid

from django.db import models


class Job(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    filename = models.CharField(max_length=255)
    raw_file = models.FileField(upload_to="uploads/")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    row_count_raw = models.PositiveIntegerField(default=0)
    row_count_clean = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.filename} ({self.status})"


class Transaction(models.Model):
    job = models.ForeignKey(Job, related_name="transactions", on_delete=models.CASCADE)
    txn_id = models.CharField(max_length=100, blank=True)
    date = models.DateField(null=True, blank=True)
    merchant = models.CharField(max_length=255, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=20)
    category = models.CharField(max_length=50)
    account_id = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    is_anomaly = models.BooleanField(default=False)
    anomaly_reason = models.TextField(blank=True)
    llm_category = models.CharField(max_length=50, blank=True)
    llm_raw_response = models.JSONField(null=True, blank=True)
    llm_failed = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=["job", "account_id"]),
            models.Index(fields=["job", "is_anomaly"]),
        ]

    def __str__(self):
        return self.txn_id or f"{self.merchant} {self.amount}"


class JobSummary(models.Model):
    job = models.OneToOneField(Job, related_name="summary", on_delete=models.CASCADE)
    total_spend_inr = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    total_spend_usd = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    top_merchants = models.JSONField(default=list)
    anomaly_count = models.PositiveIntegerField(default=0)
    per_category_spend = models.JSONField(default=dict)
    narrative = models.TextField(blank=True)
    risk_level = models.CharField(max_length=20, default="low")
    llm_raw_response = models.JSONField(null=True, blank=True)
