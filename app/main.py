"""Interface local — sem login, sem rede pública, sem persistência dos originais."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .ingestion import MAX_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES, SUPPORTED_EXTENSIONS
from .llm import DEFAULT_MODEL, tags
from .pipeline import process_files
from .report import create_report

BASE = Path(__file__).parent
app = FastAPI(title="LexIA Local", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
JOBS: dict[str, dict] = {}
TEMP = Path(tempfile.mkdtemp(prefix="lexia_run_"))
SEM = asyncio.Semaphore(1)  # evitar processamento simultâneo disputando VRAM
TTL = timedelta(hours=1)


def _cleanup():
    now = datetime.now(timezone.utc)
    for jid, job in list(JOBS.items()):
        if job["status"] in {"done", "error"} and now - job["created"] > TTL:
            shutil.rmtree(TEMP / jid, ignore_errors=True)
            JOBS.pop(jid, None)


@app.get("/")
async def index():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/api/health")
async def health():
    try:
        models = await asyncio.to_thread(tags)
        return {"ollama": True, "models": models, "default_model": DEFAULT_MODEL}
    except Exception as exc:
        return {"ollama": False, "models": [], "default_model": DEFAULT_MODEL, "error": str(exc)[:180]}


@app.post("/api/jobs")
async def create_job(files: list[UploadFile] = File(...),
                     model: str = Form(DEFAULT_MODEL),
                     enable_ocr: bool = Form(False)):
    _cleanup()
    if not 1 <= len(files) <= MAX_FILES:
        raise HTTPException(400, f"Envie de 1 a {MAX_FILES} arquivos.")
    if not model.strip() or len(model) > 100 or not all(c.isalnum() or c in "-_.:/" for c in model):
        raise HTTPException(400, "Nome de modelo inválido.")
    uploads = []
    total = 0
    for f in files:
        filename = f.filename or "sem_nome"
        ext = Path(filename).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise HTTPException(400, f"Formato não suportado: {filename}")
        # Limita a leitura individual; não depende de Content-Length do cliente.
        contents = await f.read(MAX_FILE_BYTES + 1)
        if len(contents) > MAX_FILE_BYTES:
            raise HTTPException(413, f"{filename}: excede 30 MB")
        total += len(contents)
        if total > MAX_TOTAL_BYTES:
            raise HTTPException(413, "Arquivos excedem 120 MB no total")
        uploads.append((filename, contents))
        await f.close()
    jid = uuid4().hex
    JOBS[jid] = {
        "id": jid,
        "status": "queued",
        "message": "Aguardando processamento local",
        "progress": 0,
        "created": datetime.now(timezone.utc),
        "data": None,
        "error": None,
    }
    asyncio.create_task(_run(jid, model.strip(), uploads, enable_ocr))
    return {"id": jid, "status": "queued"}


async def _run(jid: str, model: str, uploads: list[tuple[str, bytes]], enable_ocr: bool):
    async with SEM:
        job = JOBS[jid]
        job["status"] = "processing"
        loop = asyncio.get_running_loop()

        def update(message, value):
            # Mutação simples de estado consultado via polling no mesmo loop.
            loop.call_soon_threadsafe(job.update, {"message": message, "progress": round(value * 100)})

        try:
            data = await asyncio.to_thread(process_files, model, uploads, enable_ocr, update)
            job_dir = TEMP / jid
            job_dir.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(create_report, data, str(job_dir / "relatorio_juridico.docx"))
            # Dados estruturados para auditoria (armazenados localmente e temporariamente).
            (job_dir / "auditoria.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            job.update({"status": "done", "progress": 100, "message": "Relatório concluído", "data": data})
        except Exception as exc:
            job.update({"status": "error", "message": "Falha no processamento", "error": f"{type(exc).__name__}: {str(exc)[:400]}"})


@app.get("/api/jobs/{jid}")
async def job_status(jid: str):
    _cleanup()
    job = JOBS.get(jid)
    if not job:
        raise HTTPException(404, "Análise expirada ou inexistente")
    payload = {k: job[k] for k in ("id", "status", "message", "progress", "error")}
    if job["status"] == "done":
        data = job["data"]
        payload["result"] = {
            "documentos": data["arquivos_recebidos"],
            "trechos_totais": data["trechos_totais"],
            "trechos_analisados": data["trechos_analisados"],
            "evidencias_verificadas": data["evidencias_verificadas"],
            "afirmacoes_descartadas": data["afirmacoes_descartadas"],
            "avisos": data["avisos"],
            "falhas": data["falhas"],
            "secoes": data["secoes"],
        }
    return payload


@app.get("/api/jobs/{jid}/download/{format_name}")
async def download(jid: str, format_name: str):
    _cleanup()
    if jid not in JOBS or JOBS[jid]["status"] != "done":
        raise HTTPException(404, "Relatório indisponível")
    if format_name == "docx":
        filename = "relatorio_juridico.docx"
        mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif format_name == "json":
        filename = "auditoria.json"
        mime = "application/json"
    else:
        raise HTTPException(404, "Formato desconhecido")
    return FileResponse(TEMP / jid / filename, media_type=mime, filename=filename)
