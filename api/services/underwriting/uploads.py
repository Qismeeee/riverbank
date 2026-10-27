from collections.abc import Callable

from services.jobs import Job, JobHandler

from .models import Case, StatementUpload
from .repository import unguarded
from .service import UnderwritingService

STATEMENT_UPLOAD = "statement_upload"


def _recorded(case: Case, job_id: str) -> bool:
    return any((run.get("statement") or {}).get("job_id") == job_id for run in case.runs)


class StatementUploadHandler(JobHandler):
    kind = STATEMENT_UPLOAD

    def __init__(self, service: UnderwritingService):
        self.service = service

    def handle(self, job: Job, content: bytes) -> dict:
        return self.run(job, content, unguarded)

    def run(self, job: Job, content: bytes, commit_guard: Callable) -> dict:
        case = self.service.repository.get(job.case_id)
        if not _recorded(case, job.job_id):
            request = StatementUpload(**job.payload)
            case = self.service.upload_statement(
                job.case_id, request, content, job.submitted_by, job.job_id, commit_guard
            )
        return {"case_id": case.case_id, "status": case.status, "version": case.version}
