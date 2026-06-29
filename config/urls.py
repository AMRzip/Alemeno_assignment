from django.urls import path
from jobs.views import JobUploadView, health, job_results, job_status, jobs_list

urlpatterns = [
    path('health/', health),
    path('jobs/upload', JobUploadView.as_view()),
    path('jobs/<uuid:job_id>/status', job_status),
    path('jobs/<uuid:job_id>/results', job_results),
    path('jobs', jobs_list),
]
