"""Testa a integração com o Ollama usando um servidor HTTP falso (sem modelo real)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app import llm
from app.ingestion import Chunk
from app.schemas import Extraction

ITEM = {
    "categoria": "alegacao", "titulo": "T" * 400, "descricao": "d", "data": None,
    "atores": None, "importancia": "alta", "natureza": "alegado",
    "fonte_id": "D001-TXT-T01", "citacao_literal": "texto do documento", "ressalva": None,
}


class FakeOllama:
    def __init__(self, handler):
        self.requests = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(body)
                status, payload = handler(body, len(outer.requests))
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()


@pytest.fixture
def fake(monkeypatch):
    servers = []

    def make(handler):
        s = FakeOllama(handler)
        servers.append(s)
        monkeypatch.setattr(llm, "OLLAMA_URL", s.url)
        return s

    yield make
    for s in servers:
        s.close()


CHUNK = Chunk("D001-TXT-T01", "D001", "a.txt", "trecho 1", "texto do documento " * 200)


def ok(content, **extra):
    return 200, {"message": {"content": content}, "done_reason": "stop", **extra}


def test_request_disables_thinking_and_inlines_schema(fake):
    s = fake(lambda body, n: ok(json.dumps({"itens": [ITEM], "lacunas": None})))
    result = llm.extract_chunk("m", CHUNK)
    body = s.requests[0]
    assert body["think"] is False
    assert "$defs" not in json.dumps(body["format"]) and "$ref" not in json.dumps(body["format"])
    assert "maxLength" not in json.dumps(body["format"])
    # null e textos acima do limite são normalizados em vez de derrubar a leitura
    assert result.itens[0].data == "" and result.itens[0].atores == [] and len(result.itens[0].titulo) == 240
    assert result.lacunas == []


def test_thinking_model_empty_content_gets_clear_error(fake):
    fake(lambda body, n: ok(""))
    with pytest.raises(ValueError, match="incompatível"):
        llm.extract_chunk("m", CHUNK)


def test_json_in_code_fence_is_accepted(fake):
    fake(lambda body, n: ok("```json\n" + json.dumps({"itens": [ITEM]}) + "\n```"))
    assert len(llm.extract_chunk("m", CHUNK).itens) == 1


def test_retry_after_bad_json(fake):
    s = fake(lambda body, n: ok("{quebrado") if n == 1 else ok(json.dumps({"itens": []})))
    assert llm.extract_chunk("m", CHUNK) == Extraction()
    assert len(s.requests) == 2


def test_truncated_output_splits_chunk(fake):
    def handler(body, n):
        if n == 1:
            return 200, {"message": {"content": '{"itens": [{"cat'}, "done_reason": "length"}
        return ok(json.dumps({"itens": [ITEM]}))
    s = fake(handler)
    result = llm.extract_chunk("m", CHUNK)
    assert len(s.requests) == 3 and len(result.itens) == 1  # dividiu em 2; item duplicado removido


def test_model_not_found_is_fatal_with_clear_message(fake):
    fake(lambda body, n: (404, {"error": "model 'm' not found"}))
    with pytest.raises(llm.LLMFatalError, match="ollama pull m"):
        llm.extract_chunk("m", CHUNK)


def test_ollama_down_is_fatal(monkeypatch):
    monkeypatch.setattr(llm, "OLLAMA_URL", "http://127.0.0.1:9")
    with pytest.raises(llm.LLMFatalError, match="conectar ao Ollama"):
        llm.extract_chunk("m", CHUNK)


def test_think_unsupported_retries_without_it(fake):
    def handler(body, n):
        if "think" in body:
            return 400, {"error": "\"m\" does not support thinking"}
        return ok(json.dumps({"itens": []}))
    s = fake(handler)
    llm.extract_chunk("m", CHUNK)
    assert "think" not in s.requests[-1]


def test_server_error_includes_ollama_message(fake):
    fake(lambda body, n: (500, {"error": "boom específico"}))
    with pytest.raises(llm.LLMError, match="boom específico"):
        llm.extract_chunk("m", CHUNK)
