"""Single-instance local API with durable case metadata and reports."""
import asyncio
import base64
import hmac
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .ingestion import MAX_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES, SUPPORTED_EXTENSIONS
from .llm import DEFAULT_MODEL, PRIMARY_PROVIDER, tags
from .pipeline import process_files
from .report import create_report
from . import storage, library

BASE = Path(__file__).parent
app = FastAPI(title="LexIA Local", docs_url=None, redoc_url=None)
logger = logging.getLogger("lexia")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
JOBS: dict[str, dict] = {}
TEMP = storage.ROOT / "reports"
TEMP.mkdir(parents=True, exist_ok=True)
SEM = asyncio.Semaphore(1)  # evitar processamento simultâneo disputando VRAM


def _cleanup():
    pass  # retenção explícita por caso; operador administra o volume persistido


@app.middleware("http")
async def authenticate(request: Request, call_next):
    token = os.getenv("LEXIA_API_TOKEN", "")
    if not token:
        if request.client and request.client.host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
            return JSONResponse({"detail": "Configure LEXIA_API_TOKEN para acesso remoto"}, status_code=403)
    else:
        header = request.headers.get("authorization", "")
        try:
            scheme, encoded = header.split(" ", 1)
            userpass = base64.b64decode(encoded, validate=True).decode()
            provided = userpass.split(":", 1)[1]
        except (ValueError, UnicodeError, IndexError):
            provided, scheme = "", ""
        if scheme.lower() != "basic" or not hmac.compare_digest(provided, token):
            return JSONResponse({"detail": "Autenticação necessária"}, status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="LexIA"'})
    return await call_next(request)


def _user_error(exc: Exception) -> str:
    """Map internal exceptions to short, actionable Portuguese UI messages."""
    message = str(exc).casefold()
    if "429" in message or "limitou as requisições" in message or "quota" in message:
        return "O serviço de análise está temporariamente ocupado. Aguarde alguns instantes e tente novamente."
    if "conectar ao ollama" in message or "connection refused" in message:
        return "O serviço de análise local está indisponível. Verifique se ele está iniciado e tente novamente."
    if "não encontrado no ollama" in message or "model not found" in message:
        return "O modelo de análise não está instalado. Entre em contato com o responsável pelo sistema."
    if "tempo esgotado" in message or "timeout" in message:
        return "A análise demorou mais que o esperado. Tente novamente em alguns instantes."
    if "extração não foi concluída pelo modelo" in message or "modelo retornou json incompatível" in message or "resposta inválida" in message:
        return "O modelo de análise não conseguiu interpretar os trechos. Verifique o modelo/provedor configurado e tente novamente."
    if "nenhuma evidência" in message or "citação literal verificável" in message:
        return "O arquivo foi lido, mas o modelo não produziu citações que pudessem ser confirmadas no texto. Tente novamente ou selecione outro modelo."
    if "ocr" in message or "texto legível" in message:
        return "Não foi possível ler o conteúdo dos arquivos. Se forem digitalizados, ative a leitura de imagens e tente novamente."
    return "Não foi possível concluir esta operação. Tente novamente; se o problema persistir, contate o responsável pelo sistema."


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    error_id = uuid4().hex[:12]
    logger.exception("Unhandled request error_id=%s method=%s path=%s", error_id, request.method, request.url.path)
    return JSONResponse({"detail": "Ocorreu um erro inesperado. Tente novamente.", "error_id": error_id}, status_code=500)


@app.get("/api/cases")
async def cases():
    return storage.list_cases()


@app.post("/api/cases")
async def create_case(name: str = Form(...)):
    try:
        return storage.new_case(name)
    except ValueError as exc:
        raise HTTPException(400, "Informe um nome válido para o caso.") from exc


@app.post("/api/cases/{case_id}/notes")
async def add_note(case_id: str, text: str = Form(...)):
    try:
        record = storage.get_case(case_id)
    except ValueError:
        raise HTTPException(404, "Caso inexistente")
    if not record:
        raise HTTPException(404, "Caso inexistente")
    if not text.strip() or len(text) > 3000:
        raise HTTPException(400, "Nota inválida")
    record["notes"].append({"text": text.strip(), "kind": "anotacao_humana", "created": datetime.now(timezone.utc).isoformat()})
    storage.save_case(record)
    return record


@app.get("/api/cases/{case_id}/search")
async def search_case(case_id: str, q: str):
    try:
        record = storage.get_case(case_id)
    except ValueError:
        record = None
    if not record:
        raise HTTPException(404, "Caso inexistente")
    terms = [x for x in q.casefold().split() if len(x) >= 3][:10]
    if not terms:
        return {"results": []}
    matches = []
    for jid in record["jobs"]:
        job = storage.get_job(jid)
        if not job or job["status"] != "done":
            continue
        for evidence in job["data"].get("evidencias", []):
            corpus = (evidence["titulo"] + " " + evidence["descricao"] + " " + evidence["citacao_literal"]).casefold()
            score = sum(corpus.count(t) for t in terms)
            if score:
                matches.append({"job_id": jid, "score": score, **evidence})
    return {"results": sorted(matches, key=lambda x: -x["score"])[:30]}


@app.get("/api/legal-sources")
async def legal_sources():
    return {"coverage": "Catálogo de pontos de consulta; sem indexação automática nem garantia de vigência",
            "sources": [
                {"category": "legislacao_federal", "name": "Planalto — legislação federal", "url": "https://www.planalto.gov.br/ccivil_03/", "territory": "Brasil"},
                {"category": "jurisprudencia", "name": "STF — jurisprudência", "url": "https://portal.stf.jus.br/jurisprudencia/", "territory": "Brasil"},
                {"category": "jurisprudencia", "name": "STJ — jurisprudência", "url": "https://scon.stj.jus.br/SCON/", "territory": "Brasil"},
            ]}


@app.get("/api/library")
async def list_library(category: str = ""):
    try:
        return {"categories": library.CATEGORIES, "documents": library.list_documents(category)}
    except ValueError as exc:
        raise HTTPException(400, "A categoria informada não é válida.") from exc


@app.post("/api/library")
async def upload_library(files: list[UploadFile] = File(...), category: str = Form(...),
                         source_url: str = Form(""), territory: str = Form(""),
                         effective_date: str = Form(""), enable_ocr: bool = Form(True)):
    if category not in library.CATEGORIES or not 1 <= len(files) <= MAX_FILES:
        raise HTTPException(400, "Categoria ou quantidade de arquivos inválida")
    total, records, failures = 0, [], []
    for file in files:
        name = file.filename or ""
        if Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
            failures.append(f"{name}: formato não suportado")
            continue
        data = await file.read(MAX_FILE_BYTES + 1)
        await file.close()
        total += len(data)
        if len(data) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise HTTPException(413, "Limite: 30 MB por arquivo e 120 MB por envio")
        try:
            records.append(await asyncio.to_thread(library.add_document, category, name, data,
                source_url=source_url, territory=territory, effective_date=effective_date,
                enable_ocr=enable_ocr))
        except ValueError as exc:
            logger.info("Library document rejected filename=%s reason=%s", name, exc)
            failures.append(f"{name}: não foi possível ler o conteúdo deste arquivo")
    return {"documents": records, "failures": failures}


@app.get("/api/library/search")
async def search_library(q: str, category: str = ""):
    try:
        return {"results": await asyncio.to_thread(library.search, q, category=category)}
    except ValueError as exc:
        raise HTTPException(400, "Não foi possível realizar a busca com esses filtros.") from exc


@app.delete("/api/library/{category}/{document_id}")
async def delete_library_document(category: str, document_id: str):
    try:
        deleted = await asyncio.to_thread(library.delete_document, category, document_id)
    except ValueError:
        deleted = False
    if not deleted:
        raise HTTPException(404, "Referência não encontrada")
    return {"deleted": True}


@app.get("/api/library/{category}/{document_id}/download")
async def download_library_document(category: str, document_id: str):
    try:
        record = library.get_document(category, document_id)
    except ValueError:
        record = None
    if not record:
        raise HTTPException(404, "Referência não encontrada")
    original = library._folder(category, document_id) / ("original" + Path(record["name"]).suffix.lower())
    return FileResponse(original, filename=record["name"])


@app.get("/")
async def index():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/api/health")
async def health():
    if PRIMARY_PROVIDER != "ollama":
        key_name = {"gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
                    "openai": "OPENAI_API_KEY"}[PRIMARY_PROVIDER]
        configured = bool(os.getenv(key_name))
        ollama_available = False
        ollama_models = []
        if os.getenv("LEXIA_FALLBACK_PROVIDER", "").lower() == "ollama":
            try:
                ollama_models = await asyncio.to_thread(tags)
                ollama_available = True
            except Exception:
                pass
        return {"provider": PRIMARY_PROVIDER, "configured": configured,
                "models": [DEFAULT_MODEL], "default_model": DEFAULT_MODEL,
                "fallback_provider": os.getenv("LEXIA_FALLBACK_PROVIDER", ""),
                "fallback_available": ollama_available,
                "fallback_models": ollama_models}
    try:
        models = await asyncio.to_thread(tags)
        return {"provider": "ollama", "ollama": True, "models": models, "default_model": DEFAULT_MODEL}
    except Exception as exc:
        logger.warning("Ollama health check failed: %s", exc, exc_info=True)
        return {"provider": "ollama", "ollama": False, "models": [], "default_model": DEFAULT_MODEL, "error": "O serviço de análise local está indisponível. Verifique se ele está iniciado."}


@app.post("/api/jobs")
async def create_job(files: list[UploadFile] = File(...),
                     model: str = Form(DEFAULT_MODEL),
                     enable_ocr: bool = Form(False), case_id: str = Form("")):
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
    if case_id:
        try:
            case = storage.get_case(case_id)
        except ValueError:
            case = None
        if not case:
            raise HTTPException(404, "Caso inexistente")
        memory = [f"Nota humana: {note['text']}" for note in case.get("notes", [])[-8:]]
        for previous_id in case.get("jobs", [])[-3:]:
            previous = JOBS.get(previous_id) or storage.get_job(previous_id)
            if previous and previous.get("status") == "done":
                for section in previous.get("data", {}).get("secoes", []):
                    memory.extend(f"Análise anterior ({section['nome']}): {point['texto']}" for point in section.get("pontos", [])[:3])
        memory = memory[-20:]
    else:
        memory = []
    JOBS[jid] = {
        "id": jid,
        "status": "queued",
        "message": "Aguardando processamento local",
        "progress": 0,
        "created": datetime.now(timezone.utc).isoformat(),
        "case_id": case_id,
        "data": None,
        "events": [],
        "error": None,
    }
    storage.save_job(JOBS[jid])
    if case_id:
        case["jobs"].append(jid)
        storage.save_case(case)
    asyncio.create_task(_run(jid, model.strip(), uploads, enable_ocr, memory))
    return {"id": jid, "status": "queued"}


async def _run(jid: str, model: str, uploads: list[tuple[str, bytes]], enable_ocr: bool, memory: list[str]):
    async with SEM:
        job = JOBS[jid]
        job["status"] = "processing"
        storage.save_job(job)
        loop = asyncio.get_running_loop()

        def update(message, value, event=None):
            # Mutação simples de estado consultado via polling no mesmo loop.
            payload = {"message": message, "progress": round(value * 100)}
            if event:
                payload["events"] = (job.get("events", []) + [{**event, "at": datetime.now(timezone.utc).isoformat()}])[-80:]
                storage.save_job({**job, **payload})
            loop.call_soon_threadsafe(job.update, payload)

        try:
            data = await asyncio.to_thread(process_files, model, uploads, enable_ocr, update, memory)
            job_dir = TEMP / jid
            job_dir.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(create_report, data, str(job_dir / "relatorio_juridico.docx"))
            # Dados estruturados para auditoria (armazenados localmente e temporariamente).
            (job_dir / "auditoria.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            job.update({"status": "done", "progress": 100, "message": "Relatório concluído", "data": data})
            storage.save_job(job)
        except Exception as exc:
            error_id = uuid4().hex[:12]
            logger.exception("Job analysis failed job_id=%s error_id=%s", jid, error_id)
            job.update({"status": "error", "message": "Não foi possível concluir a análise", "error": _user_error(exc), "error_id": error_id})
            storage.save_job(job)


@app.get("/api/jobs/{jid}")
async def job_status(jid: str):
    _cleanup()
    try:
        job = JOBS.get(jid) or storage.get_job(jid)
    except ValueError:
        job = None
    if not job:
        raise HTTPException(404, "Análise expirada ou inexistente")
    if jid not in JOBS and job["status"] in {"queued", "processing"}:
        job.update({"status": "error", "error": "Processamento interrompido pelo reinício; envie os arquivos novamente.", "message": "Interrompido"})
        storage.save_job(job)
    payload = {k: job[k] for k in ("id", "status", "message", "progress", "error", "error_id") if k in job}
    payload["events"] = job.get("events", [])
    if job["status"] == "done":
        data = job["data"]
        payload["result"] = {
            "documentos": data["arquivos_recebidos"],
            "trechos_totais": data["trechos_totais"],
            "trechos_analisados": data["trechos_analisados"],
            "evidencias_verificadas": data["evidencias_verificadas"],
            "afirmacoes_descartadas": data["afirmacoes_descartadas"],
            "avisos": data["avisos"],
            "falhas": ([f"Não foi possível analisar {sum(not item.startswith('Síntese (') for item in data['falhas'])} trecho(s). Confira a cobertura e as falhas técnicas no relatório."]
                        if any(not item.startswith("Síntese (") for item in data["falhas"]) else []),
            "secoes": data["secoes"],
            "evidencias": data.get("evidencias", []),
        }
    return payload


@app.get("/api/jobs/{jid}/events")
async def job_events(jid: str):
    try:
        job = JOBS.get(jid) or storage.get_job(jid)
    except ValueError:
        job = None
    if not job:
        raise HTTPException(404, "Análise expirada ou inexistente")
    async def stream():
        cursor = 0
        yield "retry: 1000\n\n"
        while True:
            current = JOBS.get(jid) or job
            events = current.get("events", [])
            while cursor < len(events):
                yield f"data: {json.dumps(events[cursor], ensure_ascii=False)}\n\n"
                cursor += 1
            if current.get("status") in {"done", "error"}:
                yield f"event: complete\ndata: {json.dumps({'status': current['status']})}\n\n"
                break
            await asyncio.sleep(0.25)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/jobs/{jid}/download/{format_name}")
async def download(jid: str, format_name: str):
    _cleanup()
    try:
        job = JOBS.get(jid) or storage.get_job(jid)
    except ValueError:
        job = None
    if not job or job["status"] != "done":
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
