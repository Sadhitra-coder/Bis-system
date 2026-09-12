"""
In-memory job management for asynchronous background ingestion.
"""

import threading
import time
import uuid
from typing import Dict, List, Optional

from app.models import JobInfo, JobStatus

_jobs_lock = threading.Lock()
_jobs: Dict[str, JobInfo] = {}


def create_job(document_id: str, filename: str) -> JobInfo:
    """Create and register a new queued job."""
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    job = JobInfo(
        job_id=job_id,
        document_id=document_id,
        filename=filename,
        status=JobStatus.QUEUED,
        chunks_indexed=0,
        error=None,
        created_at=time.time(),
        completed_at=None
    )
    with _jobs_lock:
        _jobs[job_id] = job
    return job


def get_job(job_id: str) -> Optional[JobInfo]:
    """Retrieve job by its ID."""
    with _jobs_lock:
        return _jobs.get(job_id)


def list_jobs(limit: int = 50) -> List[JobInfo]:
    """List most recent jobs."""
    with _jobs_lock:
        jobs = list(_jobs.values())
    jobs.sort(key=lambda j: j.created_at, reverse=True)
    return jobs[:limit]


def update_job_status(
    job_id: str,
    status: str,
    chunks_indexed: int = 0,
    error: Optional[str] = None
) -> Optional[JobInfo]:
    """Update status, chunks count, or error message of a job."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        job.status = status
        if chunks_indexed:
            job.chunks_indexed = chunks_indexed
        if error is not None:
            job.error = error
        if status in (JobStatus.COMPLETED, JobStatus.FAILED):
            job.completed_at = time.time()
        return job
