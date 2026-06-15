"""Pydantic models for syllabus parsing and downstream scheduling/RAG."""

from __future__ import annotations

from datetime import date as dt_date
from pathlib import Path
from typing import Optional, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator, computed_field


class Subject(BaseModel):
    """A subject/course extracted from syllabus text with structured metadata."""

    model_config = ConfigDict(str_strip_whitespace=True)

    semester: Optional[int] = Field(default=None, description="Semester number (1-8).")
    course_code: str = Field(min_length=1, description="Course code (e.g., CSEB204).")
    subject: str = Field(min_length=1, description="Subject name.")
    credits: int = Field(ge=0, description="Credit hours.")
    lecture: int = Field(ge=0, description="Lecture hours per week.")
    tutorial: int = Field(ge=0, description="Tutorial hours per week.")
    practical: int = Field(ge=0, description="Practical hours per week.")
    category: Literal["THEORY", "PRACTICAL"] = Field(
        default="THEORY", description="Course category."
    )


class Topic(BaseModel):
    """A syllabus topic/unit used by optimization and RAG pipelines."""

    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, description="Topic or unit title.")
    description: Optional[str] = Field(default=None, description="Optional unit details.")
    weightage_percent: Optional[float] = Field(
        default=None,
        ge=0,
        le=100,
        description="Relative assessment weight for the topic if available.",
    )
    learning_objectives: list[str] = Field(
        default_factory=list,
        description="Learning outcomes associated with this topic.",
    )
    week_or_unit: Optional[str] = Field(
        default=None,
        description="Original syllabus marker (week number, module label, etc.).",
    )
    estimated_hours: Optional[float] = Field(
        default=None,
        ge=0,
        description="Optional manually-estimated effort for this topic.",
    )


class ExamEvent(BaseModel):
    """Exam/milestone event extracted from the syllabus."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, description="Exam name, e.g., Midterm 1.")
    date: Optional[dt_date] = Field(
        default=None,
        description="Calendar date (YYYY-MM-DD) if explicitly present.",
    )
    weightage_percent: Optional[float] = Field(
        default=None,
        ge=0,
        le=100,
        description="Grade contribution of the exam if provided.",
    )
    notes: Optional[str] = Field(default=None, description="Extra exam notes.")


class ParsedSyllabus(BaseModel):
    """Normalized syllabus object shared across parser, RAG, and scheduler."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        populate_by_name=True,
        extra="ignore",
    )

    course_title: Optional[str] = None
    instructor: Optional[str] = None
    term: Optional[str] = None
    topics: list[Topic] = Field(default_factory=list)
    learning_objectives: list[str] = Field(
        default_factory=list,
        description="Course-level learning outcomes.",
    )
    exam_dates: list[ExamEvent] = Field(
        default_factory=list,
        description="Canonical field for exams and assessment milestones.",
    )
    # Backward-compatible alias for existing parser/RAG flows that still use `exams`.
    exams: list[ExamEvent] = Field(default_factory=list, exclude=True)
    raw_text: str = Field(
        default="",
        description="Original syllabus text used for cheap raw-text mode and RAG ingest.",
    )
    source_path: Optional[str] = Field(
        default=None,
        description="Absolute or relative path to the source file.",
    )
    extraction_confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence score (0-1) for extraction quality and completeness.",
    )
    extraction_method: Optional[str] = Field(
        default=None,
        description="Method used for extraction (e.g., 'pdfplumber', 'pymupdf', 'ocr').",
    )
    extraction_metadata: dict = Field(
        default_factory=dict,
        description="Additional metadata about the extraction process.",
    )

    @model_validator(mode="after")
    def _sync_exam_fields(self) -> "ParsedSyllabus":
        """Keep `exam_dates` and legacy `exams` aligned in both directions."""
        if self.exam_dates and not self.exams:
            self.exams = list(self.exam_dates)
        elif self.exams and not self.exam_dates:
            self.exam_dates = list(self.exams)
        return self

    @property
    def source_filename(self) -> Optional[str]:
        """Convenience accessor for display/logging."""
        if not self.source_path:
            return None
        return Path(self.source_path).name
