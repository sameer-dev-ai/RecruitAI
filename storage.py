import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from blob_client import blob_delete, blob_enabled, blob_get_bytes, blob_get_json, blob_list, blob_put_bytes, blob_put_json

BLOB_JOBS_PATH = "recruitlens/jobs.json"
DEFAULT_STORE = {"next_job_id": 1, "next_resume_id": 1, "jobs": []}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _data_dir() -> Path:
    if os.getenv("VERCEL"):
        return Path("/tmp/recruitlens-data")
    return Path("data")


def _jobs_file() -> Path:
    return _data_dir() / "jobs.json"


def _upload_dir() -> Path:
    return _data_dir() / "uploads"


def _ensure_dirs() -> None:
    data_dir = _data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    _upload_dir().mkdir(parents=True, exist_ok=True)


def _load() -> dict:
    if blob_enabled():
        return blob_get_json(BLOB_JOBS_PATH) or json.loads(json.dumps(DEFAULT_STORE))
    _ensure_dirs()
    jobs_file = _jobs_file()
    if not jobs_file.exists():
        return json.loads(json.dumps(DEFAULT_STORE))
    with open(jobs_file, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(store: dict) -> None:
    if blob_enabled():
        blob_put_json(BLOB_JOBS_PATH, store)
        return
    _ensure_dirs()
    with open(_jobs_file(), "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2, ensure_ascii=False)


def storage_status() -> dict:
    return {
        "backend": "blob" if blob_enabled() else ("tmp" if os.getenv("VERCEL") else "local"),
        "persistent": blob_enabled(),
    }


def _find_job(store: dict, job_id: int) -> Optional[dict]:
    for job in store["jobs"]:
        if job["id"] == job_id:
            return job
    return None


def _job_summary(job: dict) -> dict:
    resumes = job.get("resumes", [])
    analyzed = [r for r in resumes if r.get("status") == "analyzed"]
    return {
        "id": job["id"],
        "title": job["title"],
        "description": job["description"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "resume_count": len(resumes),
        "analyzed_count": len(analyzed),
    }


def _blob_pathname(job_id: int, resume_id: int, filename: str) -> str:
    safe = Path(filename).name
    return f"recruitlens/uploads/{job_id}/{resume_id}_{safe}"


def _store_resume_file(job_id: int, resume_id: int, filename: str, file_bytes: bytes) -> dict:
    if blob_enabled():
        pathname = _blob_pathname(job_id, resume_id, filename)
        meta = blob_put_bytes(pathname, file_bytes)
        return {"stored_path": meta["url"], "blob_pathname": pathname}
    dest = job_upload_dir(job_id) / f"{resume_id}_{Path(filename).name}"
    dest.write_bytes(file_bytes)
    return {"stored_path": str(dest), "blob_pathname": None}


def _delete_resume_file(resume: dict) -> None:
    pathname = resume.get("blob_pathname")
    if pathname and blob_enabled():
        blob_delete(pathname)
        return
    path = Path(resume.get("stored_path", ""))
    if path.exists():
        path.unlink(missing_ok=True)


def _delete_job_files(job_id: int, resumes: list[dict]) -> None:
    if blob_enabled():
        for resume in resumes:
            if resume.get("blob_pathname"):
                blob_delete(resume["blob_pathname"])
        prefix = f"recruitlens/uploads/{job_id}/"
        for blob in blob_list(prefix):
            blob_delete(blob["pathname"])
        return
    job_dir = _upload_dir() / str(job_id)
    if job_dir.exists():
        shutil.rmtree(job_dir)


def read_resume_file(resume: dict) -> bytes:
    stored_path = resume.get("stored_path", "")
    if stored_path.startswith("http"):
        return blob_get_bytes(stored_path)
    return Path(stored_path).read_bytes()


def list_jobs() -> list[dict]:
    store = _load()
    return [_job_summary(job) for job in store["jobs"]]


def get_job(job_id: int) -> Optional[dict]:
    store = _load()
    job = _find_job(store, job_id)
    if not job:
        return None
    return {
        "id": job["id"],
        "title": job["title"],
        "description": job["description"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "resumes": sorted(
            job.get("resumes", []),
            key=lambda r: (r.get("rank") is None, r.get("rank") or 9999, r.get("created_at", "")),
        ),
    }


def create_job(title: str, description: str) -> dict:
    store = _load()
    now = _now()
    job = {
        "id": store["next_job_id"],
        "title": title.strip(),
        "description": description.strip(),
        "created_at": now,
        "updated_at": now,
        "resumes": [],
    }
    store["next_job_id"] += 1
    store["jobs"].insert(0, job)
    _save(store)
    return get_job(job["id"])


def update_job(job_id: int, title: str, description: str) -> Optional[dict]:
    store = _load()
    job = _find_job(store, job_id)
    if not job:
        return None
    job["title"] = title.strip()
    job["description"] = description.strip()
    job["updated_at"] = _now()
    _save(store)
    return get_job(job_id)


def delete_job(job_id: int) -> bool:
    store = _load()
    job = _find_job(store, job_id)
    if not job:
        return False
    _delete_job_files(job_id, job.get("resumes", []))
    store["jobs"] = [j for j in store["jobs"] if j["id"] != job_id]
    _save(store)
    return True


def job_upload_dir(job_id: int) -> Path:
    path = _upload_dir() / str(job_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def add_resume(job_id: int, filename: str, file_bytes: bytes) -> Optional[dict]:
    store = _load()
    job = _find_job(store, job_id)
    if not job:
        return None

    resume_id = store["next_resume_id"]
    store["next_resume_id"] += 1
    file_meta = _store_resume_file(job_id, resume_id, filename, file_bytes)

    now = _now()
    resume = {
        "id": resume_id,
        "filename": filename,
        "stored_path": file_meta["stored_path"],
        "blob_pathname": file_meta.get("blob_pathname"),
        "status": "pending",
        "candidate_name": None,
        "email": None,
        "phone_number": None,
        "linkedin_uri": None,
        "overall_score": None,
        "recommendation": None,
        "rank": None,
        "error_message": None,
        "analysis": None,
        "created_at": now,
    }
    job.setdefault("resumes", []).append(resume)
    job["updated_at"] = now
    _save(store)
    return resume


def delete_resume(job_id: int, resume_id: int) -> bool:
    store = _load()
    job = _find_job(store, job_id)
    if not job:
        return False

    target = None
    for resume in job.get("resumes", []):
        if resume["id"] == resume_id:
            target = resume
            break
    if not target:
        return False

    _delete_resume_file(target)
    job["resumes"] = [r for r in job["resumes"] if r["id"] != resume_id]
    job["updated_at"] = _now()
    _rerank_in_store(job)
    _save(store)
    return True


def get_resumes_for_screening(
    job_id: int, rescreen: bool = False, resume_id: Optional[int] = None
) -> tuple[Optional[dict], list[dict]]:
    store = _load()
    job = _find_job(store, job_id)
    if not job:
        return None, []
    resumes = job.get("resumes", [])
    if resume_id is not None:
        return job, [r for r in resumes if r["id"] == resume_id]
    if rescreen:
        return job, resumes
    return job, [r for r in resumes if r.get("status") == "pending"]


def save_resume_result(job_id: int, resume_id: int, analysis: dict | None, status: str, error_message: str = None) -> None:
    store = _load()
    job = _find_job(store, job_id)
    if not job:
        return
    for resume in job.get("resumes", []):
        if resume["id"] == resume_id:
            resume["status"] = status
            resume["error_message"] = error_message
            if analysis:
                resume["candidate_name"] = analysis.get("candidate_name")
                resume["email"] = analysis.get("email")
                resume["phone_number"] = analysis.get("phone_number")
                resume["linkedin_uri"] = analysis.get("linkedin_uri")
                resume["overall_score"] = analysis.get("overall_score")
                resume["recommendation"] = analysis.get("recommendation")
                resume["analysis"] = analysis
            break
    job["updated_at"] = _now()
    _rerank_in_store(job)
    _save(store)


def _rerank_in_store(job: dict) -> None:
    analyzed = sorted(
        [r for r in job.get("resumes", []) if r.get("status") == "analyzed"],
        key=lambda r: (-(r.get("overall_score") or 0), r.get("created_at", "")),
    )
    for i, resume in enumerate(analyzed):
        resume["rank"] = i + 1
    for resume in job.get("resumes", []):
        if resume.get("status") != "analyzed":
            resume["rank"] = None
