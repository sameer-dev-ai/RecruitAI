# RecruitLens — AI Resume Screener

Screen resumes against a job description using GPT-4o. Create jobs, upload resumes per job, and get AI-ranked candidates with contact details and per-section scores.

---

## Local development

```bash
pip install -r requirements.txt
cp .env.example .env
# Add OPENAI_API_KEY to .env

uvicorn main:app --reload --port 8000
```

Open **http://localhost:8000** — the API and UI run together.

Locally, data is stored in `data/jobs.json` and `data/uploads/`.

---

## Deploy to Vercel (full stack)

### 1. Push to GitHub

Connect the repo to Vercel.

### 2. Create a Blob store

In the Vercel project dashboard:

1. Go to **Storage** → **Create Database** → **Blob**
2. Link it to your project

Vercel sets `BLOB_READ_WRITE_TOKEN` automatically. Without Blob, jobs and resumes **will not persist** on Vercel.

### 3. Set environment variables

In **Project Settings → Environment Variables**:

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | Yes | Your OpenAI API key |
| `BLOB_READ_WRITE_TOKEN` | Yes on Vercel | Auto-set when Blob is linked |

### 4. Deploy

```bash
npm i -g vercel
vercel
```

Or push to `main` if Git integration is connected.

### Project layout on Vercel

```
resume-screener/
├── main.py               # FastAPI app + /api/* routes (Vercel entrypoint)
├── pyproject.toml        # Tells Vercel to use main:app
├── storage.py            # JSON + file storage (local or Blob)
├── blob_client.py        # Vercel Blob adapter
├── public/index.html     # Frontend (served from CDN)
├── vercel.json           # Function timeout config
└── requirements.txt
```

### Notes

- **Storage**: On Vercel, `data/jobs.json` and uploads live in **Vercel Blob**. Locally they use the `data/` folder.
- **Timeouts**: Resumes are analyzed **one at a time** to stay within Vercel's 60s function limit.
- **Re-analyze**: Use **↻ Re-analyze All** to refresh contact fields or scores.

---

## Usage

1. Create a **job** with title and description
2. Upload resumes (PDF, DOCX, DOC, TXT — up to 20 per job)
3. Click **Analyze New Resumes**
4. View ranked results with email, phone, LinkedIn (when found on resume)
5. Delete candidates individually or delete the whole job

Data persists across page refreshes.

---

## Scoring sections

| Section | What's evaluated |
|---|---|
| **Education** | Degree level, field relevance |
| **Experience** | Years, role relevance, industry |
| **Tech Stack** | Matched vs. missing skills |
| **Certifications** | Relevant credentials |
| **Soft Skills** | Leadership, communication signals |
| **Projects** | Quantified achievements |

Recommendations: **Strong Hire** · **Consider** · **Maybe** · **Reject**
