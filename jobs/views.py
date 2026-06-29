import csv
import io

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Job
from .serializers import JobListSerializer, JobResultsSerializer, JobStatusSerializer
from .tasks import process_transaction_job

@api_view(['GET'])
def health(request):
    return Response({"status": "ok"})

class JobUploadView(APIView):
    parser_classes = [MultiPartParser]

    def post(self, request):
        upload = request.FILES.get("file")
        if not upload:
            return Response(
                {"detail": "Upload a CSV file using multipart field 'file'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not upload.name.lower().endswith(".csv"):
            return Response(
                {"detail": "Only .csv uploads are supported."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        sample = upload.read().decode("utf-8-sig")
        upload.seek(0)
        try:
            reader = csv.DictReader(io.StringIO(sample))
            required_columns = {
                "txn_id",
                "date",
                "merchant",
                "amount",
                "currency",
                "status",
                "category",
                "account_id",
                "notes",
            }
            missing = required_columns - set(reader.fieldnames or [])
            if missing:
                return Response(
                    {
                        "detail": "CSV is missing required columns: "
                        f"{', '.join(sorted(missing))}"
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            row_count = sum(1 for _ in reader)
        except UnicodeDecodeError:
            return Response(
                {"detail": "CSV must be UTF-8 encoded."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        job = Job.objects.create(
            filename=upload.name,
            raw_file=upload,
            status=Job.Status.PENDING,
            row_count_raw=row_count,
        )
        process_transaction_job.delay(str(job.id))
        return Response({"job_id": str(job.id)}, status=status.HTTP_202_ACCEPTED)


@api_view(["GET"])
def job_status(request, job_id):
    job = get_object_or_404(Job.objects.select_related("summary"), id=job_id)
    return Response(JobStatusSerializer(job).data)


@api_view(["GET"])
def job_results(request, job_id):
    job = get_object_or_404(
        Job.objects.select_related("summary").prefetch_related("transactions"), id=job_id
    )
    if job.status != Job.Status.COMPLETED:
        return Response(
            {
                "detail": "Results are available after the job is completed.",
                "status": job.status,
            },
            status=status.HTTP_409_CONFLICT,
        )
    return Response(JobResultsSerializer(job).data)


@api_view(["GET"])
def jobs_list(request):
    jobs = Job.objects.all()
    status_filter = request.query_params.get("status")
    if status_filter:
        jobs = jobs.filter(status=status_filter)
    return Response(JobListSerializer(jobs, many=True).data)
