from pathlib import Path
from unittest.mock import patch

from django.core.files import File
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import Job
from .tasks import process_transaction_job


ROOT_DIR = Path(__file__).resolve().parents[2]
CSV_PATH = ROOT_DIR / "DevOps Assignment" / "transactions.csv"


@override_settings(MEDIA_ROOT=ROOT_DIR / "code" / "test_media")
class TransactionPipelineTests(TestCase):
    def test_worker_processes_sample_csv(self):
        with CSV_PATH.open("rb") as handle:
            job = Job.objects.create(
                filename="transactions.csv",
                raw_file=File(handle, name="transactions.csv"),
            )

        process_transaction_job.run(str(job.id))

        job.refresh_from_db()
        self.assertEqual(job.status, Job.Status.COMPLETED)
        self.assertEqual(job.row_count_raw, 95)
        self.assertEqual(job.row_count_clean, 85)
        self.assertEqual(job.transactions.count(), 85)
        self.assertGreater(job.summary.anomaly_count, 0)
        self.assertIn(job.summary.risk_level, {"low", "medium", "high"})


@override_settings(MEDIA_ROOT=ROOT_DIR / "code" / "test_media")
class JobApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    @patch("jobs.views.process_transaction_job.delay")
    def test_upload_creates_pending_job_and_enqueues_work(self, delay):
        upload = SimpleUploadedFile(
            "transactions.csv",
            CSV_PATH.read_bytes(),
            content_type="text/csv",
        )

        response = self.client.post("/jobs/upload", {"file": upload}, format="multipart")

        self.assertEqual(response.status_code, 202)
        job = Job.objects.get(id=response.data["job_id"])
        self.assertEqual(job.status, Job.Status.PENDING)
        self.assertEqual(job.row_count_raw, 95)
        delay.assert_called_once_with(str(job.id))

    def test_results_wait_until_completed(self):
        with CSV_PATH.open("rb") as handle:
            job = Job.objects.create(
                filename="transactions.csv",
                raw_file=File(handle, name="transactions.csv"),
            )

        response = self.client.get(f"/jobs/{job.id}/results")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["status"], Job.Status.PENDING)
