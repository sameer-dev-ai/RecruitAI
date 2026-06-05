import json
import os
import tempfile
import traceback
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from openai import OpenAI

import storage

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = FastAPI(title="Resume Screener API")
router = APIRouter(prefix="/api")


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception):
    if isinstance(exc, HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    traceback.print_exc()
    return JSONResponse(status_code=500, content={"detail": str(exc)})

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_default_api_key = os.getenv("OPENAI_API_KEY", "")


class JobCreate(BaseModel):
    title: str
    description: str


class JobUpdate(BaseModel):
    title: str
    description: str


def _require_api_key() -> OpenAI:
    if not _default_api_key:
        raise HTTPException(
            status_code=500,
            detail="OPENAI_API_KEY is not set. Add it to your .env file.",
        )
    return OpenAI(api_key=_default_api_key)


def extract_text_from_pdf(file_path: str) -> str:
    import pdfplumber

    text = ""
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
    return text.strip()


def extract_text_from_docx(file_path: str) -> str:
    import docx2txt

    return docx2txt.process(file_path)


def extract_resume_text(file_path: str, filename: str) -> str:
    ext = filename.lower().split(".")[-1]
    if ext == "pdf":
        return extract_text_from_pdf(file_path)
    elif ext in ("docx", "doc"):
        return extract_text_from_docx(file_path)
    elif ext == "txt":
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    else:
        raise ValueError(f"Unsupported file type: {ext}")


def extract_resume_text_from_record(resume: dict) -> str:
    content = storage.read_resume_file(resume)
    filename = resume["filename"]
    ext = filename.lower().split(".")[-1]
    if ext == "txt":
        return content.decode("utf-8", errors="ignore").strip()
    suffix = "." + ext
    tmp_dir = "/tmp" if os.getenv("VERCEL") else None
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=tmp_dir) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        return extract_resume_text(tmp_path, filename)
    finally:
        os.unlink(tmp_path)


def analyze_resume(resume_text: str, jd_text: str, candidate_name: str, api_client: OpenAI = None) -> dict:
    c = api_client or _require_api_key()
    prompt = f"""You are an expert HR recruiter and technical hiring manager. Analyze the following resume against the job description and return a detailed JSON evaluation.

JOB DESCRIPTION:
{jd_text}

RESUME (Candidate: {candidate_name}):
{resume_text}

Extract contact details from the resume header/contact section when present. Use null if not found.

Return ONLY valid JSON (no markdown, no extra text) with this exact structure:
{{
  "candidate_name": "extracted or provided name",
  "email": "<email address or null>",
  "phone_number": "<phone number or null>",
  "linkedin_uri": "<full LinkedIn profile URL or null>",
  "overall_score": <integer 0-100>,
  "summary": "<2-3 sentence executive summary of fit>",
  "sections": {{
    "education": {{
      "score": <integer 0-100>,
      "findings": "<what was found in resume>",
      "match_notes": "<how it matches or gaps vs JD>",
      "status": "strong" | "partial" | "weak" | "missing"
    }},
    "experience": {{
      "score": <integer 0-100>,
      "findings": "<years, roles, industries found>",
      "match_notes": "<relevance to JD requirements>",
      "status": "strong" | "partial" | "weak" | "missing"
    }},
    "tech_stack": {{
      "score": <integer 0-100>,
      "findings": "<specific technologies listed>",
      "match_notes": "<which required techs match, which are missing>",
      "matched_skills": ["skill1", "skill2"],
      "missing_skills": ["skill3", "skill4"],
      "status": "strong" | "partial" | "weak" | "missing"
    }},
    "certifications": {{
      "score": <integer 0-100>,
      "findings": "<certifications found if any>",
      "match_notes": "<relevance to role>",
      "status": "strong" | "partial" | "weak" | "missing"
    }},
    "soft_skills": {{
      "score": <integer 0-100>,
      "findings": "<leadership, communication, teamwork signals>",
      "match_notes": "<alignment with JD culture/requirements>",
      "status": "strong" | "partial" | "weak" | "missing"
    }},
    "projects_achievements": {{
      "score": <integer 0-100>,
      "findings": "<notable projects, quantified achievements>",
      "match_notes": "<relevance to role responsibilities>",
      "status": "strong" | "partial" | "weak" | "missing"
    }}
  }},
  "strengths": ["<top strength 1>", "<top strength 2>", "<top strength 3>"],
  "gaps": ["<gap 1>", "<gap 2>"],
  "recommendation": "strong_hire" | "consider" | "maybe" | "reject"
}}"""

    response = c.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=2000,
    )

    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def _build_screen_response(job_id: int, errors: list) -> JSONResponse:
    updated_job = storage.get_job(job_id)
    analyzed = [r for r in updated_job["resumes"] if r["status"] == "analyzed"]
    analyzed.sort(key=lambda x: x.get("overall_score", 0), reverse=True)

    output = []
    for r in analyzed:
        item = dict(r.get("analysis") or {})
        item["filename"] = r["filename"]
        item["rank"] = r["rank"]
        item["resume_id"] = r["id"]
        item["email"] = r.get("email") or item.get("email")
        item["phone_number"] = r.get("phone_number") or item.get("phone_number")
        item["linkedin_uri"] = r.get("linkedin_uri") or item.get("linkedin_uri")
        output.append(item)

    return JSONResponse({"results": output, "errors": errors, "total": len(output), "job": updated_job})


def _screen_single_resume(job: dict, resume: dict, job_id: int, api_client: OpenAI, errors: list) -> None:
    try:
        resume_text = extract_resume_text_from_record(resume)
        if not resume_text.strip():
            storage.save_resume_result(
                job_id, resume["id"], None, "error", "Could not extract text from file"
            )
            errors.append({"filename": resume["filename"], "error": "Could not extract text from file"})
            return

        candidate_name = resume["filename"].rsplit(".", 1)[0].replace("_", " ").replace("-", " ")
        analysis = analyze_resume(resume_text, job["description"], candidate_name, api_client)
        analysis["filename"] = resume["filename"]
        storage.save_resume_result(job_id, resume["id"], analysis, "analyzed")

    except json.JSONDecodeError:
        storage.save_resume_result(
            job_id, resume["id"], None, "error", "AI returned malformed response, try again"
        )
        errors.append({"filename": resume["filename"], "error": "AI returned malformed response, try again"})
    except Exception as e:
        storage.save_resume_result(job_id, resume["id"], None, "error", str(e))
        errors.append({"filename": resume["filename"], "error": str(e)})


def _index_html_path() -> Optional[Path]:
    base = Path(__file__).parent
    for candidate in (base / "templates" / "index.html", base / "public" / "index.html"):
        if candidate.exists():
            return candidate
    return None


@app.get("/")
def root():
    index = _index_html_path()
    if index:
        return FileResponse(index, media_type="text/html")
    return {"status": "ok", "service": "recruitlens"}


@router.get("/health")
def health():
    return {"status": "ok", **storage.storage_status()}


@router.get("/jobs")
def get_jobs():
    return {"jobs": storage.list_jobs()}


@router.post("/jobs")
def create_job(body: JobCreate):
    if not body.title.strip():
        raise HTTPException(status_code=400, detail="Job title is required")
    if not body.description.strip():
        raise HTTPException(status_code=400, detail="Job description is required")
    return storage.create_job(body.title, body.description)


@router.get("/jobs/{job_id}")
def get_job(job_id: int):
    job = storage.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.put("/jobs/{job_id}")
def update_job(job_id: int, body: JobUpdate):
    job = storage.update_job(job_id, body.title, body.description)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.delete("/jobs/{job_id}")
def delete_job(job_id: int):
    if not storage.delete_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return {"ok": True}


@router.post("/jobs/{job_id}/resumes")
async def upload_resumes(job_id: int, resumes: List[UploadFile] = File(...)):
    if not storage.get_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    if not resumes:
        raise HTTPException(status_code=400, detail="At least one resume is required")

    job = storage.get_job(job_id)
    if len(job["resumes"]) + len(resumes) > 20:
        raise HTTPException(status_code=400, detail="Maximum 20 resumes per job")

    added = []
    for resume_file in resumes:
        content = await resume_file.read()
        record = storage.add_resume(job_id, resume_file.filename, content)
        if record:
            added.append(record)
    return {"added": added, "total": len(added)}


@router.delete("/jobs/{job_id}/resumes/{resume_id}")
def remove_resume(job_id: int, resume_id: int):
    if not storage.delete_resume(job_id, resume_id):
        raise HTTPException(status_code=404, detail="Resume not found")
    return {"ok": True}


@router.post("/jobs/{job_id}/screen")
async def screen_job_resumes(
    job_id: int,
    rescreen: bool = Query(default=False),
    resume_id: Optional[int] = Query(default=None),
):
    job, resumes = storage.get_resumes_for_screening(job_id, rescreen=rescreen, resume_id=resume_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not resumes:
        raise HTTPException(status_code=400, detail="No resumes to analyze")

    api_client = _require_api_key()
    errors = []

    for resume in resumes:
        _screen_single_resume(job, resume, job_id, api_client, errors)

    return _build_screen_response(job_id, errors)


app.include_router(router)

_static = Path(__file__).parent / "templates"
if _static.exists() and not os.getenv("VERCEL"):
    app.mount("/", StaticFiles(directory=str(_static), html=True), name="static")
