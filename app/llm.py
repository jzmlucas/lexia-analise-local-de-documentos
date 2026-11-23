import json
import os
import re
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ValidationError

from .prompts import (
    EXTRACTION_SYSTEM,
    EXTRACTION_USER,
    SYNTHESIS_SYSTEM,
    SYNTHESIS_USER,
)
from .schemas import Extraction, SectionReport
from .ingestion import Chunk

OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
PRIMARY_PROVIDER = os.getenv("LEXIA_PRIMARY_PROVIDER", "ollama").strip().lower()
CLOUD_MODELS = {
    "gemini": ("GEMINI_MODEL", "gemini-2.5-flash"),
    "anthropic": ("ANTHROPIC_MODEL", "claude-sonnet-4-5"),
    "openai": ("OPENAI_MODEL", "gpt-4.1-mini"),
}
if PRIMARY_PROVIDER not in {"ollama", *CLOUD_MODELS}:
    raise RuntimeError("LEXIA_PRIMARY_PROVIDER inválido")
if PRIMARY_PROVIDER != "ollama" and os.getenv("LEXIA_ALLOW_CLOUD", "false").lower() != "true":
    raise RuntimeError("LEXIA_ALLOW_CLOUD=true é obrigatório para provedor principal em nuvem")
DEFAULT_MODEL = (os.getenv("OLLAMA_MODEL", "qwen3.5:9b") if PRIMARY_PROVIDER == "ollama"
                 else os.getenv(*CLOUD_MODELS[PRIMARY_PROVIDER]))
CONTEXT = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
# Limite de tokens gerados: evita laços infinitos (modelo repetindo espaços/itens até
# estourar o contexto) e garante que prompt + resposta caibam em num_ctx.
MAX_OUTPUT_TOKENS = int(os.getenv("OLLAMA_NUM_PREDICT", "2560"))
MAX_SPLIT_DEPTH = 2


class LLMError(RuntimeError):
    """Falha ao falar com o provedor de modelo."""


class LLMFatalError(LLMError):
    """Falha que afetará TODOS os trechos (Ollama fora do ar, modelo inexistente...).

    O pipeline interrompe imediatamente e mostra esta mensagem ao usuário, em vez de
    repetir o mesmo erro para cada trecho e esconder a causa.
    """


class LLMTruncated(ValueError):
    """A resposta foi cortada por limite de tokens/contexto (JSON incompleto)."""


class LLMRateLimitError(LLMError):
    """O provedor em nuvem limitou temporariamente as requisições."""


def _assert_local() -> None:
    parsed = urlparse(OLLAMA_URL)
    allowed = {"localhost", "127.0.0.1", "::1"}
    if os.getenv("OLLAMA_ALLOWED_HOST"):
        allowed.add(os.environ["OLLAMA_ALLOWED_HOST"])
    if parsed.scheme != "http" or parsed.hostname not in allowed:
        raise LLMFatalError("OLLAMA_BASE_URL deve apontar somente para loopback local (localhost).")


def tags() -> list[str]:
    _assert_local()
    with httpx.Client(timeout=8.0, trust_env=False) as client:
        response = client.get(f"{OLLAMA_URL}/api/tags")
        response.raise_for_status()
        return [m.get("name", "") for m in response.json().get("models", [])]


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Create local Ollama embeddings; caller can fall back to lexical search."""
    if PRIMARY_PROVIDER != "ollama":
        raise LLMError("Embeddings semânticos usam Ollama local")
    _assert_local()
    model = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
    vectors = []
    with httpx.Client(timeout=httpx.Timeout(connect=10, read=120), trust_env=False) as client:
        for text in texts:
            response = client.post(f"{OLLAMA_URL}/api/embed", json={"model": model, "input": text[:8000]})
            if response.status_code >= 400:
                raise LLMError(f"Embeddings Ollama indisponíveis: {_error_detail(response)}")
            data = response.json()
            vector = (data.get("embeddings") or [data.get("embedding")])[0]
            if not vector:
                raise LLMError("Ollama não retornou vetor de embedding")
            vectors.append(vector)
    return vectors


def _inline_schema(schema: dict) -> dict:
    """JSON Schema sem ``$ref``/``$defs``/``title``/limites, aceito pelo Ollama em qualquer versão."""
    defs = schema.get("$defs", {})
    drop = {"$defs", "title", "maxLength", "minLength", "maxItems", "minItems"}

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].split("/")[-1]])
            return {k: walk(v) for k, v in node.items() if k not in drop}
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node

    return walk(schema)


def _parse_json(content: str):
    """Aceita JSON puro, cercado por ```json, ou com texto ao redor."""
    text = (content or "").strip()
    if not text:
        raise ValueError("resposta vazia")
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


def _error_detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("error") or response.text
    except ValueError:
        detail = response.text
    return str(detail).strip()[:300]


def _post_chat(client: httpx.Client, payload: dict) -> dict:
    try:
        response = client.post(f"{OLLAMA_URL}/api/chat", json=payload)
    except httpx.ConnectError as exc:
        raise LLMFatalError(
            f"Não foi possível conectar ao Ollama em {OLLAMA_URL}. Abra o aplicativo Ollama (ou rode 'ollama serve')."
        ) from exc
    except httpx.TimeoutException as exc:
        raise LLMError("Tempo esgotado aguardando o modelo (carregamento lento ou GPU sobrecarregada).") from exc
    if response.status_code == 400 and "think" in response.text.lower() and "think" in payload:
        # Versão/modelo sem suporte ao parâmetro `think`: repete sem ele.
        payload.pop("think")
        return _post_chat(client, payload)
    if response.status_code == 404:
        raise LLMFatalError(
            f"Modelo '{payload['model']}' não encontrado no Ollama. Execute: ollama pull {payload['model']}"
        )
    if response.status_code >= 400:
        detail = _error_detail(response)
        message = f"Ollama retornou HTTP {response.status_code}: {detail}"
        # 5xx costuma ser falta de memória/modelo que não carrega: vale a pena parar tudo.
        if response.status_code >= 500 and re.search(r"memory|alloc|load|CUDA|runner|terminated", detail, re.I):
            raise LLMFatalError(message)
        raise LLMError(message)
    return response.json()


def _cloud_request(system: str, user: str, schema: type[BaseModel], provider: str):
    """Call an explicitly enabled cloud provider with the same validated schema."""
    if os.getenv("LEXIA_ALLOW_CLOUD", "false").lower() != "true":
        raise LLMError("Fallback em nuvem desativado")
    settings = {
        "openai": ("OPENAI_API_KEY", "https://api.openai.com/v1/chat/completions", os.getenv("OPENAI_MODEL", "gpt-4.1-mini")),
        "gemini": ("GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", os.getenv("GEMINI_MODEL", "gemini-2.5-flash")),
        "anthropic": ("ANTHROPIC_API_KEY", "https://api.anthropic.com/v1/messages", os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")),
    }
    if provider not in settings:
        raise LLMError("Provedor em nuvem não configurado")
    env, url, model = settings[provider]
    key = os.getenv(env, "")
    if not key:
        raise LLMError(f"Chave {env} ausente")
    cloud_system = system + "\nResponda APENAS JSON válido conforme este esquema: " + json.dumps(
        _inline_schema(schema.model_json_schema()), ensure_ascii=False
    )
    with httpx.Client(timeout=180, trust_env=False) as client:
        if provider == "anthropic":
            response = client.post(url, headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                json={"model": model, "max_tokens": MAX_OUTPUT_TOKENS, "system": cloud_system,
                      "messages": [{"role": "user", "content": user}]})
            response.raise_for_status()
            content = "".join(x.get("text", "") for x in response.json().get("content", []))
        else:
            response = client.post(url, headers={"Authorization": f"Bearer {key}"},
                json={"model": model, "temperature": 0, "messages": [{"role": "system", "content": cloud_system}, {"role": "user", "content": user}]})
            if response.status_code == 429:
                retry_after = response.headers.get("retry-after", "")
                wait_hint = f" Tente novamente em {retry_after} segundos." if retry_after.isdigit() else " Tente novamente mais tarde ou configure um provedor alternativo."
                raise LLMRateLimitError(f"{provider.capitalize()} limitou as requisições (HTTP 429).{wait_hint}")
            if response.status_code >= 400:
                detail = _error_detail(response)
                raise LLMError(f"{provider.capitalize()} retornou HTTP {response.status_code}: {detail}")
            content = response.json()["choices"][0]["message"]["content"]
    return schema.model_validate(_parse_json(content))


def _request_ollama(model: str, system: str, user: str, schema: type[BaseModel]):
    _assert_local()
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "format": _inline_schema(schema.model_json_schema()),
        # Qwen3/3.5 e outros modelos "thinking" gastam o orçamento de tokens raciocinando e
        # devolvem `content` vazio quando há `format`: era a causa do erro de JSON inválido.
        "think": False,
        "options": {"temperature": 0, "num_ctx": CONTEXT, "num_predict": MAX_OUTPUT_TOKENS},
        "stream": False,
        "keep_alive": "5m",
    }
    last_error = "resposta inválida"
    with httpx.Client(timeout=httpx.Timeout(connect=10, read=900, write=30, pool=10), trust_env=False) as client:
        for attempt in range(2):
            try:
                data = _post_chat(client, payload)
            except LLMError:
                if os.getenv("LEXIA_ALLOW_CLOUD", "false").lower() == "true" and os.getenv("LEXIA_FALLBACK_PROVIDER"):
                    return _cloud_request(system, user, schema, os.getenv("LEXIA_FALLBACK_PROVIDER", "").lower())
                raise
            message = data.get("message") or {}
            content = message.get("content", "")
            if data.get("done_reason") == "length":
                raise LLMTruncated("Resposta do modelo cortada por limite de tokens.")
            try:
                return schema.model_validate(_parse_json(content))
            except (ValueError, TypeError, ValidationError) as exc:
                last_error = str(exc).splitlines()[0][:160] if str(exc) else type(exc).__name__
                if attempt == 0:
                    payload["messages"] = payload["messages"][:2] + [
                        {"role": "assistant", "content": content[:1500]},
                        {"role": "user", "content": "A resposta anterior não correspondeu ao esquema. Responda SOMENTE JSON válido compatível."},
                    ]
    raise ValueError(f"O modelo retornou JSON incompatível com o esquema, mesmo após repetição ({last_error}).")


def _request(model: str, system: str, user: str, schema: type[BaseModel]):
    if PRIMARY_PROVIDER == "ollama":
        return _request_ollama(model, system, user, schema)
    try:
        return _cloud_request(system, user, schema, PRIMARY_PROVIDER)
    except LLMRateLimitError:
        fallback = os.getenv("LEXIA_FALLBACK_PROVIDER", "").lower()
        if fallback == "ollama":
            return _request_ollama(os.getenv("OLLAMA_MODEL", "qwen3.5:9b"), system, user, schema)
        raise
    except Exception:
        if os.getenv("LEXIA_FALLBACK_PROVIDER", "").lower() == "ollama":
            return _request_ollama(os.getenv("OLLAMA_MODEL", "qwen3.5:9b"), system, user, schema)
        raise


def _split_text(text: str) -> tuple[str, str]:
    """Divide perto do meio, preferindo quebra de parágrafo/linha/espaço; com pequena sobreposição."""
    mid = len(text) // 2
    cut = max(text.rfind("\n", 0, mid + 200), text.rfind(". ", 0, mid + 200), text.rfind(" ", 0, mid + 200))
    if cut < len(text) // 4:
        cut = mid
    overlap = 200
    return text[: cut + 1], text[max(0, cut + 1 - overlap):]


def _extract(model: str, chunk: Chunk, text: str, depth: int) -> Extraction:
    prompt = EXTRACTION_USER.format(
        filename=chunk.filename,
        location=chunk.location,
        source_id=chunk.id,
        content=text,
    )
    try:
        return _request(model, EXTRACTION_SYSTEM, prompt, Extraction)
    except LLMTruncated:
        if depth >= MAX_SPLIT_DEPTH or len(text) < 1200:
            raise
        first, second = _split_text(text)
        a = _extract(model, chunk, first, depth + 1)
        b = _extract(model, chunk, second, depth + 1)
        seen, items = set(), []
        for item in a.itens + b.itens:
            key = (item.categoria, item.citacao_literal.casefold())
            if key not in seen:
                seen.add(key)
                items.append(item)
        return Extraction(itens=items, lacunas=list(dict.fromkeys(a.lacunas + b.lacunas)))


def extract_chunk(model: str, chunk: Chunk) -> Extraction:
    # Se a resposta estourar o limite, o trecho é dividido e reprocessado; as citações
    # continuam sendo verificadas contra o trecho original completo (mesmo fonte_id).
    return _extract(model, chunk, chunk.text, 0)


def _report(model: str, prompt_args: dict, lines: list[str], depth: int) -> SectionReport:
    prompt = SYNTHESIS_USER.format(evidence_block="\n".join(lines), **prompt_args)
    try:
        return _request(model, SYNTHESIS_SYSTEM, prompt, SectionReport)
    except LLMTruncated:
        if depth >= MAX_SPLIT_DEPTH or len(lines) < 2:
            raise
        half = len(lines) // 2
        a = _report(model, prompt_args, lines[:half], depth + 1)
        b = _report(model, prompt_args, lines[half:], depth + 1)
        return SectionReport(pontos=a.pontos + b.pontos, lacunas=list(dict.fromkeys(a.lacunas + b.lacunas)))


def report_section(model: str, name: str, purpose: str, evidence_block: str, max_points: int = 16) -> SectionReport:
    args = {"section_name": name, "section_purpose": purpose, "max_points": max_points}
    return _report(model, args, evidence_block.splitlines(), 0)
