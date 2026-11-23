"""Durable, single-instance local case and job storage."""
import json
import os
from pathlib import Path
import re
from uuid import uuid4

ROOT = Path(os.getenv("LEXIA_DATA_DIR", "data"))


def _safe(value: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError("Identificador inválido")
    return value


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def new_case(name: str, office: str = "local") -> dict:
    record = {"id": uuid4().hex, "name": name.strip()[:150], "office": office.strip()[:100], "notes": [], "jobs": []}
    if not record["name"]:
        raise ValueError("Nome do caso obrigatório")
    save_case(record)
    return record


def save_case(record: dict) -> None:
    _write(ROOT / "cases" / f"{_safe(record['id'])}.json", record)


def get_case(case_id: str) -> dict | None:
    path = ROOT / "cases" / f"{_safe(case_id)}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def list_cases() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((ROOT / "cases").glob("*.json"))] if (ROOT / "cases").exists() else []


def save_job(record: dict) -> None:
    _write(ROOT / "jobs" / f"{_safe(record['id'])}.json", record)


def get_job(job_id: str) -> dict | None:
    path = ROOT / "jobs" / f"{_safe(job_id)}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
