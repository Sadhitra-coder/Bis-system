import logging
from fastapi import APIRouter, HTTPException

from app.jobs import get_job, list_jobs
from app.models import JobInfo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobInfo)
def get_job_status(job_id: str):
    """Return status of a specific ingestion job."""
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    return job


@router.get("", response_model=list[JobInfo])
def list_all_jobs(limit: int = 50):
    """List recent ingestion jobs (newest first)."""
    return list_jobs(limit=limit)
