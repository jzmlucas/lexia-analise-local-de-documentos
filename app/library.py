"""Local reference library with explicit categories and traceable text chunks."""
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import unicodedata
from uuid import uuid4

from . import storage
from .ingestion import ingest_file
from .llm import embed_texts

CATEGORIES = {
    "legislacao": "Legislação",
    "jurisprudencia": "Jurisprudência",
    "doutrina": "Doutrina",
    "modelos": "Modelos e peças",
    "outros": "Outras referências",
}
ROOT = storage.ROOT / "biblioteca" / "escritorio-local"


def _folder(category: str, document_id: str) -> Path:
    if category not in CATEGORIES or not re.fullmatch(r"[a-f0-9]{32}", document_id):
        raise ValueError("Categoria ou identificador inválido")
    return ROOT / category / document_id


def _terms(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", text.casefold())
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    return [w for w in re.findall(r"[a-z0-9]{3,}", normalized) if w not in
            {"para", "como", "com", "uma", "dos", "das", "que", "por", "sobre"}]


def add_document(category: str, name: str, data: bytes, *, source_url: str = "",
                 territory: str = "", effective_date: str = "", enable_ocr: bool = True) -> dict:
    if category not in CATEGORIES:
        raise ValueError("Categoria inválida")
    name = Path(name.replace("\\", "/")).name
    if not name or len(name) > 180:
        raise ValueError("Nome de arquivo inválido")
    if source_url and (len(source_url) > 500 or not source_url.startswith("https://")):
        raise ValueError("URL da fonte deve começar com https://")
    document_id = uuid4().hex
    extracted = ingest_file("R" + document_id[:8], name, data, enable_ocr)
    if not extracted.chunks:
        raise ValueError("Nenhum texto recuperável. Verifique o arquivo e o OCR.")
    folder = _folder(category, document_id)
    folder.mkdir(parents=True, exist_ok=False)
    try:
        (folder / ("original" + Path(name).suffix.lower())).write_bytes(data)
        record = {
            "id": document_id, "category": category, "name": name,
            "sha256": extracted.sha256, "pages": extracted.total_pages,
            "chunks": len(extracted.chunks), "warnings": extracted.warnings,
            "source_url": source_url.strip(), "territory": territory.strip()[:120],
            "effective_date": effective_date.strip()[:80],
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "verification": "não verificado automaticamente",
        }
        chunks = [asdict(chunk) for chunk in extracted.chunks]
        try:
            vectors = embed_texts([chunk["text"] for chunk in chunks])
            for chunk, vector in zip(chunks, vectors):
                chunk["embedding"] = vector
            record["embedding_model"] = __import__("os").getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
        except Exception:
            record["embedding_model"] = "lexical-fallback"
        storage._write(folder / "index.json", {"chunks": chunks})
        storage._write(folder / "metadata.json", record)
        return record
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def list_documents(category: str = "") -> list[dict]:
    if category and category not in CATEGORIES:
        raise ValueError("Categoria inválida")
    folders = [ROOT / category] if category else [ROOT / c for c in CATEGORIES]
    result = []
    for folder in folders:
        if folder.exists():
            for path in folder.glob("*/metadata.json"):
                result.append(json.loads(path.read_text(encoding="utf-8")))
    return sorted(result, key=lambda x: x["uploaded_at"], reverse=True)


def get_document(category: str, document_id: str) -> dict | None:
    path = _folder(category, document_id) / "metadata.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def delete_document(category: str, document_id: str) -> bool:
    folder = _folder(category, document_id)
    if not folder.exists():
        return False
    shutil.rmtree(folder)
    return True


def search(query: str, *, category: str = "", limit: int = 12) -> list[dict]:
    terms = list(dict.fromkeys(_terms(query)))[:12]
    matches = []
    query_vector = None
    try:
        query_vector = embed_texts([query])[0]
    except Exception:
        pass
    for record in list_documents(category):
        folder = _folder(record["category"], record["id"])
        chunks = json.loads((folder / "index.json").read_text(encoding="utf-8"))["chunks"]
        for chunk in chunks:
            corpus = " ".join(_terms(chunk["text"]))
            score = sum(corpus.count(term) for term in terms)
            vector = chunk.get("embedding")
            semantic = 0.0
            if query_vector and vector and len(query_vector) == len(vector):
                dot = sum(a * b for a, b in zip(query_vector, vector))
                norm_a = sum(a * a for a in query_vector) ** 0.5
                norm_b = sum(b * b for b in vector) ** 0.5
                semantic = dot / (norm_a * norm_b) if norm_a and norm_b else 0.0
                score += semantic * 5
            if score > 0 or semantic > 0.2:
                matches.append({
                    "score": score, "document_id": record["id"],
                    "semantic_score": round(semantic, 4),
                    "category": record["category"], "name": record["name"],
                    "source_url": record["source_url"], "territory": record["territory"],
                    "effective_date": record["effective_date"],
                    "verification": record["verification"],
                    "chunk_id": chunk["id"], "location": chunk["location"],
                    "excerpt": chunk["text"][:900],
                })
    return sorted(matches, key=lambda x: (-x["score"], x["name"], x["chunk_id"]))[:max(1, min(limit, 30))]
