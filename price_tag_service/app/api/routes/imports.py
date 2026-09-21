from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.deps import DbDep, Protected
from app.api.serializers import import_job_result
from app.models.entities import ImportJob

router = APIRouter(prefix="/v1/import-jobs", dependencies=[Protected])


@router.get("/{import_job_id}")
def get_import_job(import_job_id: str, db: DbDep):
    job = db.get(ImportJob, import_job_id)
    if job is None:
        raise HTTPException(404, "Import job not found")
    return import_job_result(job)
