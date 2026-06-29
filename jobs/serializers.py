from rest_framework import serializers

from .models import Job, JobSummary, Transaction


class JobSummaryBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobSummary
        fields = [
            "total_spend_inr",
            "total_spend_usd",
            "top_merchants",
            "anomaly_count",
            "risk_level",
        ]


class JobListSerializer(serializers.ModelSerializer):
    row_count = serializers.IntegerField(source="row_count_raw")

    class Meta:
        model = Job
        fields = ["id", "status", "filename", "row_count", "created_at"]


class JobStatusSerializer(serializers.ModelSerializer):
    summary = JobSummaryBriefSerializer(read_only=True)

    class Meta:
        model = Job
        fields = ["id", "status", "summary", "error_message"]


class TransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Transaction
        fields = [
            "txn_id",
            "date",
            "merchant",
            "amount",
            "currency",
            "status",
            "category",
            "account_id",
            "notes",
            "is_anomaly",
            "anomaly_reason",
            "llm_category",
            "llm_failed",
        ]


class JobSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = JobSummary
        fields = [
            "total_spend_inr",
            "total_spend_usd",
            "top_merchants",
            "anomaly_count",
            "per_category_spend",
            "narrative",
            "risk_level",
        ]


class JobResultsSerializer(serializers.ModelSerializer):
    cleaned_transactions = TransactionSerializer(source="transactions", many=True)
    flagged_anomalies = serializers.SerializerMethodField()
    category_spend_breakdown = serializers.JSONField(source="summary.per_category_spend")
    llm_summary = JobSummarySerializer(source="summary")

    class Meta:
        model = Job
        fields = [
            "id",
            "status",
            "filename",
            "row_count_raw",
            "row_count_clean",
            "cleaned_transactions",
            "flagged_anomalies",
            "category_spend_breakdown",
            "llm_summary",
        ]

    def get_flagged_anomalies(self, obj):
        return TransactionSerializer(obj.transactions.filter(is_anomaly=True), many=True).data
