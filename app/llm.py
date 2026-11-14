import json
import os
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel

from .prompts import (
    EXTRACTION_SYSTEM,
    EXTRACTION_USER,
    SYNTHESIS_SYSTEM,
    SYNTHESIS_USER,
)
from .schemas import Extraction, SectionReport
from .ingestion import Chunk

OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:9b")
CONTEXT = int(os.getenv("OLLAMA_NUM_CTX", "8192"))


def _assert_local() -> None:
    parsed = urlparse(OLLAMA_URL)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("OLLAMA_BASE_URL deve apontar somente para loopback local (localhost).")


def tags() -> list[str]:
    _assert_local()
    with httpx.Client(timeout=8.0, trust_env=False) as client:
        response = client.get(f"{OLLAMA_URL}/api/tags")
        response.raise_for_status()
        return [m.get("name", "") for m in response.json().get("models", [])]


def _request(model: str, system: str, user: str, schema: type[BaseModel]):
    _assert_local()
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "format": schema.model_json_schema(),
        "options": {"temperature": 0, "num_ctx": CONTEXT},
        "stream": False,
        "keep_alive": "5m",
    }
    with httpx.Client(timeout=httpx.Timeout(connect=10, read=900, write=30, pool=10), trust_env=False) as client:
        for attempt in range(2):
            response = client.post(f"{OLLAMA_URL}/api/chat", json=payload)
            response.raise_for_status()
            content = response.json().get("message", {}).get("content", "")
            try:
                return schema.model_validate(json.loads(content))
            except (ValueError, TypeError):
                if attempt:
                    raise ValueError("O modelo retornou JSON incompatível com o esquema, mesmo após repetição.")
                payload["messages"].append({
                    "role": "user",
                    "content": "A resposta anterior não correspondeu ao esquema. Responda SOMENTE JSON válido compatível."
                })


def extract_chunk(model: str, chunk: Chunk) -> Extraction:
    prompt = EXTRACTION_USER.format(
        filename=chunk.filename,
        location=chunk.location,
        source_id=chunk.id,
        content=chunk.text,
    )
    return _request(model, EXTRACTION_SYSTEM, prompt, Extraction)


def report_section(model: str, name: str, purpose: str, evidence_block: str, max_points: int = 16) -> SectionReport:
    prompt = SYNTHESIS_USER.format(
        section_name=name,
        section_purpose=purpose,
        evidence_block=evidence_block,
        max_points=max_points,
    )
    return _request(model, SYNTHESIS_SYSTEM, prompt, SectionReport)
