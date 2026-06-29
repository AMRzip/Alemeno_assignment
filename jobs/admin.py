from django.contrib import admin

from .models import Job, JobSummary, Transaction


admin.site.register(Job)
admin.site.register(Transaction)
admin.site.register(JobSummary)
