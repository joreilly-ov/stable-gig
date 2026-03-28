"""Anonymous contractor Q&A per job.

POST  /jobs/{job_id}/questions                      — contractor asks a question
GET   /jobs/{job_id}/questions                      — list questions (role-filtered)
PATCH /jobs/{job_id}/questions/{question_id}        — homeowner answers a question

Business rules:
  • Only registered contractors may ask questions.
  • Questions can be asked on jobs with status 'open' or 'awarded'.
  • A contractor may ask multiple questions on the same job.
  • Only the job owner may answer questions.
  • Anonymisation: when the homeowner lists questions, contractor_id is stripped and
    replaced with a stable ordinal label ("Contractor 1", "Contractor 2" …) derived
    from the order the first question was submitted by each unique contractor.
    Contractors see their own questions identified as "You".

Auth: all endpoints require a valid Supabase JWT.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.database import get_supabase_admin
from app.dependencies import get_current_user

router = APIRouter(tags=["questions"])
log = logging.getLogger(__name__)

_QUESTION_ALLOWED_STATUSES = {"open", "awarded", "in_progress"}


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class QuestionCreate(BaseModel):
    question: str = Field(
        ...,
        min_length=10,
        max_length=1_000,
        description="Clarifying question about the job (10–1 000 characters).",
    )


class QuestionAnswer(BaseModel):
    answer: str = Field(
        ...,
        min_length=1,
        max_length=2_000,
        description="Homeowner's answer to the contractor's question.",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db():
    return get_supabase_admin()


def _get_job_or_404(job_id: str) -> dict:
    res = _db().table("jobs").select("*").eq("id", job_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Job not found")
    return res.data[0]


def _get_contractor_id_or_403(user_id: str) -> str:
    """Return contractors.id for the user or raise 403.

    Under Clean Split, contractors.id = auth.users.id — there is no user_id column.
    """
    res = (
        _db()
        .table("contractors")
        .select("id")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=403, detail="Only registered contractors may ask questions")
    return res.data[0]["id"]


def _get_question_or_404(question_id: str, job_id: str) -> dict:
    res = (
        _db()
        .table("job_questions")
        .select("*")
        .eq("id", question_id)
        .eq("job_id", job_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Question not found")
    return res.data[0]


def _anonymise_for_homeowner(questions: list[dict]) -> list[dict]:
    """Replace contractor_id with a stable ordinal label per job."""
    contractor_labels: dict[str, str] = {}
    counter = 0
    result = []
    for q in questions:
        cid = q.get("contractor_id")
        if cid not in contractor_labels:
            counter += 1
            contractor_labels[cid] = f"Contractor {counter}"
        out = {k: v for k, v in q.items() if k != "contractor_id"}
        out["asked_by"] = contractor_labels[cid]
        result.append(out)
    return result


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/jobs/{job_id}/questions", status_code=201)
async def ask_question(
    job_id: str,
    body: QuestionCreate,
    user=Depends(get_current_user),
):
    """Contractor submits an anonymous clarifying question on a job."""
    user_id       = str(user.id)
    contractor_id = _get_contractor_id_or_403(user_id)
    job           = _get_job_or_404(job_id)

    if job["status"] not in _QUESTION_ALLOWED_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Questions can only be asked on jobs with status "
                f"{sorted(_QUESTION_ALLOWED_STATUSES)} "
                f"(current: '{job['status']}')"
            ),
        )

    res = _db().table("job_questions").insert({
        "job_id":        job_id,
        "contractor_id": contractor_id,
        "question":      body.question,
    }).execute()

    if not res.data:
        log.error("question_create_failed", extra={"user_id": user_id, "job_id": job_id})
        raise HTTPException(status_code=500, detail="Failed to save question")

    q = res.data[0]
    log.info("question_asked", extra={"user_id": user_id, "job_id": job_id, "question_id": q["id"]})

    # Return without contractor_id; caller knows it's their own
    return {k: v for k, v in q.items() if k != "contractor_id"} | {"asked_by": "You"}


@router.get("/jobs/{job_id}/questions")
async def list_questions(job_id: str, user=Depends(get_current_user)):
    """
    List questions on a job.

    - Job owner: sees all questions, contractor identity anonymised as "Contractor N".
    - Contractor: sees only their own questions, identified as "You".
    """
    user_id = str(user.id)
    job     = _get_job_or_404(job_id)

    if job["user_id"] == user_id:
        # Owner — all questions ordered oldest first, with contractor_id for anonymisation
        res = (
            _db()
            .table("job_questions")
            .select("*")
            .eq("job_id", job_id)
            .order("created_at")
            .execute()
        )
        return _anonymise_for_homeowner(res.data)

    # Contractor — only their own questions
    contractor_id_res = (
        _db()
        .table("contractors")
        .select("id")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )
    if not contractor_id_res.data:
        raise HTTPException(status_code=403, detail="Not authorised to view questions on this job")

    contractor_id = contractor_id_res.data[0]["id"]
    res = (
        _db()
        .table("job_questions")
        .select("*")
        .eq("job_id", job_id)
        .eq("contractor_id", contractor_id)
        .order("created_at")
        .execute()
    )
    return [
        ({k: v for k, v in q.items() if k != "contractor_id"} | {"asked_by": "You"})
        for q in res.data
    ]


@router.patch("/jobs/{job_id}/questions/{question_id}")
async def answer_question(
    job_id: str,
    question_id: str,
    body: QuestionAnswer,
    user=Depends(get_current_user),
):
    """Job owner answers a contractor's question."""
    user_id = str(user.id)
    job     = _get_job_or_404(job_id)

    if job["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Only the job owner can answer questions")

    question = _get_question_or_404(question_id, job_id)

    if question.get("answer") is not None:
        raise HTTPException(status_code=409, detail="This question has already been answered")

    res = (
        _db()
        .table("job_questions")
        .update({"answer": body.answer, "answered_at": "now()"})
        .eq("id", question_id)
        .execute()
    )

    if not res.data:
        raise HTTPException(status_code=500, detail="Failed to save answer")

    q = res.data[0]
    log.info("question_answered", extra={"user_id": user_id, "question_id": question_id})
    return {k: v for k, v in q.items() if k != "contractor_id"}
