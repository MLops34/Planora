# """
# Syllabus parsing: extract text from PDFs and optionally structure with an LLM.

# Requires ``OPENAI_API_KEY`` in the environment for structured LLM parsing.
# Without a key, ``parse_syllabus_pdf(..., use_llm=False)`` returns raw text only.
# """

# from __future__ import annotations

# import json
# import logging
# import os
# import re
# import sys
# import time
# from pathlib import Path
# from typing import Union, Optional, List

# # Project root on path for `models` when not installed as a package
# _PROJECT_ROOT = Path(__file__).resolve().parents[1]
# if str(_PROJECT_ROOT) not in sys.path:
#     sys.path.insert(0, str(_PROJECT_ROOT))

# from pypdf import PdfReader

# from core.llm_provider import get_llm_provider_config
# from models.syllabus import ParsedSyllabus, Subject

# # Configure logging
# logger = logging.getLogger(__name__)

# # Library availability checks
# _PDFPLUMBER_AVAILABLE = False
# _PYMUPDF_AVAILABLE = False
# _PYPDF_AVAILABLE = True  # pypdf is imported above, so it's available
# _PYTESSERACT_AVAILABLE = False
# _EASYOCR_AVAILABLE = False

# try:
#     import pdfplumber
#     _PDFPLUMBER_AVAILABLE = True
# except ImportError:
#     logger.warning("pdfplumber not available - table extraction will be limited")

# try:
#     import fitz  # PyMuPDF
#     _PYMUPDF_AVAILABLE = True
# except ImportError:
#     logger.warning("PyMuPDF (fitz) not available - alternative extraction will be used")

# try:
#     import pytesseract
#     from PIL import Image
#     _PYTESSERACT_AVAILABLE = True
# except ImportError:
#     logger.debug("pytesseract not available - OCR fallback will be limited")

# try:
#     import easyocr
#     _EASYOCR_AVAILABLE = True
# except ImportError:
#     logger.debug("easyocr not available - OCR fallback will be limited")

# PathLike = Union[str, Path]

# # Rough token-safe cap for LLM context (characters); adjust per model
# # Reduced from 48000 to 20000 to prevent token limit errors
# # Each 4 chars ≈ 1 token, so 20k chars ≈ 5k tokens (safe margin for Groq models)
# _DEFAULT_MAX_CHARS_FOR_LLM = 20_000

# # Library preference configuration (higher priority = tried first)
# _LIBRARY_PRIORITY = {
#     "pdfplumber": 3,  # Best for tables and layout
#     "pymupdf": 2,     # Good for text and tables
#     "pypdf": 1,       # Baseline fallback
# }

# # LLM provider fallback chain (tried in order if previous fails)
# _LLM_PROVIDER_FALLBACK = [
#     "groq",      # Primary: fast, cost-effective
#     "openai",    # Fallback: reliable, higher quality
#     "local",     # Last resort: local model if available
# ]


# def get_library_status() -> dict[str, bool]:
#     """Return availability status of all PDF extraction libraries."""
#     return {
#         "pdfplumber": _PDFPLUMBER_AVAILABLE,
#         "pymupdf": _PYMUPDF_AVAILABLE,
#         "pypdf": _PYPDF_AVAILABLE,
#     }


# def set_library_priority(library: str, priority: int) -> None:
#     """
#     Set priority for a specific PDF extraction library.

#     Args:
#         library: One of "pdfplumber", "pymupdf", "pypdf"
#         priority: Higher value = tried first (recommended: 1-10)
#     """
#     if library not in _LIBRARY_PRIORITY:
#         raise ValueError(f"Unknown library: {library}. Must be one of {list(_LIBRARY_PRIORITY.keys())}")
#     _LIBRARY_PRIORITY[library] = priority
#     logger.info(f"Set {library} priority to {priority}")


# def get_library_priority() -> dict[str, int]:
#     """Return current priority configuration for all libraries."""
#     return _LIBRARY_PRIORITY.copy()


# def set_llm_provider_fallback(providers: list[str]) -> None:
#     """
#     Set the LLM provider fallback chain.

#     Args:
#         providers: List of provider names in priority order (e.g., ["groq", "openai", "local"])
#     """
#     global _LLM_PROVIDER_FALLBACK
#     valid_providers = {"groq", "openai", "local"}
#     for provider in providers:
#         if provider not in valid_providers:
#             raise ValueError(f"Unknown provider: {provider}. Must be one of {valid_providers}")
#     _LLM_PROVIDER_FALLBACK = providers
#     logger.info(f"Set LLM provider fallback chain: {providers}")


# def get_llm_provider_fallback() -> list[str]:
#     """Return current LLM provider fallback chain."""
#     return _LLM_PROVIDER_FALLBACK.copy()


# # (re and typing already imported at top of file)


# def extract_subjects(
#     raw_text: str,
#     semester: Optional[int] = None,
#     course_code: Optional[str] = None,
# ) -> List[Subject]:
#     """
#     Extract subjects filtered by:
#     - semester
#     OR
#     - course code

#     Returns structured Subject objects instead of dicts for better type safety.

#     Examples:
#         extract_subjects(text, semester=2)

#         extract_subjects(text, course_code="CSEB204")
#     """

#     semester_patterns = {
#         "FIRST SEMESTER": 1,
#         "SECOND SEMESTER": 2,
#         "THIRD SEMESTER": 3,
#         "FOURTH SEMESTER": 4,
#         "FIFTH SEMESTER": 5,
#         "SIXTH SEMESTER": 6,
#         "SEVENTH SEMESTER": 7,
#         "EIGHTH SEMESTER": 8,
#     }

#     lines = [line.strip() for line in raw_text.splitlines() if line.strip()]

#     current_semester = None
#     current_category = "THEORY"

#     results = []

#     # Improved regex to handle: CS-201, CSE 201, CSE201, lowercase OCR, spacing variations
#     subject_pattern = re.compile(
#         r"^([A-Za-z]{2,}[-\s]?\d{3}(?:\(P\))?)\s+"
#         r"(.+?)\s+"
#         r"(\d+)\s+(\d+)\s+(\d+)\s+(\d+)$"
#     )

#     for line in lines:

#         upper_line = line.upper()

#         # Detect semester
#         if upper_line in semester_patterns:
#             current_semester = semester_patterns[upper_line]
#             continue

#         # Detect category
#         if upper_line == "THEORETICAL":
#             current_category = "THEORY"
#             continue

#         if upper_line == "PRACTICAL":
#             current_category = "PRACTICAL"
#             continue

#         # Match subject line
#         match = subject_pattern.match(line)

#         if match:

#             extracted_course_code = match.group(1).strip()
#             subject_name = match.group(2).strip()

#             l = int(match.group(3))
#             t = int(match.group(4))
#             p = int(match.group(5))
#             credits = int(match.group(6))

#             subject_data = Subject(
#                 semester=current_semester,
#                 course_code=extracted_course_code,
#                 subject=subject_name,
#                 credits=credits,
#                 lecture=l,
#                 tutorial=t,
#                 practical=p,
#                 category=current_category
#             )

#             # FILTER BY SEMESTER
#             if semester is not None:
#                 if current_semester == semester:
#                     results.append(subject_data)

#             # FILTER BY COURSE CODE
#             elif course_code is not None:
#                 if extracted_course_code.upper() == course_code.upper():
#                     results.append(subject_data)

#             # NO FILTER → return all
#             else:
#                 results.append(subject_data)

#     return results


# # ---------------------------------------------------------------------------
# # Retrieval intent detection
# # ---------------------------------------------------------------------------

# _ORDINAL_TO_INT: dict[str, int] = {
#     "first": 1, "second": 2, "third": 3, "fourth": 4,
#     "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
#     "1st": 1, "2nd": 2, "3rd": 3, "4th": 4,
#     "5th": 5, "6th": 6, "7th": 7, "8th": 8,
# }

# # Patterns that suggest the user wants structured subject extraction
# _RETRIEVAL_KEYWORDS = frozenset([
#     "semester", "sem", "subject", "subjects", "course", "courses",
#     "extract", "list", "show", "get", "fetch", "find", "give",
# ])


# def _parse_retrieval_intent(query: str) -> Optional[dict]:
#     """
#     Detect whether a user query is a *structured subject retrieval* request
#     and extract the filter parameters for :func:`extract_subjects`.

#     Supported patterns
#     ------------------
#     * **Semester filter** — any of:
#       - ``"extract semester 2 subjects"``
#       - ``"show me sem 3"``
#       - ``"second semester subjects"``
#       - ``"list 4th semester"``
#     * **Course code filter** — any of:
#       - ``"show CSEB204"``
#       - ``"find CS-201"``
#     * **All subjects** — any of:
#       - ``"list all subjects"``
#       - ``"show all courses"``

#     Returns
#     -------
#     dict with keys ``type`` and ``value``, or ``None`` if this is NOT a
#     retrieval query (so the caller can fall back to a RAG answer instead).

#     Examples
#     --------
#     >>> _parse_retrieval_intent("extract semester 2 subjects")
#     {'type': 'semester', 'value': 2}
#     >>> _parse_retrieval_intent("show CSEB204")
#     {'type': 'course_code', 'value': 'CSEB204'}
#     >>> _parse_retrieval_intent("list all subjects")
#     {'type': 'all', 'value': None}
#     >>> _parse_retrieval_intent("what is machine learning?")
#     None
#     """
#     if not query or not query.strip():
#         return None

#     q = query.strip().lower()
#     words = set(re.findall(r"[a-z]+", q))

#     # Must contain at least one retrieval keyword to qualify
#     if not words & _RETRIEVAL_KEYWORDS:
#         return None

#     # ── All subjects ─────────────────────────────────────────────────────────
#     if re.search(r"\ball\b", q) and words & {"subject", "subjects", "course", "courses"}:
#         return {"type": "all", "value": None}

#     # ── Semester filter ───────────────────────────────────────────────────────
#     # Pattern 1: "semester 2" / "sem 3" / "semester2"
#     m = re.search(r"(?:semester|sem)\s*(\d)", q)
#     if m:
#         return {"type": "semester", "value": int(m.group(1))}

#     # Pattern 2: "2nd semester" / "4th sem"
#     m = re.search(r"(\d)(?:st|nd|rd|th)\s+(?:semester|sem)", q)
#     if m:
#         return {"type": "semester", "value": int(m.group(1))}

#     # Pattern 3: "second semester" / "third sem"
#     m = re.search(
#         r"(first|second|third|fourth|fifth|sixth|seventh|eighth)\s+(?:semester|sem)",
#         q,
#     )
#     if m:
#         val = _ORDINAL_TO_INT.get(m.group(1))
#         if val:
#             return {"type": "semester", "value": val}

#     # Pattern 4: bare ordinals near retrieval word, e.g. "show me second sem subjects"
#     for word, num in _ORDINAL_TO_INT.items():
#         if word in q and ("sem" in q or "semester" in q):
#             return {"type": "semester", "value": num}

#     # ── Course code filter ────────────────────────────────────────────────────
#     # Course codes look like: CSEB204, CS-201, CS201, MECH301(P)
#     m = re.search(r"\b([A-Za-z]{2,}[-]?\d{3}(?:\(P\))?)", query)  # use original case
#     if m:
#         return {"type": "course_code", "value": m.group(1).upper()}

#     # Query contained retrieval keywords but no specific filter — return all
#     if words & {"subject", "subjects", "course", "courses"}:
#         return {"type": "all", "value": None}

#     return None


# def _normalize_extracted_text(text: str) -> str:
#     """Normalize noisy PDF extraction into a parser-friendly text block."""
#     if not text:
#         return ""
#     normalized = text.replace("\r\n", "\n").replace("\r", "\n")
#     # Remove isolated page numbers and common running headers/footers noise.
#     cleaned_lines: list[str] = []
#     for raw_line in normalized.splitlines():
#         line = re.sub(r"\s+", " ", raw_line).strip()
#         if not line:
#             continue
#         if re.fullmatch(r"(page\s*)?\d{1,3}(\s*of\s*\d{1,3})?", line, flags=re.IGNORECASE):
#             continue
#         cleaned_lines.append(line)
#     normalized = "\n".join(cleaned_lines)
#     # Fix PDF hyphenation line-break artifacts.
#     normalized = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", normalized)
#     # Remove repeated excessive blank lines after cleaning.
#     normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
#     return normalized


# def _text_quality_is_low(text: str) -> bool:
#     """Heuristic to detect low-quality extraction likely needing fallback strategy."""
#     if not text.strip():
#         return True
#     words = re.findall(r"[A-Za-z]{3,}", text)
#     if len(words) < 80:
#         return True
#     lines = [line for line in text.splitlines() if line.strip()]
#     alpha_chars = sum(ch.isalpha() for ch in text)
#     total_chars = max(1, len(text))
#     alpha_ratio = alpha_chars / total_chars
#     short_line_ratio = (
#         sum(1 for line in lines if len(line.strip()) <= 2) / max(1, len(lines))
#     )
#     return alpha_ratio < 0.45 or short_line_ratio > 0.12


# def _chunk_text(text: str, max_chars: int) -> list[str]:
#     """Chunk text by paragraph boundaries to preserve topic context."""
#     if len(text) <= max_chars:
#         return [text]
#     paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
#     chunks: list[str] = []
#     current: list[str] = []
#     current_len = 0
#     for para in paragraphs:
#         extra = len(para) + (2 if current else 0)
#         if current and current_len + extra > max_chars:
#             chunks.append("\n\n".join(current))
#             current = [para]
#             current_len = len(para)
#         else:
#             current.append(para)
#             current_len += extra
#     if current:
#         chunks.append("\n\n".join(current))
#     return chunks


# def _focus_question_tokens(question: str) -> set[str]:
#     return {t.lower() for t in re.findall(r"[A-Za-z]{3,}", question)}


# def _ranked_excerpts_for_focus(full_text: str, question: str, excerpt_budget: int) -> str:
#     """
#     Cheap keyword overlap: sliding windows scored by question token hits.
#     No embeddings required (works with Groq-only setups).
#     """
#     tokens = _focus_question_tokens(question)
#     if not tokens:
#         return ""

#     chunk_size = 1800
#     step = 1200
#     scored: list[tuple[int, str]] = []
#     pos = 0
#     while pos < len(full_text):
#         chunk = full_text[pos : pos + chunk_size]
#         low = chunk.lower()
#         score = sum(low.count(t) for t in tokens)
#         scored.append((score, chunk))
#         pos += step

#     scored.sort(key=lambda x: -x[0])
#     sep = "\n\n--- excerpt ---\n\n"
#     parts: list[str] = []
#     used = 0
#     for score, chunk in scored:
#         if score <= 0:
#             break
#         if used + len(chunk) + len(sep) > excerpt_budget:
#             room = excerpt_budget - used - len(sep)
#             if room > 400:
#                 parts.append(chunk[:room])
#             break
#         parts.append(chunk)
#         used += len(chunk) + len(sep)

#     return sep.join(parts) if parts else ""


# def _compose_llm_input_for_parse(full_raw: str, focus_query: str | None, max_chars: int) -> str:
#     """
#     When ``focus_query`` is set, prepend ranked excerpts so the LLM prioritizes
#     sections of the PDF that match the user's optional question.
#     """
#     fq = (focus_query or "").strip()
#     if not fq:
#         if len(full_raw) > max_chars:
#             return full_raw[:max_chars] + "\n\n[... truncated ...]"
#         return full_raw

#     excerpt_budget = min(16_000, max(8_000, max_chars // 2))
#     excerpts = _ranked_excerpts_for_focus(full_raw, fq, excerpt_budget)

#     header = (
#         "### User focus question (prioritize syllabus content that answers this)\n"
#         f"{fq}\n\n"
#     )
#     if excerpts:
#         header += "### Excerpts most relevant to that question (keyword-ranked)\n" + excerpts + "\n\n"
#     header += "### Full syllabus text (still extract all course-wide topics and exams; may truncate)\n"

#     remaining = max_chars - len(header) - 80
#     remaining = max(2000, remaining)
#     body = full_raw[:remaining] if len(full_raw) > remaining else full_raw
#     suffix = "\n\n[... syllabus truncated for model context ...]" if len(full_raw) > remaining else ""
#     return (header + body + suffix).strip()


# def _merge_unique_topics(base: ParsedSyllabus, incoming: ParsedSyllabus) -> None:
#     seen: set[str] = {t.title.strip().lower() for t in base.topics if t.title.strip()}
#     for topic in incoming.topics:
#         key = topic.title.strip().lower()
#         if key and key not in seen:
#             base.topics.append(topic)
#             seen.add(key)
#     existing_exam_keys = {
#         (e.name.strip().lower(), e.date.isoformat() if e.date else "")
#         for e in base.exam_dates
#     }
#     for exam in incoming.exam_dates:
#         exam_key = (exam.name.strip().lower(), exam.date.isoformat() if exam.date else "")
#         if exam_key not in existing_exam_keys:
#             base.exam_dates.append(exam)
#             existing_exam_keys.add(exam_key)


# def _sanitize_md_cell(cell: str) -> str:
#     """Escape characters that break markdown pipe tables."""
#     return cell.replace("|", "∣").replace("\n", " ").strip()


# def _table_fingerprint(table: list[list[str | None]]) -> str:
#     """Stable key for deduplicating near-identical tables across extractors."""
#     if not table:
#         return ""
#     rows = table[:3]
#     parts: list[str] = []
#     for row in rows:
#         parts.append("|".join((("" if c is None else str(c)).strip() for c in row)))
#     return "|".join(parts)[:800]


# def _dedupe_tables(tables: list[list[list[str | None]]]) -> list[list[list[str | None]]]:
#     seen: set[str] = set()
#     out: list[list[list[str | None]]] = []
#     for table in tables:
#         fp = _table_fingerprint(table)
#         if not fp or fp in seen:
#             continue
#         seen.add(fp)
#         out.append(table)
#     return out


# def _tables_to_markdown(tables: list[list[list[str | None]]]) -> str:
#     """
#     Convert extracted tables into a simple markdown representation.
#     `tables` is list[table] where table is list[row] and row is list[cell].
#     """
#     blocks: list[str] = []
#     deduped = _dedupe_tables(tables)
#     for t_idx, table in enumerate(deduped, start=1):
#         if not table or not any(row for row in table):
#             continue
#         norm_rows: list[list[str]] = []
#         for row in table:
#             norm_rows.append(
#                 [_sanitize_md_cell("" if c is None else str(c)) for c in row],
#             )
#         if sum(1 for r in norm_rows for c in r if c) < 3:
#             continue
#         header = norm_rows[0]
#         col_count = max(1, len(header))
#         header = (header + [""] * col_count)[:col_count]
#         sep = ["---"] * col_count
#         body = norm_rows[1:] if len(norm_rows) > 1 else []

#         md = [f"[Table {t_idx}]", "|" + "|".join(header) + "|", "|" + "|".join(sep) + "|"]
#         for row in body[:80]:
#             row = (row + [""] * col_count)[:col_count]
#             md.append("|" + "|".join(row) + "|")
#         blocks.append("\n".join(md))
#     return "\n\n".join(blocks).strip()


# def _pdfplumber_table_setting_presets() -> list[dict[str, object]]:
#     """
#     Multiple strategies: ruled tables, mixed, and borderless / text-aligned grids.
#     """
#     return [
#         {
#             "vertical_strategy": "lines",
#             "horizontal_strategy": "lines",
#             "intersection_tolerance": 4,
#             "snap_tolerance": 3,
#             "join_tolerance": 3,
#             "edge_min_length": 3,
#         },
#         {
#             "vertical_strategy": "lines",
#             "horizontal_strategy": "text",
#             "intersection_tolerance": 5,
#             "snap_tolerance": 4,
#             "join_tolerance": 4,
#             "text_x_tolerance": 2,
#             "text_y_tolerance": 2,
#         },
#         {
#             "vertical_strategy": "text",
#             "horizontal_strategy": "text",
#             "intersection_tolerance": 5,
#             "snap_tolerance": 4,
#             "join_tolerance": 4,
#             "min_words_vertical": 2,
#             "min_words_horizontal": 1,
#         },
#     ]


# def _extract_tables_pdfplumber_page(page: object, settings: dict[str, object]) -> list[list[list[str | None]]]:
#     out: list[list[list[str | None]]] = []
#     try:
#         raw = page.extract_tables(table_settings=settings)
#     except TypeError:
#         raw = page.extract_tables()
#     if not raw:
#         return out
#     for table in raw:
#         if table and any(any(c not in (None, "") for c in row) for row in table):
#             out.append(table)
#     return out


# def _extract_pdfplumber_text_and_tables(path: Path) -> tuple[str, list[list[list[str | None]]], str]:
#     """Return (plain text per page joined, all unique-ish tables, library name)."""
#     if not _PDFPLUMBER_AVAILABLE:
#         logger.warning("pdfplumber requested but not available")
#         return "", [], "pdfplumber"

#     import pdfplumber

#     texts: list[str] = []
#     all_tables: list[list[list[str | None]]] = []
#     presets = _pdfplumber_table_setting_presets()

#     try:
#         with pdfplumber.open(str(path)) as pdf:
#             for page_num, page in enumerate(pdf.pages, start=1):
#                 try:
#                     txt = page.extract_text(
#                         x_tolerance=2,
#                         y_tolerance=2,
#                         layout=True,
#                     )
#                 except TypeError:
#                     txt = page.extract_text(x_tolerance=2, y_tolerance=2)
#                 if txt:
#                     texts.append(txt)
#                 for preset in presets:
#                     try:
#                         all_tables.extend(_extract_tables_pdfplumber_page(page, preset))
#                     except Exception as e:
#                         logger.debug(f"pdfplumber table extraction failed on page {page_num} with preset: {e}")
#                         continue
#         body = "\n\n".join(texts).strip()
#         logger.info(f"pdfplumber extracted {len(texts)} pages and {len(all_tables)} tables")
#         return body, all_tables, "pdfplumber"
#     except Exception as e:
#         logger.error(f"pdfplumber extraction failed: {e}")
#         return "", [], "pdfplumber"


# def _extract_pymupdf_text_and_tables(path: Path) -> tuple[str, list[list[list[str | None]]], str]:
#     """PyMuPDF text plus native table finder when available (strong on grid PDFs)."""
#     if not _PYMUPDF_AVAILABLE:
#         logger.warning("PyMuPDF requested but not available")
#         return "", [], "pymupdf"

#     import fitz  # PyMuPDF

#     texts: list[str] = []
#     all_tables: list[list[list[str | None]]] = []
#     doc = fitz.open(str(path))
#     try:
#         for page_num, page in enumerate(doc, start=1):
#             t = page.get_text("text")
#             if t:
#                 texts.append(t)
#             try:
#                 finder = page.find_tables()
#                 for tab in getattr(finder, "tables", []) or []:
#                     try:
#                         rows = tab.extract()
#                     except Exception as e:
#                         logger.debug(f"PyMuPDF table extraction failed on page {page_num}: {e}")
#                         continue
#                     if rows and any(any(str(c).strip() for c in row) for row in rows):
#                         all_tables.append(rows)
#             except (AttributeError, RuntimeError, ValueError) as e:
#                 logger.debug(f"PyMuPDF table finder failed on page {page_num}: {e}")
#                 pass
#         body = "\n\n".join(texts).strip()
#         logger.info(f"PyMuPDF extracted {len(texts)} pages and {len(all_tables)} tables")
#         return body, all_tables, "pymupdf"
#     except Exception as e:
#         logger.error(f"PyMuPDF extraction failed: {e}")
#         return "", [], "pymupdf"
#     finally:
#         doc.close()


# def _extract_pypdf_text(path: Path) -> tuple[str, list[list[list[str | None]]], str]:
#     """Baseline pypdf text extraction (no table support)."""
#     if not _PYPDF_AVAILABLE:
#         logger.warning("pypdf requested but not available")
#         return "", [], "pypdf"

#     try:
#         reader = PdfReader(str(path))
#         parts: list[str] = []
#         for page_num, page in enumerate(reader.pages, start=1):
#             try:
#                 t = page.extract_text()
#                 if t:
#                     parts.append(t)
#             except Exception as e:
#                 logger.debug(f"pypdf extraction failed on page {page_num}: {e}")
#                 continue
#         body = "\n\n".join(parts).strip()
#         logger.info(f"pypdf extracted {len(parts)} pages")
#         return body, [], "pypdf"
#     except Exception as e:
#         logger.error(f"pypdf extraction failed: {e}")
#         return "", [], "pypdf"


# def _extract_with_pytesseract(path: Path) -> tuple[str, str]:
#     """OCR extraction using pytesseract as fallback for scanned PDFs."""
#     if not _PYTESSERACT_AVAILABLE:
#         logger.warning("pytesseract requested but not available")
#         return "", "pytesseract"

#     try:
#         import fitz  # PyMuPDF for rendering pages to images

#         texts: list[str] = []
#         doc = fitz.open(str(path))
#         try:
#             for page_num in range(len(doc)):
#                 try:
#                     page = doc[page_num]
#                     # Render page to image
#                     pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # 2x zoom for better OCR
#                     img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

#                     # OCR the image
#                     text = pytesseract.image_to_string(img)
#                     if text.strip():
#                         texts.append(text)
#                 except Exception as e:
#                     logger.debug(f"pytesseract OCR failed on page {page_num + 1}: {e}")
#                     continue
#         finally:
#             doc.close()

#         body = "\n\n".join(texts).strip()
#         logger.info(f"pytesseract OCR extracted {len(texts)} pages")
#         return body, "pytesseract"
#     except Exception as e:
#         logger.error(f"pytesseract OCR extraction failed: {e}")
#         return "", "pytesseract"


# def _extract_with_easyocr(path: Path) -> tuple[str, str]:
#     """OCR extraction using EasyOCR as fallback for scanned PDFs."""
#     if not _EASYOCR_AVAILABLE:
#         logger.warning("easyocr requested but not available")
#         return "", "easyocr"

#     try:
#         import fitz  # PyMuPDF for rendering pages to images

#         reader = easyocr.Reader(['en'], gpu=False)  # Initialize reader
#         texts: list[str] = []
#         doc = fitz.open(str(path))
#         try:
#             for page_num in range(len(doc)):
#                 try:
#                     page = doc[page_num]
#                     # Render page to image
#                     pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
#                     img_bytes = pix.tobytes("png")

#                     # OCR the image
#                     result = reader.readtext(img_bytes)
#                     text = "\n".join([item[1] for item in result])
#                     if text.strip():
#                         texts.append(text)
#                 except Exception as e:
#                     logger.debug(f"easyocr failed on page {page_num + 1}: {e}")
#                     continue
#         finally:
#             doc.close()

#         body = "\n\n".join(texts).strip()
#         logger.info(f"easyocr extracted {len(texts)} pages")
#         return body, "easyocr"
#     except Exception as e:
#         logger.error(f"easyocr extraction failed: {e}")
#         return "", "easyocr"


# def _word_score(text: str) -> int:
#     return len(re.findall(r"[A-Za-z]{3,}", text or ""))


# def _calculate_extraction_confidence(
#     text: str,
#     topics_count: int,
#     exams_count: int,
#     used_ocr: bool = False,
#     tables_extracted: int = 0,
# ) -> float:
#     """
#     Calculate confidence score (0-1) for extraction quality.

#     Factors:
#     - Text quality (word count, alpha ratio)
#     - Structured data completeness (topics, exams)
#     - OCR usage (penalty if OCR was needed)
#     - Table extraction success

#     Returns:
#         Confidence score between 0.0 and 1.0
#     """
#     score = 0.0

#     # Text quality score (0-0.4)
#     word_count = _word_score(text)
#     if word_count > 500:
#         score += 0.4
#     elif word_count > 200:
#         score += 0.3
#     elif word_count > 100:
#         score += 0.2
#     elif word_count > 50:
#         score += 0.1

#     # Alpha ratio check (0-0.2)
#     alpha_chars = sum(ch.isalpha() for ch in text)
#     total_chars = max(1, len(text))
#     alpha_ratio = alpha_chars / total_chars
#     if alpha_ratio > 0.6:
#         score += 0.2
#     elif alpha_ratio > 0.45:
#         score += 0.1

#     # Structured data completeness (0-0.3)
#     if topics_count >= 5:
#         score += 0.15
#     elif topics_count >= 3:
#         score += 0.1
#     elif topics_count >= 1:
#         score += 0.05

#     if exams_count >= 2:
#         score += 0.15
#     elif exams_count >= 1:
#         score += 0.1

#     # Table extraction bonus (0-0.1)
#     if tables_extracted >= 3:
#         score += 0.1
#     elif tables_extracted >= 1:
#         score += 0.05

#     # OCR penalty (0-0.2)
#     if used_ocr:
#         score -= 0.2

#     # Ensure score is within bounds
#     return max(0.0, min(1.0, score))


# def extract_text_from_pdf(path: PathLike) -> tuple[str, dict]:
#     """
#     Extract text from PDF with table-aware merging and OCR fallback.

#     - **pdfplumber**: layout text + multi-strategy table detection (ruled + borderless).
#     - **PyMuPDF**: text + ``find_tables()`` when the runtime supports it.
#     - **pypdf**: baseline text.
#     - **OCR fallback**: pytesseract or EasyOCR when text quality is low (scanned PDFs).

#     Returns:
#         tuple of (extracted_text, metadata_dict) where metadata includes:
#         - extraction_method: library used
#         - confidence_score: quality score (0-1)
#         - tables_extracted: count
#         - used_ocr: boolean
#         - word_score: quality metric

#     Libraries are tried in priority order (configurable via _LIBRARY_PRIORITY).
#     """
#     path = Path(path)
#     if not path.is_file():
#         raise FileNotFoundError(f"PDF not found: {path}")

#     # Sort libraries by priority (higher priority first)
#     libraries = sorted(
#         [
#             ("pdfplumber", _PDFPLUMBER_AVAILABLE, _extract_pdfplumber_text_and_tables),
#             ("pymupdf", _PYMUPDF_AVAILABLE, _extract_pymupdf_text_and_tables),
#             ("pypdf", _PYPDF_AVAILABLE, _extract_pypdf_text),
#         ],
#         key=lambda x: _LIBRARY_PRIORITY.get(x[0], 0),
#         reverse=True,
#     )

#     body_candidates: list[tuple[str, str]] = []  # (text, library_name)
#     all_tables: list[list[list[str | None]]] = []
#     extraction_results: list[str] = []

#     for lib_name, is_available, extract_func in libraries:
#         if not is_available:
#             logger.debug(f"Skipping {lib_name} - not available")
#             continue

#         try:
#             text, tables, used_lib = extract_func(path)
#             if text:
#                 body_candidates.append((text, used_lib))
#                 extraction_results.append(f"{used_lib}: extracted text")
#             if tables:
#                 all_tables.extend(tables)
#                 extraction_results.append(f"{used_lib}: extracted {len(tables)} tables")
#         except Exception as e:
#             logger.error(f"{lib_name} extraction failed: {e}")
#             extraction_results.append(f"{lib_name}: failed - {e}")

#     logger.info(f"Extraction summary: {'; '.join(extraction_results)}")

#     if not body_candidates:
#         logger.warning("No library successfully extracted text from PDF")
#         return "", {"extraction_method": None, "confidence_score": 0.0, "tables_extracted": 0, "used_ocr": False, "word_score": 0}

#     # Select best body text by word count
#     best_body = ""
#     best_lib = ""
#     best_score = -1
#     for cand, lib in body_candidates:
#         s = _word_score(cand)
#         if s > best_score:
#             best_body = cand or ""
#             best_lib = lib
#             best_score = s

#     logger.info(f"Selected {best_lib} for body text (score: {best_score})")

#     used_ocr = False

#     # Check if text quality is low and try OCR fallback
#     if _text_quality_is_low(best_body):
#         logger.warning(f"Text quality is low (score: {best_score}), attempting OCR fallback")
#         ocr_candidates: list[tuple[str, str]] = []

#         # Try pytesseract first
#         if _PYTESSERACT_AVAILABLE:
#             try:
#                 ocr_text, ocr_lib = _extract_with_pytesseract(path)
#                 if ocr_text:
#                     ocr_score = _word_score(ocr_text)
#                     ocr_candidates.append((ocr_text, ocr_lib))
#                     logger.info(f"pytesseract OCR score: {ocr_score}")
#             except Exception as e:
#                 logger.error(f"pytesseract OCR failed: {e}")

#         # Try EasyOCR as fallback
#         if _EASYOCR_AVAILABLE and (not ocr_candidates or _word_score(ocr_candidates[0][0]) < best_score):
#             try:
#                 ocr_text, ocr_lib = _extract_with_easyocr(path)
#                 if ocr_text:
#                     ocr_score = _word_score(ocr_text)
#                     ocr_candidates.append((ocr_text, ocr_lib))
#                     logger.info(f"easyocr score: {ocr_score}")
#             except Exception as e:
#                 logger.error(f"easyocr failed: {e}")

#         # Use OCR if it improves quality significantly
#         if ocr_candidates:
#             best_ocr, best_ocr_lib = max(ocr_candidates, key=lambda x: _word_score(x[0]))
#             ocr_score = _word_score(best_ocr)
#             if ocr_score > best_score * 1.2:  # OCR must be 20% better
#                 logger.info(f"Using {best_ocr_lib} OCR (score: {ocr_score}) over {best_lib} (score: {best_score})")
#                 best_body = best_ocr
#                 best_lib = best_ocr_lib
#                 best_score = ocr_score
#                 used_ocr = True

#     table_md = _tables_to_markdown(all_tables)
#     if table_md:
#         combined = f"{best_body}\n\n---\n\n## Extracted tables\n\n{table_md}".strip()
#     else:
#         combined = best_body.strip()

#     normalized = _normalize_extracted_text(combined)

#     # Calculate confidence score
#     confidence = _calculate_extraction_confidence(
#         normalized,
#         topics_count=0,  # Will be updated after LLM parsing
#         exams_count=0,   # Will be updated after LLM parsing
#         used_ocr=used_ocr,
#         tables_extracted=len(all_tables),
#     )

#     metadata = {
#         "extraction_method": best_lib,
#         "confidence_score": confidence,
#         "tables_extracted": len(all_tables),
#         "used_ocr": used_ocr,
#         "word_score": best_score,
#     }

#     return normalized, metadata


# def _strip_json_fence(raw: str) -> str:
#     """Remove optional ```json ... ``` wrapper from model output."""
#     s = raw.strip()
#     if not s.startswith("```"):
#         return s
#     lines = s.split("\n")
#     if lines and lines[0].startswith("```"):
#         lines = lines[1:]
#     if lines and lines[-1].strip() == "```":
#         lines = lines[:-1]
#     return "\n".join(lines).strip()


# def _repair_malformed_json(raw: str) -> str:
#     """
#     Attempt to repair common JSON malformations from LLM output.

#     Handles:
#     - Trailing commas
#     - Missing quotes around keys
#     - Single quotes instead of double quotes
#     - Unescaped newlines in strings
#     """
#     repaired = raw.strip()

#     # Remove trailing commas before closing brackets/braces
#     repaired = re.sub(r",\s*([}\]])", r"\1", repaired)

#     # Fix single quotes to double quotes (but be careful with contractions)
#     # Only replace quotes that are likely JSON delimiters
#     repaired = re.sub(r"'([^']+)':", r'"\1":', repaired)  # keys
#     repaired = re.sub(r":\s*'([^']+)'", r': "\1"', repaired)  # string values

#     # Fix unescaped newlines in string values
#     repaired = re.sub(r'"\s*\n\s*"', '" "', repaired)

#     # Remove any markdown code blocks that weren't caught
#     repaired = _strip_json_fence(repaired)

#     return repaired


# def _parse_json_with_retry(
#     raw: str,
#     max_retries: int = 3,
#     initial_delay: float = 0.5,
# ) -> dict:
#     """
#     Parse JSON with retry logic and repair attempts.

#     Args:
#         raw: Raw JSON string from LLM
#         max_retries: Maximum number of parsing attempts
#         initial_delay: Initial delay between retries in seconds

#     Returns:
#         Parsed dictionary

#     Raises:
#         ValueError: If JSON cannot be parsed after all retries
#     """
#     last_error = None

#     for attempt in range(max_retries):
#         try:
#             # First attempt: direct parse
#             if attempt == 0:
#                 data = json.loads(raw)
#                 return data

#             # Subsequent attempts: try repair
#             repaired = _repair_malformed_json(raw)
#             data = json.loads(repaired)
#             logger.info(f"JSON repaired successfully on attempt {attempt + 1}")
#             return data

#         except json.JSONDecodeError as e:
#             last_error = e
#             logger.warning(f"JSON parse attempt {attempt + 1} failed: {e}")

#             if attempt < max_retries - 1:
#                 delay = initial_delay * (2 ** attempt)  # Exponential backoff
#                 time.sleep(delay)

#     # All attempts failed - try fallback extraction
#     logger.error("All JSON parse attempts failed, trying fallback extraction")
#     return _fallback_json_extraction(raw, last_error)


# def _fallback_json_extraction(raw: str, original_error: Exception) -> dict:
#     """
#     Fallback extraction for severely malformed JSON.

#     Attempts to extract partial structure using regex patterns.
#     Returns a minimal valid structure that won't crash the parser.
#     """
#     fallback = {
#         "course_title": None,
#         "instructor": None,
#         "term": None,
#         "topics": [],
#         "learning_objectives": [],
#         "exam_dates": [],
#     }

#     try:
#         # Try to extract course title
#         title_match = re.search(r'"course_title"\s*:\s*"([^"]+)"', raw, re.IGNORECASE)
#         if title_match:
#             fallback["course_title"] = title_match.group(1)

#         # Try to extract instructor
#         instructor_match = re.search(r'"instructor"\s*:\s*"([^"]+)"', raw, re.IGNORECASE)
#         if instructor_match:
#             fallback["instructor"] = instructor_match.group(1)

#         # Try to extract term
#         term_match = re.search(r'"term"\s*:\s*"([^"]+)"', raw, re.IGNORECASE)
#         if term_match:
#             fallback["term"] = term_match.group(1)

#         logger.warning("Using fallback extraction - partial data recovered")
#         return fallback

#     except Exception as e:
#         logger.error(f"Fallback extraction also failed: {e}")
#         # Return empty structure to prevent crash
#         return fallback


# def _parse_syllabus_via_json_object(llm: object, truncated: str) -> ParsedSyllabus:
#     """
#     Parse syllabus using JSON-object mode (Groq / providers that reject ``json_schema``).

#     LangChain's ``with_structured_output`` often emits ``response_format: json_schema``,
#     which Groq does not support for many models.
#     """
#     from langchain_core.prompts import ChatPromptTemplate

#     prompt = ChatPromptTemplate.from_messages(
#         [
#             (
#                 "system",
#                 "You extract structured data from university course syllabi. "
#                 "Respond with a single JSON object only (no markdown, no prose). "
#                 "Keys: course_title (string|null), instructor (string|null), term (string|null), "
#                 "topics (array of objects with title, description|null, weightage_percent|null, "
#                 "learning_objectives array of strings, week_or_unit|null, estimated_hours|null), "
#                 "learning_objectives (array of strings), "
#                 "exam_dates (array of objects with name, date as YYYY-MM-DD|null, "
#                 "weightage_percent|null, notes|null). "
#                 "Infer exam dates only when explicitly stated; otherwise use null for date. "
#                 "Topics should reflect major units or modules, in syllabus order when possible. "
#                 "When the syllabus begins with a USER FOCUS QUESTION and excerpts, "
#                 "extract topics and exams that relate to that focus, but still return the "
#                 "full course JSON (all major units and exams you can infer from the full text). "
#                 "Use raw_text as empty string \"\". "
#                 "Do not wrap the JSON in code fences.",
#             ),
#             (
#                 "human",
#                 "Parse this syllabus text.\n\n{syllabus}\n\n"
#                 "If text seems partial, still extract whatever topic and exam signals exist.",
#             ),
#         ]
#     )
#     # Use prompt.format() + llm.invoke() for better compatibility
#     formatted_prompt = prompt.format(syllabus=truncated)
#     msg = llm.invoke(formatted_prompt)
#     raw = str(getattr(msg, "content", msg))
#     try:
#         data = _parse_json_with_retry(raw)
#     except Exception as exc:
#         raise ValueError(
#             "Syllabus model returned invalid JSON after all repair attempts. "
#             "Try a different model or shorten the syllabus input."
#         ) from exc
#     return ParsedSyllabus.model_validate(data)


# def _parse_with_llm_single(text: str, max_chars: int = _DEFAULT_MAX_CHARS_FOR_LLM) -> ParsedSyllabus:
#     """Use an OpenAI-compatible chat model to fill ``ParsedSyllabus`` with provider fallback."""
#     from langchain_core.prompts import ChatPromptTemplate
#     from langchain_openai import ChatOpenAI

#     truncated = text[:max_chars]
#     if len(text) > max_chars:
#         truncated += "\n\n[... document truncated for model context ...]"

#     last_error = None

#     # Try providers in fallback order
#     for provider_name in _LLM_PROVIDER_FALLBACK:
#         try:
#             logger.info(f"Attempting LLM parsing with provider: {provider_name}")

#             # Get provider config (use current provider if it matches, otherwise try to configure)
#             try:
#                 provider = get_llm_provider_config()
#                 if provider.provider != provider_name and provider_name != "local":
#                     logger.debug(f"Skipping {provider_name} - current provider is {provider.provider}")
#                     continue
#             except Exception as e:
#                 logger.warning(f"Could not get provider config for {provider_name}: {e}")
#                 if provider_name == "local":
#                     # Try to use local model configuration
#                     provider = type('Provider', (), {
#                         'provider': 'local',
#                         'api_key': 'dummy',
#                         'base_url': 'http://localhost:11434/v1'
#                     })()
#                 else:
#                     continue

#             llm = ChatOpenAI(
#                 model=os.getenv("OPENAI_SYLLABUS_MODEL", "mistralai/mistral-7b-instruct"),
#                 temperature=0,
#                 api_key=provider.api_key,
#                 base_url=provider.base_url,
#             )

#             # Groq: many models do not support response_format json_schema (used by with_structured_output).
#             if provider.provider == "groq":
#                 llm_json = ChatOpenAI(
#                     model=os.getenv("OPENAI_SYLLABUS_MODEL", "mistralai/mistral-7b-instruct"),
#                     temperature=0,
#                     api_key=provider.api_key,
#                     base_url=provider.base_url,
#                     model_kwargs={"response_format": {"type": "json_object"}},
#                 )
#                 result = _parse_syllabus_via_json_object(llm_json, truncated)
#                 logger.info(f"Successfully parsed with provider: {provider_name}")
#                 return result

#             structured = llm.with_structured_output(ParsedSyllabus)

#             prompt = ChatPromptTemplate.from_messages(
#                 [
#                     (
#                         "system",
#                         "You extract structured data from university course syllabi. "
#                         "Infer exam dates only when explicitly stated; otherwise leave date null. "
#                         "Topics should reflect major units or modules, in syllabus order when possible. "
#                         "If the syllabus begins with a USER FOCUS QUESTION and ranked excerpts, still "
#                         "return the full course structure, but weight extraction toward content that "
#                         "answers that question. "
#                         "Return concise, clean topic names without duplicates. "
#                         "Leave raw_text empty; the application fills it after parsing.",
#                     ),
#                     (
#                         "human",
#                         "Parse this syllabus text into the required schema.\n\n{syllabus}\n\n"
#                         "If text seems partial, still extract whatever topic and exam signals exist.",
#                     ),
#                 ]
#             )
#             # Use prompt.format() + llm.invoke() for better compatibility
#             formatted_prompt = prompt.format(syllabus=truncated)
#             result: ParsedSyllabus = structured.invoke(formatted_prompt)
#             logger.info(f"Successfully parsed with provider: {provider_name}")
#             return result

#         except Exception as e:
#             last_error = e
#             logger.warning(f"Provider {provider_name} failed: {e}")
#             continue

#     # All providers failed
#     logger.error(f"All LLM providers failed. Last error: {last_error}")
#     raise ValueError(
#         f"All LLM providers in fallback chain failed. "
#         f"Providers tried: {_LLM_PROVIDER_FALLBACK}. "
#         f"Last error: {last_error}"
#     )


# def _parse_with_llm(text: str, max_chars: int = _DEFAULT_MAX_CHARS_FOR_LLM) -> ParsedSyllabus:
#     """Chunk-aware LLM parsing for better large/noisy syllabus coverage."""
#     chunks = _chunk_text(text, max_chars=max_chars)
#     if len(chunks) == 1:
#         return _parse_with_llm_single(chunks[0], max_chars=max_chars)

#     merged = ParsedSyllabus()
#     # Parse at most first 2 chunks (reduced from 3 to prevent token limits)
#     # to balance quality, latency and cost
#     for i, chunk in enumerate(chunks[:2]):
#         logger.info(f"Parsing chunk {i+1}/2 (size: {len(chunk)} chars)")
#         parsed_chunk = _parse_with_llm_single(chunk, max_chars=max_chars)
#         _merge_unique_topics(merged, parsed_chunk)
#     return merged


# def parse_syllabus_pdf(
#     path: PathLike,
#     *,
#     use_llm: bool = True,
#     max_chars_for_llm: int = _DEFAULT_MAX_CHARS_FOR_LLM,
#     focus_query: str | None = None,
# ) -> ParsedSyllabus:
#     """
#     Load a syllabus PDF, extract text, and optionally fill structured fields with an LLM.

#     ``focus_query`` (e.g. the user's optional RAG question) is used only when ``use_llm`` is
#     True: the PDF text is keyword-ranked and the most relevant excerpts are prepended so the
#     model prioritizes those sections while still seeing the full syllabus (truncated to budget).

#     If ``use_llm`` is False, returns a ``ParsedSyllabus`` with ``raw_text`` set and
#     empty topics/exams (suitable for RAG-only flows without API cost).

#     Now includes extraction confidence scoring and metadata tracking.
#     """
#     path = Path(path)
#     raw, extraction_metadata = extract_text_from_pdf(path)

#     if not use_llm:
#         return ParsedSyllabus(
#             raw_text=raw,
#             source_path=str(path.resolve()),
#             extraction_confidence=extraction_metadata.get("confidence_score", 0.0),
#             extraction_method=extraction_metadata.get("extraction_method"),
#             extraction_metadata=extraction_metadata,
#         )

#     if _text_quality_is_low(raw):
#         # Extraction is weak; still proceed, but parsed structure may be sparse.
#         # The caller UI surfaces quality hints and fallback enrichment.
#         logger.warning(
#             "Low text quality detected in '%s' — LLM parsing results may be sparse. "
#             "Consider a higher-quality PDF or enable OCR.",
#             path.name,
#         )
#     llm_input = _compose_llm_input_for_parse(raw, focus_query, max_chars_for_llm)
#     parsed = _parse_with_llm(llm_input, max_chars=max_chars_for_llm)
#     parsed.raw_text = raw
#     parsed.source_path = str(path.resolve())

#     # Update confidence score with LLM parsing results
#     parsed.extraction_confidence = _calculate_extraction_confidence(
#         raw,
#         topics_count=len(parsed.topics),
#         exams_count=len(parsed.exam_dates),
#         used_ocr=extraction_metadata.get("used_ocr", False),
#         tables_extracted=extraction_metadata.get("tables_extracted", 0),
#     )
#     parsed.extraction_method = extraction_metadata.get("extraction_method")
#     parsed.extraction_metadata = extraction_metadata

#     return parsed


# def save_parsed_syllabus(data: ParsedSyllabus, json_path: PathLike) -> None:
#     """Write ``ParsedSyllabus`` to JSON (excludes huge raw_text if you strip it first)."""
#     out = Path(json_path)
#     out.parent.mkdir(parents=True, exist_ok=True)
#     out.write_text(data.model_dump_json(indent=2), encoding="utf-8")


# def load_parsed_syllabus(json_path: PathLike) -> ParsedSyllabus:
#     """Load ``ParsedSyllabus`` from JSON."""
#     path = Path(json_path)
#     return ParsedSyllabus.model_validate_json(path.read_text(encoding="utf-8"))


# def parse_syllabus_from_text(
#     text: str, *, use_llm: bool = True, focus_query: str | None = None
# ) -> ParsedSyllabus:
#     """Parse already-extracted syllabus text (e.g. from OCR or paste)."""
#     text = _normalize_extracted_text(text.strip())
#     if not use_llm:
#         return ParsedSyllabus(raw_text=text)
#     llm_input = _compose_llm_input_for_parse(text, focus_query, _DEFAULT_MAX_CHARS_FOR_LLM)
#     parsed = _parse_with_llm(llm_input)
#     parsed.raw_text = text
#     return parsed


#Updated code of UpdatedParser.py

"""
Syllabus parsing: extract text from PDFs and structure data using rule-based heuristics.

No LLM is used in this module. All extraction is done via regex, heuristics,
and PDF libraries (pdfplumber, PyMuPDF, pypdf).
"""
# from __future__ import annotations
from models.syllabus import dt_date, ParsedSyllabus, Subject, Topic


import calendar
import logging
import re
import sys
from datetime import date
from pathlib import Path
from typing import List, Optional, Union

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from pypdf import PdfReader

from models.syllabus import dt_date, ParsedSyllabus, Subject, Topic

logger = logging.getLogger(__name__)

_PDFPLUMBER_AVAILABLE = False
_PYMUPDF_AVAILABLE = False
_PYPDF_AVAILABLE = True
_PYTESSERACT_AVAILABLE = False
_EASYOCR_AVAILABLE = False

try:
    import pdfplumber
    _PDFPLUMBER_AVAILABLE = True
except ImportError:
    logger.warning("pdfplumber not available - table extraction will be limited")

try:
    import fitz
    _PYMUPDF_AVAILABLE = True
except ImportError:
    logger.warning("PyMuPDF (fitz) not available - alternative extraction will be used")

try:
    import pytesseract
    from PIL import Image
    _PYTESSERACT_AVAILABLE = True
except ImportError:
    logger.debug("pytesseract not available - OCR fallback will be limited")

try:
    import easyocr
    _EASYOCR_AVAILABLE = True
except ImportError:
    logger.debug("easyocr not available - OCR fallback will be limited")

PathLike = Union[str, Path]

_LIBRARY_PRIORITY = {
    "pdfplumber": 3,
    "pymupdf": 2,
    "pypdf": 1,
}


def get_library_status() -> dict[str, bool]:
    return {
        "pdfplumber": _PDFPLUMBER_AVAILABLE,
        "pymupdf": _PYMUPDF_AVAILABLE,
        "pypdf": _PYPDF_AVAILABLE,
    }


def set_library_priority(library: str, priority: int) -> None:
    if library not in _LIBRARY_PRIORITY:
        raise ValueError(
            f"Unknown library: {library}. Must be one of {list(_LIBRARY_PRIORITY.keys())}"
        )
    _LIBRARY_PRIORITY[library] = priority
    logger.info(f"Set {library} priority to {priority}")


def get_library_priority() -> dict[str, int]:
    return _LIBRARY_PRIORITY.copy()

_ORDINAL_TO_INT: dict[str, int] = {
    "first": 1, "second": 2, "third": 3, "fourth": 4,
    "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
    "1st": 1, "2nd": 2, "3rd": 3, "4th": 4,
    "5th": 5, "6th": 6, "7th": 7, "8th": 8,
}

_RETRIEVAL_KEYWORDS = frozenset([
    "semester", "sem", "subject", "subjects", "course", "courses",
    "extract", "list", "show", "get", "fetch", "find", "give",
])


def extract_subjects(
    raw_text: str,
    semester: Optional[int] = None,
    course_code: Optional[str] = None,
) -> List[Subject]:
    semester_patterns = {
        "FIRST SEMESTER": 1, "SECOND SEMESTER": 2, "THIRD SEMESTER": 3,
        "FOURTH SEMESTER": 4, "FIFTH SEMESTER": 5, "SIXTH SEMESTER": 6,
        "SEVENTH SEMESTER": 7, "EIGHTH SEMESTER": 8,
    }
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    current_semester = None
    current_category = "THEORY"
    results = []
    subject_pattern = re.compile(
        r"^([A-Za-z]{2,}[-\s]?\d{3}(?:\(P\))?)\s+(.+?)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)$"
    )
    for line in lines:
        upper_line = line.upper()
        if upper_line in semester_patterns:
            current_semester = semester_patterns[upper_line]
            continue
        if upper_line == "THEORETICAL":
            current_category = "THEORY"
            continue
        if upper_line == "PRACTICAL":
            current_category = "PRACTICAL"
            continue
        match = subject_pattern.match(line)
        if match:
            extracted_course_code = match.group(1).strip()
            subject_name = match.group(2).strip()
            l, t, p, credits = (int(match.group(i)) for i in range(3, 7))
            subject_data = Subject(
                semester=current_semester, course_code=extracted_course_code,
                subject=subject_name, credits=credits, lecture=l, tutorial=t,
                practical=p, category=current_category,
            )
            if semester is not None:
                if current_semester == semester:
                    results.append(subject_data)
            elif course_code is not None:
                if extracted_course_code.upper() == course_code.upper():
                    results.append(subject_data)
            else:
                results.append(subject_data)
    return results


def _parse_retrieval_intent(query: str) -> Optional[dict]:
    if not query or not query.strip():
        return None
    q = query.strip().lower()
    words = set(re.findall(r"[a-z]+", q))
    if not words & _RETRIEVAL_KEYWORDS:
        return None
    if re.search(r"\ball\b", q) and words & {"subject", "subjects", "course", "courses"}:
        return {"type": "all", "value": None}
    m = re.search(r"(?:semester|sem)\s*(\d)", q)
    if m:
        return {"type": "semester", "value": int(m.group(1))}
    m = re.search(r"(\d)(?:st|nd|rd|th)\s+(?:semester|sem)", q)
    if m:
        return {"type": "semester", "value": int(m.group(1))}
    m = re.search(
        r"(first|second|third|fourth|fifth|sixth|seventh|eighth)\s+(?:semester|sem)",
        q,
    )
    if m:
        val = _ORDINAL_TO_INT.get(m.group(1))
        if val:
            return {"type": "semester", "value": val}
    for word, num in _ORDINAL_TO_INT.items():
        if word in q and ("sem" in q or "semester" in q):
            return {"type": "semester", "value": num}
    m = re.search(r"\b([A-Za-z]{2,}[-]?\d{3}(?:\(P\))?)\b", q)
    if m:
        return {"type": "course_code", "value": m.group(1).upper()}
    if words & {"subject", "subjects", "course", "courses"}:
        return {"type": "all", "value": None}
    return None


# ---------------------------------------------------------------------------
# Text normalization and quality
# ---------------------------------------------------------------------------


def _normalize_extracted_text(text: str) -> str:
    if not text:
        return ""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned_lines: list[str] = []
    for raw_line in normalized.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        if re.fullmatch(r"(page\s*)?\d{1,3}(\s*of\s*\d{1,3})?", line, flags=re.IGNORECASE):
            continue
        cleaned_lines.append(line)
    normalized = "\n".join(cleaned_lines)
    normalized = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
    return normalized


def _text_quality_is_low(text: str) -> bool:
    if not text.strip():
        return True
    words = re.findall(r"[A-Za-z]{3,}", text)
    if len(words) < 80:
        return True
    lines = [line for line in text.splitlines() if line.strip()]
    alpha_chars = sum(ch.isalpha() for ch in text)
    total_chars = max(1, len(text))
    alpha_ratio = alpha_chars / total_chars
    short_line_ratio = sum(1 for line in lines if len(line.strip()) <= 2) / max(1, len(lines))
    return alpha_ratio < 0.45 or short_line_ratio > 0.12


def _word_score(text: str) -> int:
    return len(re.findall(r"[A-Za-z]{3,}", text or ""))

# ---------------------------------------------------------------------------
# Table extraction helpers
# ---------------------------------------------------------------------------


def _sanitize_md_cell(cell: str) -> str:
    return cell.replace("|", "\u2223").replace("\n", " ").strip()


def _table_fingerprint(table: list[list[str | None]]) -> str:
    if not table:
        return ""
    rows = table[:3]
    parts: list[str] = []
    for row in rows:
        parts.append("|".join((("" if c is None else str(c)).strip() for c in row)))
    return "|".join(parts)[:800]


def _dedupe_tables(tables: list[list[list[str | None]]]) -> list[list[list[str | None]]]:
    seen: set[str] = set()
    out: list[list[list[str | None]]] = []
    for table in tables:
        fp = _table_fingerprint(table)
        if not fp or fp in seen:
            continue
        seen.add(fp)
        out.append(table)
    return out


def _tables_to_markdown(tables: list[list[list[str | None]]]) -> str:
    blocks: list[str] = []
    deduped = _dedupe_tables(tables)
    for t_idx, table in enumerate(deduped, start=1):
        if not table or not any(row for row in table):
            continue
        norm_rows: list[list[str]] = []
        for row in table:
            norm_rows.append([_sanitize_md_cell("" if c is None else str(c)) for c in row])
        if sum(1 for r in norm_rows for c in r if c) < 3:
            continue
        header = norm_rows[0]
        col_count = max(1, len(header))
        header = (header + [""] * col_count)[:col_count]
        sep = ["---"] * col_count
        body = norm_rows[1:] if len(norm_rows) > 1 else []
        md = [f"[Table {t_idx}]", "|" + "|".join(header) + "|", "|" + "|".join(sep) + "|"]
        for row in body[:80]:
            row = (row + [""] * col_count)[:col_count]
            md.append("|" + "|".join(row) + "|")
        blocks.append("\n".join(md))
    return "\n\n".join(blocks).strip()


def _pdfplumber_table_setting_presets() -> list[dict[str, object]]:
    return [
        {
            "vertical_strategy": "lines", "horizontal_strategy": "lines",
            "intersection_tolerance": 4, "snap_tolerance": 3,
            "join_tolerance": 3, "edge_min_length": 3,
        },
        {
            "vertical_strategy": "lines", "horizontal_strategy": "text",
            "intersection_tolerance": 5, "snap_tolerance": 4,
            "join_tolerance": 4, "text_x_tolerance": 2, "text_y_tolerance": 2,
        },
        {
            "vertical_strategy": "text", "horizontal_strategy": "text",
            "intersection_tolerance": 5, "snap_tolerance": 4,
            "join_tolerance": 4, "min_words_vertical": 2, "min_words_horizontal": 1,
        },
    ]


def _extract_tables_pdfplumber_page(
    page: object, settings: dict[str, object]
) -> list[list[list[str | None]]]:
    out: list[list[list[str | None]]] = []
    try:
        raw = page.extract_tables(table_settings=settings)
    except TypeError:
        raw = page.extract_tables()
    if not raw:
        return out
    for table in raw:
        if table and any(any(c not in (None, "") for c in row) for row in table):
            out.append(table)
    return out


def _extract_pdfplumber_text_and_tables(
    path: Path,
) -> tuple[str, list[list[list[str | None]]], str]:
    if not _PDFPLUMBER_AVAILABLE:
        logger.warning("pdfplumber requested but not available")
        return "", [], "pdfplumber"
    import pdfplumber
    texts: list[str] = []
    all_tables: list[list[list[str | None]]] = []
    presets = _pdfplumber_table_setting_presets()
    try:
        with pdfplumber.open(str(path)) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                try:
                    txt = page.extract_text(x_tolerance=2, y_tolerance=2, layout=True)
                except TypeError:
                    txt = page.extract_text(x_tolerance=2, y_tolerance=2)
                if txt:
                    texts.append(txt)
                for preset in presets:
                    try:
                        all_tables.extend(_extract_tables_pdfplumber_page(page, preset))
                    except Exception as e:
                        logger.debug(f"pdfplumber table failed on page {page_num}: {e}")
                        continue
        body = "\n\n".join(texts).strip()
        logger.info(f"pdfplumber extracted {len(texts)} pages and {len(all_tables)} tables")
        return body, all_tables, "pdfplumber"
    except Exception as e:
        logger.error(f"pdfplumber extraction failed: {e}")
        return "", [], "pdfplumber"


def _extract_pymupdf_text_and_tables(
    path: Path,
) -> tuple[str, list[list[list[str | None]]], str]:
    if not _PYMUPDF_AVAILABLE:
        logger.warning("PyMuPDF requested but not available")
        return "", [], "pymupdf"
    import fitz
    texts: list[str] = []
    all_tables: list[list[list[str | None]]] = []
    doc = fitz.open(str(path))
    try:
        for page_num, page in enumerate(doc, start=1):
            t = page.get_text("text")
            if t:
                texts.append(t)
            try:
                finder = page.find_tables()
                for tab in getattr(finder, "tables", []) or []:
                    try:
                        rows = tab.extract()
                    except Exception as e:
                        logger.debug(f"PyMuPDF table failed on page {page_num}: {e}")
                        continue
                    if rows and any(any(str(c).strip() for c in row) for row in rows):
                        all_tables.append(rows)
            except (AttributeError, RuntimeError, ValueError) as e:
                logger.debug(f"PyMuPDF table finder failed on page {page_num}: {e}")
        body = "\n\n".join(texts).strip()
        logger.info(f"PyMuPDF extracted {len(texts)} pages and {len(all_tables)} tables")
        return body, all_tables, "pymupdf"
    except Exception as e:
        logger.error(f"PyMuPDF extraction failed: {e}")
        return "", [], "pymupdf"
    finally:
        doc.close()


def _extract_pypdf_text(path: Path) -> tuple[str, list[list[list[str | None]]], str]:
    if not _PYPDF_AVAILABLE:
        logger.warning("pypdf requested but not available")
        return "", [], "pypdf"
    try:
        reader = PdfReader(str(path))
        parts: list[str] = []
        for page_num, page in enumerate(reader.pages, start=1):
            try:
                t = page.extract_text()
                if t:
                    parts.append(t)
            except Exception as e:
                logger.debug(f"pypdf extraction failed on page {page_num}: {e}")
                continue
        body = "\n\n".join(parts).strip()
        logger.info(f"pypdf extracted {len(parts)} pages")
        return body, [], "pypdf"
    except Exception as e:
        logger.error(f"pypdf extraction failed: {e}")
        return "", [], "pypdf"


def _extract_with_pytesseract(path: Path) -> tuple[str, str]:
    if not _PYTESSERACT_AVAILABLE:
        logger.warning("pytesseract requested but not available")
        return "", "pytesseract"
    try:
        import fitz
        texts: list[str] = []
        doc = fitz.open(str(path))
        try:
            for page_num in range(len(doc)):
                try:
                    page = doc[page_num]
                    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    text = pytesseract.image_to_string(img)
                    if text.strip():
                        texts.append(text)
                except Exception as e:
                    logger.debug(f"pytesseract OCR failed on page {page_num + 1}: {e}")
                    continue
        finally:
            doc.close()
        body = "\n\n".join(texts).strip()
        logger.info(f"pytesseract OCR extracted {len(texts)} pages")
        return body, "pytesseract"
    except Exception as e:
        logger.error(f"pytesseract OCR extraction failed: {e}")
        return "", "pytesseract"


def _extract_with_easyocr(path: Path) -> tuple[str, str]:
    if not _EASYOCR_AVAILABLE:
        logger.warning("easyocr requested but not available")
        return "", "easyocr"
    try:
        import fitz
        reader = easyocr.Reader(["en"], gpu=False)
        texts: list[str] = []
        doc = fitz.open(str(path))
        try:
            for page_num in range(len(doc)):
                try:
                    page = doc[page_num]
                    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                    img_bytes = pix.tobytes("png")
                    result = reader.readtext(img_bytes)
                    text = "\n".join([item[1] for item in result])
                    if text.strip():
                        texts.append(text)
                except Exception as e:
                    logger.debug(f"easyocr failed on page {page_num + 1}: {e}")
                    continue
        finally:
            doc.close()
        body = "\n\n".join(texts).strip()
        logger.info(f"easyocr extracted {len(texts)} pages")
        return body, "easyocr"
    except Exception as e:
        logger.error(f"easyocr extraction failed: {e}")
        return "", "easyocr"

# ---------------------------------------------------------------------------
# Extraction confidence scoring
# ---------------------------------------------------------------------------


def _calculate_extraction_confidence(
    text: str,
    topics_count: int,
    exams_count: int,
    used_ocr: bool = False,
    tables_extracted: int = 0,
) -> float:
    score = 0.0
    word_count = _word_score(text)
    if word_count > 500:
        score += 0.4
    elif word_count > 200:
        score += 0.3
    elif word_count > 100:
        score += 0.2
    elif word_count > 50:
        score += 0.1

    alpha_chars = sum(ch.isalpha() for ch in text)
    total_chars = max(1, len(text))
    alpha_ratio = alpha_chars / total_chars
    if alpha_ratio > 0.6:
        score += 0.2
    elif alpha_ratio > 0.45:
        score += 0.1

    if topics_count >= 5:
        score += 0.15
    elif topics_count >= 3:
        score += 0.1
    elif topics_count >= 1:
        score += 0.05

    if exams_count >= 2:
        score += 0.15
    elif exams_count >= 1:
        score += 0.1

    if tables_extracted >= 3:
        score += 0.1
    elif tables_extracted >= 1:
        score += 0.05

    if used_ocr:
        score -= 0.2

    return max(0.0, min(1.0, score))


# ---------------------------------------------------------------------------
# Main PDF text extraction
# ---------------------------------------------------------------------------


def extract_text_from_pdf(path: PathLike) -> tuple[str, dict]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"PDF not found: {path}")

    libraries = sorted(
        [
            ("pdfplumber", _PDFPLUMBER_AVAILABLE, _extract_pdfplumber_text_and_tables),
            ("pymupdf", _PYMUPDF_AVAILABLE, _extract_pymupdf_text_and_tables),
            ("pypdf", _PYPDF_AVAILABLE, _extract_pypdf_text),
        ],
        key=lambda x: _LIBRARY_PRIORITY.get(x[0], 0),
        reverse=True,
    )

    body_candidates: list[tuple[str, str]] = []
    all_tables: list[list[list[str | None]]] = []
    extraction_results: list[str] = []

    for lib_name, is_available, extract_func in libraries:
        if not is_available:
            logger.debug(f"Skipping {lib_name} - not available")
            continue
        try:
            text, tables, used_lib = extract_func(path)
            if text:
                body_candidates.append((text, used_lib))
                extraction_results.append(f"{used_lib}: extracted text")
            if tables:
                all_tables.extend(tables)
                extraction_results.append(f"{used_lib}: extracted {len(tables)} tables")
        except Exception as e:
            logger.error(f"{lib_name} extraction failed: {e}")
            extraction_results.append(f"{lib_name}: failed - {e}")

    logger.info(f"Extraction summary: {'; '.join(extraction_results)}")

    if not body_candidates:
        logger.warning("No library successfully extracted text from PDF")
        return "", {
            "extraction_method": None, "confidence_score": 0.0,
            "tables_extracted": 0, "used_ocr": False, "word_score": 0,
        }

    best_body = ""
    best_lib = ""
    best_score = -1
    for cand, lib in body_candidates:
        s = _word_score(cand)
        if s > best_score:
            best_body = cand or ""
            best_lib = lib
            best_score = s

    logger.info(f"Selected {best_lib} for body text (score: {best_score})")

    used_ocr = False
    if _text_quality_is_low(best_body):
        logger.warning(f"Text quality is low (score: {best_score}), attempting OCR fallback")
        ocr_candidates: list[tuple[str, str]] = []
        if _PYTESSERACT_AVAILABLE:
            try:
                ocr_text, ocr_lib = _extract_with_pytesseract(path)
                if ocr_text:
                    ocr_candidates.append((ocr_text, ocr_lib))
            except Exception as e:
                logger.error(f"pytesseract OCR failed: {e}")
        if _EASYOCR_AVAILABLE and (not ocr_candidates or _word_score(ocr_candidates[0][0]) < best_score):
            try:
                ocr_text, ocr_lib = _extract_with_easyocr(path)
                if ocr_text:
                    ocr_candidates.append((ocr_text, ocr_lib))
            except Exception as e:
                logger.error(f"easyocr failed: {e}")
        if ocr_candidates:
            best_ocr, best_ocr_lib = max(ocr_candidates, key=lambda x: _word_score(x[0]))
            ocr_score = _word_score(best_ocr)
            if ocr_score > best_score * 1.2:
                logger.info(f"Using {best_ocr_lib} OCR (score: {ocr_score}) over {best_lib} (score: {best_score})")
                best_body = best_ocr
                best_lib = best_ocr_lib
                best_score = ocr_score
                used_ocr = True

    table_md = _tables_to_markdown(all_tables)
    if table_md:
        combined = f"{best_body}\n\n---\n\n## Extracted tables\n\n{table_md}".strip()
    else:
        combined = best_body.strip()

    normalized = _normalize_extracted_text(combined)
    confidence = _calculate_extraction_confidence(
        normalized, topics_count=0, exams_count=0,
        used_ocr=used_ocr, tables_extracted=len(all_tables),
    )

    metadata = {
        "extraction_method": best_lib, "confidence_score": confidence,
        "tables_extracted": len(all_tables), "used_ocr": used_ocr,
        "word_score": best_score,
    }
    return normalized, metadata

# ---------------------------------------------------------------------------
# Heuristic extractors (rule-based, no LLM)
# ---------------------------------------------------------------------------

_MONTH_NAMES = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]
_MONTH_ABBR = [
    "jan", "feb", "mar", "apr", "may", "jun",
    "jul", "aug", "sep", "sept", "oct", "nov", "dec",
]

_TOPIC_HEADER_PATTERNS = [
    re.compile(r"^(?:Unit|UNIT)\s+(\d+[a-zA-Z]?)\s*[:\-\.]\s*(.+)", re.IGNORECASE),
    re.compile(r"^(?:Week|WEEK)\s+(\d+[a-zA-Z]?)\s*[:\-\.]\s*(.+)", re.IGNORECASE),
    re.compile(r"^(?:Module|MODULE)\s+(\d+[a-zA-Z]?)\s*[:\-\.]\s*(.+)", re.IGNORECASE),
    re.compile(r"^(?:Chapter|CHAPTER)\s+(\d+[a-zA-Z]?)\s*[:\-\.]\s*(.+)", re.IGNORECASE),
    re.compile(r"^(?:Topic|TOPIC)\s+(\d+[a-zA-Z]?)\s*[:\-\.]\s*(.+)", re.IGNORECASE),
    re.compile(r"^(?:Section|SECTION)?\s*([IVXivx]+)\s*[:\-\.]\s*(.+)", re.IGNORECASE),
    re.compile(r"^(\d+(?:\.\d+)*)\s*[:\-\.)]\s*(.+)", re.IGNORECASE),
    re.compile(r"^([A-Z][A-Z\s\-&]{2,}[A-Z])\s*$"),
]

_EXAM_KEYWORDS = [
    "midterm", "final", "quiz", "exam", "test", "assignment",
    "project", "paper", "presentation", "homework",
]


def _parse_date_string(date_str: str, default_year: int | None = None) -> date | None:
    date_str = date_str.strip()
    if not date_str:
        return None
    # ISO: YYYY-MM-DD
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", date_str)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    # DD/MM/YYYY or DD-MM-YYYY
    m = re.match(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", date_str)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    # MM/DD/YYYY
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", date_str)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass
    # Month DD, YYYY
    m = re.match(r"([A-Za-z]{3,})\s+(\d{1,2})(?:[a-z]{2})?,?\s+(\d{4})", date_str)
    if m:
        month_str, day, year = m.group(1).lower(), int(m.group(2)), int(m.group(3))
        for i, mn in enumerate(_MONTH_NAMES, 1):
            if mn.startswith(month_str):
                try:
                    return date(year, i, day)
                except ValueError:
                    pass
        for i, ma in enumerate(_MONTH_ABBR, 1):
            if ma == month_str:
                try:
                    return date(year, i, day)
                except ValueError:
                    pass
    # Month DD (no year)
    if default_year:
        m = re.match(r"([A-Za-z]{3,})\s+(\d{1,2})(?:[a-z]{2})?\b", date_str)
        if m:
            month_str, day = m.group(1).lower(), int(m.group(2))
            for i, mn in enumerate(_MONTH_NAMES, 1):
                if mn.startswith(month_str):
                    try:
                        return date(default_year, i, day)
                    except ValueError:
                        pass
            for i, ma in enumerate(_MONTH_ABBR, 1):
                if ma == month_str:
                    try:
                        return date(default_year, i, day)
                    except ValueError:
                        pass
    return None


def _extract_course_title_heuristic(raw_text: str) -> str | None:
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if not lines:
        return None
    title_patterns = [
        re.compile(r"(?:Course\s+Title|COURSE\s+TITLE)[:\s]+(.+)", re.IGNORECASE),
        re.compile(r"(?:Course|COURSE)[:\s]+(.+)", re.IGNORECASE),
        re.compile(r"(?:Title|TITLE)[:\s]+(.+)", re.IGNORECASE),
        re.compile(r"(?:Course\s+Name|COURSE\s+NAME)[:\s]+(.+)", re.IGNORECASE),
    ]
    for pattern in title_patterns:
        m = pattern.search(raw_text)
        if m:
            title = m.group(1).strip()
            if len(title) > 3:
                return title.rstrip("*#-=")
    first_line = lines[0]
    if len(first_line) > 5 and len(first_line) < 120:
        alpha_ratio = sum(c.isalpha() or c.isspace() for c in first_line) / max(1, len(first_line))
        if alpha_ratio > 0.7:
            return first_line.rstrip("*#-=")
    return None


def _extract_instructor_heuristic(raw_text: str) -> str | None:
    instructor_patterns = [
        re.compile(r"(?:Instructor|INSTRUCTOR)[:\s]+([A-Za-z\s\.\-]+?)(?:\n|$|Email|Office|Phone)", re.IGNORECASE),
        re.compile(r"(?:Professor|PROFESSOR)[:\s]+([A-Za-z\s\.\-]+?)(?:\n|$|Email|Office)", re.IGNORECASE),
        re.compile(r"(?:Faculty|FACULTY)[:\s]+([A-Za-z\s\.\-]+?)(?:\n|$|Email|Office)", re.IGNORECASE),
        re.compile(r"(?:Teacher|TEACHER)[:\s]+([A-Za-z\s\.\-]+?)(?:\n|$|Email|Office)", re.IGNORECASE),
    ]
    for pattern in instructor_patterns:
        m = pattern.search(raw_text)
        if m:
            name = m.group(1).strip()
            if len(name) > 2:
                return name.rstrip("*#-=:")
    m = re.search(r"\b(Dr\.?\s+[A-Za-z\s\.\-]{2,40})(?:\n|,|$|\s{2,})", raw_text)
    if m:
        return m.group(1).strip()
    m = re.search(r"\b(Prof\.?\s+[A-Za-z\s\.\-]{2,40})(?:\n|,|$|\s{2,})", raw_text)
    if m:
        return m.group(1).strip()
    return None


def _extract_term_heuristic(raw_text: str) -> str | None:
    term_patterns = [
        re.compile(r"\b(Fall|Spring|Summer|Winter)\s+(20\d{2})\b", re.IGNORECASE),
        re.compile(r"\b(Autumn|Spring|Summer|Winter)\s+Term\s+(20\d{2})\b", re.IGNORECASE),
        re.compile(r"\b(20\d{2})\s+(Fall|Spring|Summer|Winter)\b", re.IGNORECASE),
    ]
    for pattern in term_patterns:
        m = pattern.search(raw_text)
        if m:
            groups = [g for g in m.groups() if g]
            return " ".join(groups)
    m = re.search(r"\b(20\d{2})\s*-\s*(20\d{2})\b", raw_text)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = re.search(r"\b(20\d{2})\b", raw_text)
    if m:
        return m.group(1)
    return None


def _extract_exam_dates_heuristic(raw_text: str) -> list[dt_date]:
    exam_dates: list[dt_date] = []
    lines = raw_text.splitlines()
    year_match = re.search(r"\b(20\d{2})\b", raw_text)
    default_year = int(year_match.group(1)) if year_match else None
    seen_exams: set[str] = set()

    exam_line_patterns = [
        re.compile(
            r"(Midterm\s*(?:Exam)?|Final\s*(?:Exam)?|Quiz\s*\d*|Assignment\s*\d*|"
            r"Project|Presentation|Paper|Test\s*\d*)\s*[:\-\(]\s*"
            r"((?:\d{4}-\d{2}-\d{2})|(?:\d{1,2}[/-]\d{1,2}[/-]\d{4})|"
            r"(?:[A-Za-z]{3,}\s+\d{1,2}(?:[a-z]{2})?,?\s*\d{4}))",
            re.IGNORECASE,
        ),
        re.compile(
            r"((?:\d{4}-\d{2}-\d{2})|(?:\d{1,2}[/-]\d{1,2}[/-]\d{4})|"
            r"(?:[A-Za-z]{3,}\s+\d{1,2}(?:[a-z]{2})?,?\s*\d{4}))\s*[:\-\(]\s*"
            r"(Midterm\s*(?:Exam)?|Final\s*(?:Exam)?|Quiz\s*\d*|"
            r"Assignment\s*\d*|Project|Presentation|Paper|Test\s*\d*)",
            re.IGNORECASE,
        ),
        re.compile(
            r"(Due|Deadline|Scheduled)[:\s]+"
            r"((?:\d{4}-\d{2}-\d{2})|(?:\d{1,2}[/-]\d{1,2}[/-]\d{4})|"
            r"(?:[A-Za-z]{3,}\s+\d{1,2}(?:[a-z]{2})?,?\s*\d{4}))",
            re.IGNORECASE,
        ),
        re.compile(
            r"(Week\s+\d+)\s*[:\-\.]\s*"
            r"(Midterm\s*(?:Exam)?|Final\s*(?:Exam)?|Quiz\s*\d*|"
            r"Assignment\s*\d*|Project|Presentation|Test\s*\d*)",
            re.IGNORECASE,
        ),
    ]

    for line in lines:
        line = line.strip()
        if not line:
            continue
        lower_line = line.lower()
        if not any(kw in lower_line for kw in _EXAM_KEYWORDS):
            continue
        for pattern in exam_line_patterns:
            m = pattern.search(line)
            if m:
                name = m.group(1).strip()
                date_str = m.group(2).strip()
                parsed_date = _parse_date_string(date_str, default_year)
                weightage = None
                wm = re.search(r"(\d{1,3})\s*%", line)
                if wm:
                    weightage = int(wm.group(1))
                exam_key = f"{name.lower()}_{date_str}"
                if exam_key in seen_exams:
                    continue
                seen_exams.add(exam_key)
                exam_dates.append(dt_date(
                    name=name, date=parsed_date,
                    weightage_percent=weightage, notes=None,
                ))
                break

    # Second pass: assessment table sections
    in_assessment_section = False
    assessment_section_pattern = re.compile(
        r"^(?:Assessment|Evaluation|Grading|Grade Distribution|"
        r"Course Assessment|Assessment Scheme)",
        re.IGNORECASE,
    )
    for i, line in enumerate(lines):
        line = line.strip()
        if assessment_section_pattern.match(line):
            in_assessment_section = True
            continue
        if in_assessment_section:
            if line == "":
                continue
            if re.match(r"^[A-Z][A-Z\s]{3,}$", line) and i > 0:
                in_assessment_section = False
                continue
            lower_line = line.lower()
            if any(kw in lower_line for kw in _EXAM_KEYWORDS):
                date_str = None
                for di in range(i, min(i + 3, len(lines))):
                    dm = re.search(
                        r"((?:\d{4}-\d{2}-\d{2})|(?:\d{1,2}[/-]\d{1,2}[/-]\d{4})|"
                        r"(?:[A-Za-z]{3,}\s+\d{1,2}(?:[a-z]{2})?,?\s*\d{4}))",
                        lines[di],
                    )
                    if dm:
                        date_str = dm.group(1)
                        break
                nm = re.search(
                    r"(Midterm\s*(?:Exam)?|Final\s*(?:Exam)?|Quiz\s*\d*|"
                    r"Assignment\s*\d*|Project|Presentation|Paper|Test\s*\d*|"
                    r"Homework\s*\d*)",
                    line, re.IGNORECASE,
                )
                if nm:
                    name = nm.group(1).strip()
                    parsed_date = _parse_date_string(date_str, default_year) if date_str else None
                    exam_key = f"{name.lower()}_{date_str or ''}"
                    if exam_key in seen_exams:
                        continue
                    seen_exams.add(exam_key)
                    weightage = None
                    wm = re.search(r"(\d{1,3})\s*%", line)
                    if wm:
                        weightage = int(wm.group(1))
                    exam_dates.append(dt_date(
                        name=name, date=parsed_date,
                        weightage_percent=weightage, notes=None,
                    ))
    return exam_dates


def _extract_topics_heuristic(raw_text: str) -> list[Topic]:
    topics: list[Topic] = []
    lines = raw_text.splitlines()
    seen_titles: set[str] = set()

    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        title: str | None = None
        week_or_unit: str | None = None
        description: str | None = None
        weightage_percent: float | None = None

        for pattern in _TOPIC_HEADER_PATTERNS:
            m = pattern.match(line)
            if m:
                groups = m.groups()
                if len(groups) == 2:
                    week_or_unit = groups[0].strip()
                    title = groups[1].strip()
                else:
                    title = groups[0].strip()
                break

        if not title:
            continue
        if len(title) < 4 or re.match(r"^\d+$", title):
            continue
        title_key = title.lower()
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)

        # Look ahead for description and weightage
        lookahead_lines: list[str] = []
        for j in range(i + 1, min(i + 5, len(lines))):
            next_line = lines[j].strip()
            if not next_line:
                continue
            if any(p.match(next_line) for p in _TOPIC_HEADER_PATTERNS[:6]):
                break
            if re.match(r"^[-=]{3,}$", next_line):
                break
            lookahead_lines.append(next_line)

        if lookahead_lines:
            desc_text = " ".join(lookahead_lines)
            if len(desc_text) > 300:
                desc_text = desc_text[:300] + "..."
            description = desc_text

        wm = re.search(r"(\d{1,3})\s*%", line)
        if not wm and lookahead_lines:
            for la in lookahead_lines:
                wm = re.search(r"(\d{1,3})\s*%", la)
                if wm:
                    break
        if wm:
            weightage_percent = int(wm.group(1))

        estimated_hours: int | None = None
        hm = re.search(
            r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)",
            line + " " + " ".join(lookahead_lines[:2]),
            re.IGNORECASE,
        )
        if hm:
            try:
                estimated_hours = int(float(hm.group(1)))
            except ValueError:
                pass

        topics.append(Topic(
            title=title, description=description,
            weightage_percent=weightage_percent,
            learning_objectives=[], week_or_unit=week_or_unit,
            estimated_hours=estimated_hours,
        ))

    # Fallback: bullet-pointed topics
    if len(topics) < 3:
        bullet_pattern = re.compile(r"^\s*[\*\-\u2022\u25cb]\s+([A-Z][A-Za-z\s\-&,]{3,80})$")
        for line in lines:
            line = line.strip()
            m = bullet_pattern.match(line)
            if m:
                title = m.group(1).strip()
                title_key = title.lower()
                if title_key not in seen_titles and len(title) > 4:
                    seen_titles.add(title_key)
                    topics.append(Topic(
                        title=title, description=None,
                        weightage_percent=None, learning_objectives=[],
                        week_or_unit=None, estimated_hours=None,
                    ))
    return topics


def _extract_learning_objectives_heuristic(raw_text: str) -> list[str]:
    objectives: list[str] = []
    section_pattern = re.compile(
        r"(?:Learning\s+Objectives|Course\s+Objectives|Objectives|"
        r"Learning\s+Outcomes|Course\s+Outcomes|Outcomes)[:\s]*\n",
        re.IGNORECASE,
    )
    m = section_pattern.search(raw_text)
    if not m:
        return objectives
    start_pos = m.end()
    section_text = raw_text[start_pos:start_pos + 2000]
    lines = section_text.splitlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if re.match(r"^[A-Z][A-Z\s]{3,}$", line):
            break
        if re.match(r"^(Unit|Week|Module|Chapter|Assessment|Schedule|Grading)\b", line, re.IGNORECASE):
            break
        obj_m = re.match(r"^\s*(?:[\*\-\u2022\u25cb\d]+[\.\)]?\s*)?(.+)", line)
        if obj_m:
            obj_text = obj_m.group(1).strip()
            if len(obj_text) > 10 and len(obj_text) < 300:
                objectives.append(obj_text)
        if len(objectives) >= 20:
            break
    return objectives

# ---------------------------------------------------------------------------
# Main entry points
# ---------------------------------------------------------------------------


def parse_syllabus_pdf(
    path: PathLike,
    *,
    use_llm: bool = False,  # ignored, kept for API compatibility
    max_chars_for_llm: int = 0,  # ignored, kept for API compatibility
    focus_query: str | None = None,  # ignored, no LLM ranking
) -> ParsedSyllabus:
    """
    Load a syllabus PDF and extract structured data using rule-based heuristics.

    Parameters ``use_llm``, ``max_chars_for_llm``, and ``focus_query`` are kept
    for API compatibility but are ignored -- this function is purely rule-based
    and does not call any LLM.
    """
    path = Path(path)
    raw, extraction_metadata = extract_text_from_pdf(path)

    topics = _extract_topics_heuristic(raw)
    exam_dates = _extract_exam_dates_heuristic(raw)
    course_title = _extract_course_title_heuristic(raw)
    instructor = _extract_instructor_heuristic(raw)
    term = _extract_term_heuristic(raw)
    learning_objectives = _extract_learning_objectives_heuristic(raw)

    confidence = _calculate_extraction_confidence(
        raw, topics_count=len(topics), exams_count=len(exam_dates),
        used_ocr=extraction_metadata.get("used_ocr", False),
        tables_extracted=extraction_metadata.get("tables_extracted", 0),
    )

    return ParsedSyllabus(
        course_title=course_title, instructor=instructor, term=term,
        topics=topics, learning_objectives=learning_objectives,
        exam_dates=exam_dates, raw_text=raw,
        source_path=str(path.resolve()),
        extraction_confidence=confidence,
        extraction_method=extraction_metadata.get("extraction_method"),
        extraction_metadata=extraction_metadata,
    )


def parse_syllabus_from_text(
    text: str,
    *,
    use_llm: bool = False,  # ignored, kept for API compatibility
    focus_query: str | None = None,  # ignored, no LLM ranking
) -> ParsedSyllabus:
    """
    Parse already-extracted syllabus text using rule-based heuristics.
    No LLM is called.
    """
    text = _normalize_extracted_text(text.strip())

    topics = _extract_topics_heuristic(text)
    exam_dates = _extract_exam_dates_heuristic(text)
    course_title = _extract_course_title_heuristic(text)
    instructor = _extract_instructor_heuristic(text)
    term = _extract_term_heuristic(text)
    learning_objectives = _extract_learning_objectives_heuristic(text)

    return ParsedSyllabus(
        course_title=course_title, instructor=instructor, term=term,
        topics=topics, learning_objectives=learning_objectives,
        exam_dates=exam_dates, raw_text=text,
        extraction_confidence=0.0, extraction_method=None,
        extraction_metadata=None,
    )


def save_parsed_syllabus(data: ParsedSyllabus, json_path: PathLike) -> None:
    """Write ``ParsedSyllabus`` to JSON."""
    out = Path(json_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(data.model_dump_json(indent=2), encoding="utf-8")


def load_parsed_syllabus(json_path: PathLike) -> ParsedSyllabus:
    """Load ``ParsedSyllabus`` from JSON."""
    path = Path(json_path)
    return ParsedSyllabus.model_validate_json(path.read_text(encoding="utf-8"))
