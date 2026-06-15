# """FastAPI layer for Next.js frontend integration."""

# from __future__ import annotations

# import json
# import os
# import urllib.request
# from dataclasses import asdict
# from datetime import date, datetime, time, timedelta
# from pathlib import Path

# from typing import Optional

# from fastapi import FastAPI, File, Form, HTTPException, UploadFile
# from fastapi.middleware.cors import CORSMiddleware
# from dotenv import load_dotenv
# from pydantic import BaseModel, Field, model_validator

# from app import (
#     _build_schedule_insights,
#     _compute_parse_quality,
#     ensure_topics_for_scheduling,
#     optional_rag_query,
# )
# from core.llm_provider import get_llm_provider_config
# from core.optimizer import DailyLimits, DeepWorkWindow, StudyScheduler
# from core.parser import extract_text_from_pdf, parse_syllabus_pdf, extract_subjects, _parse_retrieval_intent
# from core.rag import rag_extract_schedule_syllabus
# from langchain_openai import ChatOpenAI
# from models.syllabus import ParsedSyllabus, Subject

# load_dotenv()


# class ParseResponse(BaseModel):
#     syllabus: dict
#     parse_quality: dict


# class ScheduleTopicInput(BaseModel):
#     title: str
#     priority: float = 1.0
#     target_minutes: int = 0
#     difficulty: float = 1.0
#     has_deadline: bool = False
#     deadline: date | None = None


# class ScheduleRequest(BaseModel):
#     syllabus: dict
#     topics: list[ScheduleTopicInput]
#     optimizer_mode: str = Field(default="cp_sat", pattern="^(cp_sat|greedy)$")
#     include_reviews: bool = True
#     strict_mode: bool = True
#     query: str | None = None
#     no_study_weekdays: list[int] = Field(default_factory=list)
#     max_minutes_per_day: int = Field(default=240, ge=60, le=720)
#     min_block_minutes: int = Field(default=30, ge=15, le=120)
#     max_block_minutes: int = Field(default=90, ge=30, le=240)
#     planning_horizon_days: int = Field(default=56, ge=7, le=180)

#     @model_validator(mode="after")
#     def _validate_block_limits(self) -> "ScheduleRequest":
#         if self.max_block_minutes < self.min_block_minutes:
#             raise ValueError("max_block_minutes must be >= min_block_minutes")
#         return self


# class ChatUpdate(BaseModel):
#     title: str
#     priority: float | None = None
#     target_minutes: int | None = None
#     difficulty: float | None = None
#     has_deadline: bool | None = None
#     deadline: date | None = None


# class ChatAdjustRequest(BaseModel):
#     syllabus: dict
#     topics: list[ScheduleTopicInput]
#     message: str


# def _default_windows() -> list[DeepWorkWindow]:
#     """Recurring weekly deep-work slots (Mon/Wed/Thu evenings + weekend mornings)."""
#     return [
#         DeepWorkWindow(weekday=0, start_time=time(18, 30), end_time=time(21, 30)),
#         DeepWorkWindow(weekday=2, start_time=time(18, 30), end_time=time(21, 30)),
#         DeepWorkWindow(weekday=3, start_time=time(18, 30), end_time=time(21, 0)),
#         DeepWorkWindow(weekday=5, start_time=time(9, 30), end_time=time(12, 30)),
#         DeepWorkWindow(weekday=6, start_time=time(10, 0), end_time=time(12, 30)),
#     ]


# app = FastAPI(title="RAG Assistant API", version="1.0.0")
# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],
#     allow_methods=["*"],
#     allow_headers=["*"],
# )


# @app.get("/health")
# def health() -> dict[str, str]:
#     return {"status": "ok"}


# @app.get("/debug/config")
# def debug_config() -> dict[str, object]:
#     """
#     Safe runtime config snapshot (no secrets).
#     Use this to confirm which provider/base_url/models are being used.
#     """
#     try:
#         cfg = get_llm_provider_config()
#         provider = cfg.provider
#         base_url = cfg.base_url
#     except Exception:
#         provider = "unknown"
#         base_url = os.getenv("OPENAI_BASE_URL")
#     return {
#         "provider": provider,
#         "base_url": base_url,
#         "models": {
#             "syllabus": os.getenv("OPENAI_SYLLABUS_MODEL"),
#             "chat": os.getenv("OPENAI_CHAT_MODEL"),
#             "planner": os.getenv("OPENAI_PLANNER_CHAT_MODEL"),
#             "embedding": os.getenv("OPENAI_EMBEDDING_MODEL"),
#         },
#     }


# @app.get("/debug/openrouter-models")
# def debug_openrouter_models(limit: int = 30) -> dict[str, object]:
#     """
#     Lists available model IDs from OpenRouter.
#     Helpful when you hit 'No endpoints found for <model>'.
#     """
#     cfg = get_llm_provider_config()
#     if cfg.provider != "openrouter":
#         raise HTTPException(status_code=400, detail="Not using OpenRouter provider.")
#     req = urllib.request.Request(
#         "https://openrouter.ai/api/v1/models",
#         headers={"Authorization": f"Bearer {cfg.api_key}"},
#         method="GET",
#     )
#     try:
#         with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
#             payload = json.loads(resp.read().decode("utf-8"))
#     except Exception as exc:  # noqa: BLE001
#         raise HTTPException(status_code=400, detail=f"Failed to fetch OpenRouter models: {exc}") from exc
#     items = payload.get("data", [])
#     ids = [item.get("id") for item in items if isinstance(item, dict) and item.get("id")]
#     return {"count": len(ids), "ids": ids[: max(1, min(int(limit), 200))]}


# @app.post("/api/parse", response_model=ParseResponse)
# async def parse_endpoint(
#     file: UploadFile = File(...),
#     use_llm: bool = Form(default=True),
#     focus_query: Optional[str] = Form(default=None),
# ) -> ParseResponse:
#     suffix = Path(file.filename or "syllabus.pdf").suffix or ".pdf"
#     temp_path = Path(f".upload_{datetime.now().timestamp()}{suffix}")
#     try:
#         payload = await file.read()
#         temp_path.write_bytes(payload)
#         fq = (focus_query or "").strip() or None
#         parsed = parse_syllabus_pdf(str(temp_path), use_llm=use_llm, focus_query=fq)
#         parsed = ensure_topics_for_scheduling(parsed)
#         if not parsed.topics:
#             raise HTTPException(
#                 status_code=400,
#                 detail=(
#                     "Could not extract usable topics. Try enabling LLM parsing "
#                     "or upload a clearer text-selectable PDF."
#                 ),
#             )
#         quality = _compute_parse_quality(parsed.raw_text or "", len(parsed.topics))
#         return ParseResponse(
#             syllabus=parsed.model_dump(mode="json"),
#             parse_quality=quality,
#         )
#     except HTTPException:
#         raise
#     except Exception as exc:  # noqa: BLE001
#         raise HTTPException(status_code=500, detail=str(exc)) from exc
#     finally:
#         if temp_path.exists():
#             temp_path.unlink()


# @app.post("/api/rag-extract", response_model=ParseResponse)
# async def rag_extract_endpoint(
#     files: list[UploadFile] = File(...),
#     extraction_metric: str = Form(...),
# ) -> ParseResponse:
#     """
#     Guided extraction: one FAISS index over all PDFs; ``extraction_metric`` is the retrieval query
#     (what to pull out, e.g. required subjects for a schedule).
#     """
#     metric = (extraction_metric or "").strip()
#     if not metric:
#         raise HTTPException(
#             status_code=400,
#             detail="RAG question (extraction_metric) is required for guided extraction.",
#         )
#     if not files:
#         raise HTTPException(status_code=400, detail="At least one PDF is required.")

#     temp_paths: list[Path] = []
#     ts = datetime.now().timestamp()
#     try:
#         labeled: list[tuple[str, str]] = []
#         for i, upload in enumerate(files):
#             suffix = Path(upload.filename or f"doc{i}.pdf").suffix or ".pdf"
#             temp_path = Path(f".upload_rag_{ts}_{i}{suffix}")
#             temp_path.write_bytes(await upload.read())
#             temp_paths.append(temp_path)
#             label = upload.filename or temp_path.name
#             text, _meta = extract_text_from_pdf(temp_path)
#             labeled.append((label, text))

#         parsed = rag_extract_schedule_syllabus(labeled, metric)
#         parsed = ensure_topics_for_scheduling(parsed)
#         if not parsed.topics:
#             raise HTTPException(
#                 status_code=400,
#                 detail=(
#                     "Could not extract usable topics from retrieved context. "
#                     "Try a more specific RAG question or different PDFs."
#                 ),
#             )
#         quality = _compute_parse_quality(parsed.raw_text or "", len(parsed.topics))
#         return ParseResponse(
#             syllabus=parsed.model_dump(mode="json"),
#             parse_quality=quality,
#         )
#     except HTTPException:
#         raise
#     except ValueError as exc:
#         raise HTTPException(status_code=400, detail=str(exc)) from exc
#     except Exception as exc:  # noqa: BLE001
#         raise HTTPException(status_code=500, detail=str(exc)) from exc
#     finally:
#         for p in temp_paths:
#             if p.exists():
#                 p.unlink()


# @app.get("/debug/extract-subjects-test")
# def test_extract_subjects() -> dict:
#     """Quick test to verify extract_subjects and _parse_retrieval_intent work."""
#     try:
#         test_text = """
#         SECOND SEMESTER
#         CS-201 Database Systems 3 1 0 4
#         CS-202 Web Development 3 1 0 4
#         """
#         intent = _parse_retrieval_intent("semester 2 subjects")
#         subjects = extract_subjects(test_text, semester=2)
#         return {
#             "status": "ok",
#             "intent": intent,
#             "subjects_found": len(subjects),
#             "subjects": [s.model_dump(mode="json") for s in subjects]
#         }
#     except Exception as exc:
#         return {"status": "error", "error": str(exc)}


# @app.post("/api/extract-subjects")
# async def extract_subjects_endpoint(
#     file: UploadFile = File(...),
#     question: Optional[str] = Form(default=None),
# ) -> dict:
#     """
#     Extract subjects from PDF based on a question (e.g., "semester 2 subjects").
    
#     Uses _parse_retrieval_intent to detect:
#     - Semester filters: "extract semester 2 subjects"
#     - Course code filters: "show CSEB204"
#     - All subjects: "list all subjects"
    
#     Returns structured list of Subject objects with semester, course_code, subject name, credits, etc.
#     """
#     if not question or not question.strip():
#         raise HTTPException(
#             status_code=400,
#             detail="Question parameter is required (e.g., 'semester 2 subjects')"
#         )
    
#     suffix = Path(file.filename or "syllabus.pdf").suffix or ".pdf"
#     temp_path = Path(f".upload_{datetime.now().timestamp()}{suffix}")
#     try:
#         payload = await file.read()
#         temp_path.write_bytes(payload)
        
#         # Extract raw text from PDF
#         raw_text, _ = extract_text_from_pdf(str(temp_path))
#         if not raw_text or not raw_text.strip():
#             raise HTTPException(
#                 status_code=400,
#                 detail="Could not extract text from PDF. Try a different PDF format."
#             )
        
#         # Parse user's question intent
#         intent = _parse_retrieval_intent(question.strip())
#         if not intent:
#             raise HTTPException(
#                 status_code=400,
#                 detail=(
#                     "Could not understand question. Try formats like: "
#                     "'semester 2 subjects', 'list all subjects', or 'show CSEB204'"
#                 )
#             )
        
#         # Extract subjects based on intent
#         intent_type = intent.get("type")
#         intent_value = intent.get("value")
        
#         if intent_type == "semester":
#             subjects = extract_subjects(raw_text, semester=intent_value)
#         elif intent_type == "course_code":
#             subjects = extract_subjects(raw_text, course_code=intent_value)
#         else:  # "all"
#             subjects = extract_subjects(raw_text)
        
#         if not subjects:
#             raise HTTPException(
#                 status_code=400,
#                 detail=f"No subjects found matching: {question}"
#             )
        
#         return {
#             "question": question,
#             "intent": intent,
#             "subjects": [s.model_dump(mode="json") for s in subjects],
#             "count": len(subjects)
#         }
    
#     except HTTPException:
#         raise
#     except Exception as exc:  # noqa: BLE001
#         raise HTTPException(status_code=500, detail=str(exc)) from exc
#     finally:
#         if temp_path.exists():
#             temp_path.unlink()


# @app.post("/api/schedule")
# def schedule_endpoint(payload: ScheduleRequest) -> dict:
#     try:
#         syllabus = ParsedSyllabus.model_validate(payload.syllabus)
#         topic_minutes_override: dict[str, int] = {}
#         topic_difficulty: dict[str, float] = {}
#         topic_deadlines: dict[str, date] = {}

#         for item in payload.topics:
#             for topic in syllabus.topics:
#                 if topic.title == item.title:
#                     topic.weightage_percent = float(item.priority)
#                     topic.estimated_hours = float(item.target_minutes) / 60 if item.target_minutes > 0 else None
#                     break
#             topic_minutes_override[item.title] = int(item.target_minutes)
#             topic_difficulty[item.title] = float(item.difficulty)
#             if item.has_deadline and item.deadline:
#                 topic_deadlines[item.title] = item.deadline

#         rag_answer = optional_rag_query(syllabus, payload.query)
#         scheduler = StudyScheduler(preferred_mode=payload.optimizer_mode)
#         explicit_minutes_mode = any(v > 0 for v in topic_minutes_override.values())
#         planning_start = date.today()
#         planning_end = planning_start + timedelta(days=int(payload.planning_horizon_days))
#         daily_limits = DailyLimits(
#             max_minutes_per_day=payload.max_minutes_per_day,
#             min_block_minutes=payload.min_block_minutes,
#             max_block_minutes=payload.max_block_minutes,
#         )
#         blocks = scheduler.build_schedule(
#             syllabus=syllabus,
#             deep_work_windows=_default_windows(),
#             daily_limits=daily_limits,
#             start_date=planning_start,
#             end_date=planning_end,
#             include_reviews=payload.include_reviews,
#             topic_minutes_override=topic_minutes_override if explicit_minutes_mode else None,
#             topic_difficulty=topic_difficulty,
#             topic_deadlines=topic_deadlines,
#             no_study_weekdays=set(payload.no_study_weekdays),
#             strict_mode=payload.strict_mode,
#         )
#         analysis_rows = _build_schedule_insights(
#             syllabus=syllabus,
#             topic_minutes_override=topic_minutes_override,
#             topic_difficulty=topic_difficulty,
#             topic_deadlines=topic_deadlines,
#             blocks=blocks,
#         )
#         return {
#             "blocks": [asdict(b) for b in blocks],
#             "analysis": analysis_rows,
#             "rag_answer": rag_answer,
#         }
#     except Exception as exc:  # noqa: BLE001
#         raise HTTPException(status_code=400, detail=str(exc)) from exc


# @app.post("/api/chat-adjust")
# def chat_adjust_endpoint(payload: ChatAdjustRequest) -> dict:
#     if not payload.message.strip():
#         raise HTTPException(status_code=400, detail="Message cannot be empty.")
#     try:
#         provider = get_llm_provider_config()
#         llm = ChatOpenAI(
#             model=os.getenv("OPENAI_PLANNER_CHAT_MODEL", "microsoft/phi-3-mini-128k-instruct"),
#             temperature=0,
#             api_key=provider.api_key,
#             base_url=provider.base_url,
#             model_kwargs={"response_format": {"type": "json_object"}},
#         )
#         topic_rows = [item.model_dump(mode="json") for item in payload.topics]
#         prompt = (
#             "You are a study planning copilot. Convert user instruction into topic setting updates.\n"
#             "Return JSON object with keys:\n"
#             "reply: short assistant message\n"
#             "updates: array of objects with fields title, priority, target_minutes, difficulty, "
#             "has_deadline, deadline.\n"
#             "Rules:\n"
#             "- Only update existing titles from provided topics.\n"
#             "- Keep priority between 0.1 and 100.\n"
#             "- Keep difficulty between 0.5 and 3.0.\n"
#             "- Keep target_minutes between 0 and 5000.\n"
#             "- deadline should be YYYY-MM-DD when has_deadline true.\n"
#             "- Do not invent new topics.\n"
#             f"Topics: {json.dumps(topic_rows)}\n"
#             f"User message: {payload.message}"
#         )
#         raw = llm.invoke(prompt)
#         content = str(getattr(raw, "content", raw))
#         parsed = json.loads(content)
#         updates: list[ChatUpdate] = []
#         for candidate in parsed.get("updates", []):
#             try:
#                 updates.append(ChatUpdate.model_validate(candidate))
#             except Exception:
#                 continue
#         return {
#             "reply": parsed.get("reply", "Applied your requested planning adjustments."),
#             "updates": [update.model_dump(mode="json") for update in updates],
#         }
#     except Exception as exc:  # noqa: BLE001
#         raise HTTPException(status_code=400, detail=f"Chat adjustment failed: {exc}") from exc

"""FastAPI layer for Next.js frontend integration."""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator

from app import (
    _build_schedule_insights,
    _compute_parse_quality,
    ensure_topics_for_scheduling,
    optional_rag_query,
)
from core.llm_provider import get_llm_provider_config
from core.optimizer import DailyLimits, DeepWorkWindow, StudyScheduler
from core.parser import extract_text_from_pdf, parse_syllabus_pdf, extract_subjects, _parse_retrieval_intent
from langchain_openai import ChatOpenAI
from models.syllabus import ParsedSyllabus, Subject

load_dotenv()


class ParseResponse(BaseModel):
    syllabus: dict
    parse_quality: dict


class ScheduleTopicInput(BaseModel):
    title: str
    priority: float = 1.0
    target_minutes: int = 0
    difficulty: float = 1.0
    has_deadline: bool = False
    deadline: date | None = None


class ScheduleRequest(BaseModel):
    syllabus: dict
    topics: list[ScheduleTopicInput]
    optimizer_mode: str = Field(default="cp_sat", pattern="^(cp_sat|greedy)$")
    include_reviews: bool = True
    strict_mode: bool = True
    query: str | None = None
    no_study_weekdays: list[int] = Field(default_factory=list)
    max_minutes_per_day: int = Field(default=240, ge=60, le=720)
    min_block_minutes: int = Field(default=30, ge=15, le=120)
    max_block_minutes: int = Field(default=90, ge=30, le=240)
    planning_horizon_days: int = Field(default=56, ge=7, le=180)

    @model_validator(mode="after")
    def _validate_block_limits(self) -> "ScheduleRequest":
        if self.max_block_minutes < self.min_block_minutes:
            raise ValueError("max_block_minutes must be >= min_block_minutes")
        return self


class ChatUpdate(BaseModel):
    title: str
    priority: float | None = None
    target_minutes: int | None = None
    difficulty: float | None = None
    has_deadline: bool | None = None
    deadline: date | None = None


class ChatAdjustRequest(BaseModel):
    syllabus: dict
    topics: list[ScheduleTopicInput]
    message: str


def _default_windows() -> list[DeepWorkWindow]:
    """Recurring weekly deep-work slots (Mon/Wed/Thu evenings + weekend mornings)."""
    return [
        DeepWorkWindow(weekday=0, start_time=time(18, 30), end_time=time(21, 30)),
        DeepWorkWindow(weekday=2, start_time=time(18, 30), end_time=time(21, 30)),
        DeepWorkWindow(weekday=3, start_time=time(18, 30), end_time=time(21, 0)),
        DeepWorkWindow(weekday=5, start_time=time(9, 30), end_time=time(12, 30)),
        DeepWorkWindow(weekday=6, start_time=time(10, 0), end_time=time(12, 30)),
    ]


app = FastAPI(title="RAG Assistant API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credential=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/debug/config")
def debug_config() -> dict[str, object]:
    """
    Safe runtime config snapshot (no secrets).
    Use this to confirm which provider/base_url/models are being used.
    """
    try:
        cfg = get_llm_provider_config()
        provider = cfg.provider
        base_url = cfg.base_url
    except Exception:
        provider = "unknown"
        base_url = os.getenv("OPENAI_BASE_URL")
    return {
        "provider": provider,
        "base_url": base_url,
        "models": {
            "syllabus": os.getenv("OPENAI_SYLLABUS_MODEL"),
            "chat": os.getenv("OPENAI_CHAT_MODEL"),
            "planner": os.getenv("OPENAI_PLANNER_CHAT_MODEL"),
            "embedding": os.getenv("OPENAI_EMBEDDING_MODEL"),
        },
    }


@app.get("/debug/openrouter-models")
def debug_openrouter_models(limit: int = 30) -> dict[str, object]:
    """
    Lists available model IDs from OpenRouter.
    Helpful when you hit 'No endpoints found for <model>'.
    """
    cfg = get_llm_provider_config()
    if cfg.provider != "openrouter":
        raise HTTPException(status_code=400, detail="Not using OpenRouter provider.")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/models",
        headers={"Authorization": f"Bearer {cfg.api_key}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Failed to fetch OpenRouter models: {exc}") from exc
    items = payload.get("data", [])
    ids = [item.get("id") for item in items if isinstance(item, dict) and item.get("id")]
    return {"count": len(ids), "ids": ids[: max(1, min(int(limit), 200))]}


@app.post("/api/parse", response_model=ParseResponse)
async def parse_endpoint(
    file: UploadFile = File(...),
    focus_query: Optional[str] = Form(default=None),
) -> ParseResponse:
    """
    Parse syllabus PDF using pure rule-based extraction (no LLM).
    focus_query is kept for API compatibility but ignored.
    """
    suffix = Path(file.filename or "syllabus.pdf").suffix or ".pdf"
    temp_path = Path(f".upload_{datetime.now().timestamp()}{suffix}")
    try:
        payload = await file.read()
        temp_path.write_bytes(payload)
        # Rule-based extraction — no LLM
        parsed = parse_syllabus_pdf(str(temp_path))
        parsed = ensure_topics_for_scheduling(parsed)
        if not parsed.topics:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Could not extract usable topics. "
                    "Upload a clearer text-selectable PDF with readable headings."
                ),
            )
        quality = _compute_parse_quality(parsed.raw_text or "", len(parsed.topics))
        return ParseResponse(
            syllabus=parsed.model_dump(mode="json"),
            parse_quality=quality,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        if temp_path.exists():
            temp_path.unlink()


@app.post("/api/rag-extract", response_model=ParseResponse)
async def rag_extract_endpoint(
    files: list[UploadFile] = File(...),
    extraction_metric: str = Form(...),
) -> ParseResponse:
    """
    Extract syllabus from PDFs using rule-based parsing.
    extraction_metric is deprecated (kept for API compatibility).
    """
    if not files:
        raise HTTPException(status_code=400, detail="At least one PDF is required.")

    temp_paths: list[Path] = []
    ts = datetime.now().timestamp()
    try:
        # Process first PDF (multi-PDF merge can be added later)
        upload = files[0]
        suffix = Path(upload.filename or "doc.pdf").suffix or ".pdf"
        temp_path = Path(f".upload_{ts}{suffix}")
        temp_path.write_bytes(await upload.read())
        temp_paths.append(temp_path)

        # Rule-based extraction — no LLM, no RAG
        parsed = parse_syllabus_pdf(str(temp_path))
        parsed = ensure_topics_for_scheduling(parsed)

        if not parsed.topics:
            raise HTTPException(
                status_code=400,
                detail="Could not extract topics. Try a text-selectable PDF.",
            )
        quality = _compute_parse_quality(parsed.raw_text or "", len(parsed.topics))
        return ParseResponse(
            syllabus=parsed.model_dump(mode="json"),
            parse_quality=quality,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        for p in temp_paths:
            if p.exists():
                p.unlink()


@app.get("/debug/extract-subjects-test")
def test_extract_subjects() -> dict:
    """Quick test to verify extract_subjects and _parse_retrieval_intent work."""
    try:
        test_text = """
        SECOND SEMESTER
        CS-201 Database Systems 3 1 0 4
        CS-202 Web Development 3 1 0 4
        """
        intent = _parse_retrieval_intent("semester 2 subjects")
        subjects = extract_subjects(test_text, semester=2)
        return {
            "status": "ok",
            "intent": intent,
            "subjects_found": len(subjects),
            "subjects": [s.model_dump(mode="json") for s in subjects]
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@app.post("/api/extract-subjects")
async def extract_subjects_endpoint(
    file: UploadFile = File(...),
    question: Optional[str] = Form(default=None),
) -> dict:
    """
    Extract subjects from PDF based on a question (e.g., "semester 2 subjects").

    Uses _parse_retrieval_intent to detect:
    - Semester filters: "extract semester 2 subjects"
    - Course code filters: "show CSEB204"
    - All subjects: "list all subjects"

    Returns structured list of Subject objects with semester, course_code, subject name, credits, etc.
    """
    if not question or not question.strip():
        raise HTTPException(
            status_code=400,
            detail="Question parameter is required (e.g., 'semester 2 subjects')"
        )

    suffix = Path(file.filename or "syllabus.pdf").suffix or ".pdf"
    temp_path = Path(f".upload_{datetime.now().timestamp()}{suffix}")
    try:
        payload = await file.read()
        temp_path.write_bytes(payload)

        # Extract raw text from PDF
        raw_text = extract_text_from_pdf(str(temp_path))
        if not raw_text or not raw_text.strip():
            raise HTTPException(
                status_code=400,
                detail="Could not extract text from PDF. Try a different PDF format."
            )

        # Parse user's question intent
        intent = _parse_retrieval_intent(question.strip())
        if not intent:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Could not understand question. Try formats like: "
                    "'semester 2 subjects', 'list all subjects', or 'show CSEB204'"
                )
            )

        # Extract subjects based on intent
        intent_type = intent.get("type")
        intent_value = intent.get("value")

        if intent_type == "semester":
            subjects = extract_subjects(raw_text, semester=intent_value)
        elif intent_type == "course_code":
            subjects = extract_subjects(raw_text, course_code=intent_value)
        else:  # "all"
            subjects = extract_subjects(raw_text)

        if not subjects:
            raise HTTPException(
                status_code=400,
                detail=f"No subjects found matching: {question}"
            )

        return {
            "question": question,
            "intent": intent,
            "subjects": [s.model_dump(mode="json") for s in subjects],
            "count": len(subjects)
        }

    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        if temp_path.exists():
            temp_path.unlink()


@app.post("/api/schedule")
def schedule_endpoint(payload: ScheduleRequest) -> dict:
    try:
        syllabus = ParsedSyllabus.model_validate(payload.syllabus)
        topic_minutes_override: dict[str, int] = {}
        topic_difficulty: dict[str, float] = {}
        topic_deadlines: dict[str, date] = {}

        for item in payload.topics:
            for topic in syllabus.topics:
                if topic.title == item.title:
                    topic.weightage_percent = float(item.priority)
                    topic.estimated_hours = float(item.target_minutes) / 60 if item.target_minutes > 0 else None
                    break
            topic_minutes_override[item.title] = int(item.target_minutes)
            topic_difficulty[item.title] = float(item.difficulty)
            if item.has_deadline and item.deadline:
                topic_deadlines[item.title] = item.deadline

        rag_answer: str | None = None
        if payload.query and payload.query.strip():
            try:
                rag_answer = optional_rag_query(syllabus, payload.query)
            except Exception as rag_exc:  # noqa: BLE001
                rag_answer = f"RAG unavailable: {rag_exc}"
        scheduler = StudyScheduler(preferred_mode=payload.optimizer_mode)
        explicit_minutes_mode = any(v > 0 for v in topic_minutes_override.values())
        planning_start = date.today()
        planning_end = planning_start + timedelta(days=int(payload.planning_horizon_days))
        daily_limits = DailyLimits(
            max_minutes_per_day=payload.max_minutes_per_day,
            min_block_minutes=payload.min_block_minutes,
            max_block_minutes=payload.max_block_minutes,
        )
        blocks = scheduler.build_schedule(
            syllabus=syllabus,
            deep_work_windows=_default_windows(),
            daily_limits=daily_limits,
            start_date=planning_start,
            end_date=planning_end,
            include_reviews=payload.include_reviews,
            topic_minutes_override=topic_minutes_override if explicit_minutes_mode else None,
            topic_difficulty=topic_difficulty,
            topic_deadlines=topic_deadlines,
            no_study_weekdays=set(payload.no_study_weekdays),
            strict_mode=payload.strict_mode,
        )
        analysis_rows = _build_schedule_insights(
            syllabus=syllabus,
            topic_minutes_override=topic_minutes_override,
            topic_difficulty=topic_difficulty,
            topic_deadlines=topic_deadlines,
            blocks=blocks,
        )
        return {
            "blocks": [asdict(b) for b in blocks],
            "analysis": analysis_rows,
            "rag_answer": rag_answer,
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/chat-adjust")
def chat_adjust_endpoint(payload: ChatAdjustRequest) -> dict:
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")
    try:
        provider = get_llm_provider_config()
        llm = ChatOpenAI(
            model=os.getenv("OPENAI_PLANNER_CHAT_MODEL", "microsoft/phi-3-mini-128k-instruct"),
            temperature=0,
            api_key=provider.api_key,
            base_url=provider.base_url,
            model_kwargs={"response_format": {"type": "json_object"}},  
        )
        topic_rows = [item.model_dump(mode="json") for item in payload.topics]
        prompt = (
            "You are a study planning copilot. Convert user instruction into topic setting updates.\n"
            "Return JSON object with keys:\n"
            "reply: short assistant message\n"
            "updates: array of objects with fields title, priority, target_minutes, difficulty, "
            "has_deadline, deadline.\n"
            "Rules:\n"
            "- Only update existing titles from provided topics.\n"
            "- Keep priority between 0.1 and 100.\n"
            "- Keep difficulty between 0.5 and 3.0.\n"
            "- Keep target_minutes between 0 and 5000.\n"
            "- deadline should be YYYY-MM-DD when has_deadline true.\n"
            "- Do not invent new topics.\n"
            f"Topics: {json.dumps(topic_rows)}\n"
            f"User message: {payload.message}"
        )
        raw = llm.invoke(prompt)
        content = str(getattr(raw, "content", raw))
        parsed = json.loads(content)
        updates: list[ChatUpdate] = []
        for candidate in parsed.get("updates", []):
            try:
                updates.append(ChatUpdate.model_validate(candidate))
            except Exception:
                continue
        return {
            "reply": parsed.get("reply", "Applied your requested planning adjustments."),
            "updates": [update.model_dump(mode="json") for update in updates],
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Chat adjustment failed: {exc}") from exc