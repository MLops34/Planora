"""
RAG interface for "Chat with my syllabus": chunk, embed, retrieve, answer.

Uses FAISS + OpenAI embeddings/chat. Set ``OPENAI_API_KEY``.

NOTE: This module is ONLY for chat Q&A. Syllabus structure extraction is
handled by parser.py using rule-based heuristics (no LLM).
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from core.llm_provider import get_llm_provider_config

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]

DEFAULT_CHUNK_SIZE = 1200
DEFAULT_CHUNK_OVERLAP = 200


def _ensure_llm_provider() -> None:
    get_llm_provider_config()


def _split_documents(documents: List[Document]) -> List[Document]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=DEFAULT_CHUNK_SIZE,
        chunk_overlap=DEFAULT_CHUNK_OVERLAP,
        length_function=len,
    )
    return splitter.split_documents(documents)


def documents_from_pdf(
    path: PathLike, extra_metadata: Optional[Dict[str, Any]] = None
) -> List[Document]:
    """Load a PDF as LangChain Document pages."""
    from langchain_community.document_loaders import PyPDFLoader
    path = Path(path)
    loader = PyPDFLoader(str(path))
    docs = loader.load()
    meta = {"source": str(path.resolve()), **(extra_metadata or {})}
    for d in docs:
        d.metadata = {**d.metadata, **meta}
    return docs


def documents_from_text(
    text: str,
    *,
    source_label: str = "inline",
    extra_metadata: Optional[Dict[str, Any]] = None,
) -> List[Document]:
    """Safely wrap text as Document."""
    clean_text = str(text).strip() if text is not None else ""
    meta = {"source": source_label, **(extra_metadata or {})}
    return [Document(page_content=clean_text, metadata=meta)]


class SyllabusRAG:
    """In-memory FAISS store for syllabus Q&A."""

    def __init__(
        self,
        *,
        embedding_model: Optional[str] = None,
        chat_model: Optional[str] = None,
        k_retrieve: int = 6,
    ) -> None:
        _ensure_llm_provider()
        provider = get_llm_provider_config()
        default_embedding = "text-embedding-3-small"
        self._embedding_model = embedding_model or os.getenv(
            "OPENAI_EMBEDDING_MODEL", default_embedding
        )
        self._chat_model = chat_model or os.getenv(
            "OPENAI_CHAT_MODEL", "llama-3.1-8b-instant"
        )
        self._k = k_retrieve
        self._provider = provider
        self._embeddings = OpenAIEmbeddings(
            model=self._embedding_model,
            api_key=provider.api_key,
            base_url=provider.base_url,
        )
        self._vectorstore: Optional[FAISS] = None
        self._store_id: str = str(uuid.uuid4())

    @property
    def store_id(self) -> str:
        return self._store_id

    def ingest_documents(self, documents: List[Document]) -> int:
        """Chunk, clean, and embed documents."""
        if not documents:
            logger.warning("No documents provided")
            return 0
        chunks = _split_documents(documents)
        if not chunks:
            logger.warning("No chunks after splitting")
            return 0
        clean_chunks = []
        for c in chunks:
            content_str = str(c.page_content or "").strip()
            if len(content_str) < 15:
                continue
            clean_chunks.append(
                Document(page_content=content_str, metadata=c.metadata or {})
            )
        if not clean_chunks:
            logger.warning("No valid chunks after cleaning")
            return 0
        try:
            if self._vectorstore is None:
                self._vectorstore = FAISS.from_documents(clean_chunks, self._embeddings)
            else:
                self._vectorstore.add_documents(clean_chunks)
            logger.info(f"Successfully ingested {len(clean_chunks)} chunks")
            return len(clean_chunks)
        except Exception as e:
            logger.error(f"Embedding failed: {e}")
            raise

    def ingest_pdf(self, path: PathLike, **metadata: Any) -> int:
        docs = documents_from_pdf(path, extra_metadata=metadata)
        return self.ingest_documents(docs)

    def ingest_text(self, text: str, **metadata: Any) -> int:
        clean = str(text).strip() if text else ""
        if not clean:
            return 0
        docs = documents_from_text(clean, extra_metadata=metadata)
        return self.ingest_documents(docs)

    def as_retriever(self):
        if self._vectorstore is None:
            raise RuntimeError(
                "No documents ingested yet. Call ingest_pdf() or ingest_text() first."
            )
        return self._vectorstore.as_retriever(search_kwargs={"k": self._k})

    def query(self, question: str) -> str:
        """Main query method."""
        if self._vectorstore is None:
            raise RuntimeError("No documents ingested yet.")
        retriever = self.as_retriever()
        llm = ChatOpenAI(
            model=self._chat_model,
            temperature=0,
            api_key=self._provider.api_key,
            base_url=self._provider.base_url,
        )
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are a helpful teaching assistant. Answer ONLY using the provided syllabus context."),
            ("human", "Context:\n{context}\n\nQuestion: {input}"),
        ])
        docs = retriever.invoke(question)
        context = "\n\n".join(d.page_content for d in docs)
        formatted_prompt = prompt.format(context=context, input=question)
        response = llm.invoke(formatted_prompt)
        return str(getattr(response, "content", response)).strip()

    def save_local(self, directory: PathLike) -> None:
        if self._vectorstore is None:
            raise RuntimeError("Nothing to save.")
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        self._vectorstore.save_local(str(path))

    def load_local(self, directory: PathLike) -> None:
        path = Path(directory)
        self._vectorstore = FAISS.load_local(
            str(path), self._embeddings, allow_dangerous_deserialization=True
        )


def rag_qa_over_raw_text(raw_text: str, question: str) -> str:
    """Chunk, embed, and answer over a single syllabus text."""
    text = (raw_text or "").strip()
    q = (question or "").strip()
    if not text or not q:
        return ""
    rag = SyllabusRAG()
    rag.ingest_text(text, source="syllabus")
    return rag.query(q)
