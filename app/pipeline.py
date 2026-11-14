"""Processa todos os trechos, verifica citações, sintetiza em lotes e entrega inventário integral."""
from collections import Counter
from dataclasses import asdict
import json
import re
import unicodedata
from typing import Callable

from .ingestion import ingest_file, IngestedDoc, Chunk
from .llm import extract_chunk, report_section
from .prompts import SECTION_INSTRUCTIONS


def _normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).replace("\u00ad", "")
    return re.sub(r"\s+", " ", s).strip().casefold()


def quote_is_in_chunk(quote: str, chunk_text: str) -> bool:
    normalized = _normalize(quote)
    return len(normalized) >= 10 and normalized in _normalize(chunk_text)


def _as_block(item: dict) -> str:
    return json.dumps({
        "evidencia": item["evidencia_id"],
        "categoria": item["categoria"],
        "titulo": item["titulo"][:150],
        "descricao": item["descricao"][:850],
        "data": item["data"],
        "natureza": item["natureza"],
        "importancia": item["importancia"],
        "arquivo": item["arquivo"],
        "localizacao": item["localizacao"],
        "citacao": item["citacao_literal"][:280],
        "ressalva": item["ressalva"][:300],
    }, ensure_ascii=False)


def _batches(items: list[dict], budget: int = 10800) -> list[str]:
    result, pending, size = [], [], 0
    for item in items:
        fragment = _as_block(item)
        if pending and size + len(fragment) > budget:
            result.append("\n".join(pending))
            pending, size = [], 0
        pending.append(fragment)
        size += len(fragment)
    if pending:
        result.append("\n".join(pending))
    return result


def _chosen(name: str, evidence: list[dict]) -> tuple[list[dict], bool]:
    selected: list[dict]
    if name == "Resumo executivo":
        selected = evidence
    elif name == "Identificação e contexto":
        selected = [x for x in evidence if x["categoria"] in {"identificacao", "outro"}]
    elif name == "Cronologia processual e fática":
        selected = [x for x in evidence if x["data"] or x["categoria"] in {"fato", "decisao", "prazo"}]
    elif name == "Alegações, fundamentos e pedidos":
        selected = [x for x in evidence if x["categoria"] in {"alegacao", "pedido", "fundamento"}]
    elif name == "Provas e elementos documentais":
        selected = [x for x in evidence if x["categoria"] in {"prova", "fato", "contradicao"}]
    elif name == "Decisões, intimações e prazos mencionados":
        selected = [x for x in evidence if x["categoria"] in {"decisao", "prazo"}]
    elif name == "Contradições, riscos e questões para revisão":
        selected = [x for x in evidence if x["categoria"] in {"contradicao", "alegacao", "prova", "fato", "prazo"}]
    else:
        selected = evidence
    priority = {"alta": 0, "media": 1, "baixa": 2}
    selected = sorted(selected, key=lambda x: (priority[x["importancia"]], x["evidencia_id"]))
    # As seções transversais trabalham com amostra prioritária; inventário final não é cortado.
    cap = 65 if name in {"Resumo executivo", "Providências sugeridas para conferência humana"} else None
    capped = cap is not None and len(selected) > cap
    return (selected[:cap] if cap else selected), capped


def _extract(model: str, chunks: list[Chunk], update: Callable[[str, float], None]):
    evidence, rejected, failed = [], 0, []
    complete = 0
    for n, chunk in enumerate(chunks, 1):
        update(f"Analisando trecho {n}/{len(chunks)} · {chunk.filename} · {chunk.location}", 0.08 + 0.62 * (n-1) / max(1, len(chunks)))
        try:
            extracted = extract_chunk(model, chunk)
        except Exception as exc:
            failed.append(f"{chunk.id}: {type(exc).__name__} ({str(exc)[:180]})")
            continue
        complete += 1
        for item in extracted.itens:
            if item.fonte_id != chunk.id or not quote_is_in_chunk(item.citacao_literal, chunk.text):
                rejected += 1
                continue
            record = item.model_dump()
            record.update({
                "evidencia_id": f"E{len(evidence)+1:05d}",
                "arquivo": chunk.filename,
                "localizacao": chunk.location,
                "documento_id": chunk.doc_id,
            })
            evidence.append(record)
    return evidence, rejected, failed, complete


def process_files(model: str, uploads: list[tuple[str, bytes]], enable_ocr: bool,
                  update: Callable[[str, float], None]) -> dict:
    update("Extraindo texto dos documentos", 0.02)
    docs: list[IngestedDoc] = []
    ingest_failures: list[str] = []
    for index, (filename, data) in enumerate(uploads, 1):
        try:
            docs.append(ingest_file(f"D{index:03d}", filename, data, enable_ocr))
        except Exception as exc:
            ingest_failures.append(f"Documento {index} ({filename}) não processado: {type(exc).__name__}: {str(exc)[:180]}")
    chunks = [chunk for doc in docs for chunk in doc.chunks]
    if not chunks:
        raise ValueError("Não há texto legível para análise. Habilite OCR e verifique os PDFs.")
    evidence, rejected, failed, complete = _extract(model, chunks, update)
    failed = ingest_failures + failed
    if not evidence:
        raise ValueError(
            "Nenhuma evidência com citação literal verificável foi produzida. "
            "Experimente outro modelo ou um documento mais legível."
            + (f" Trechos com erro: {len(failed)}." if failed else "")
        )
    valid_ids = {x["evidencia_id"] for x in evidence}
    sections = []
    warnings = [f"{doc.filename}: {warn}" for doc in docs for warn in doc.warnings]
    if rejected:
        warnings.append(f"{rejected} afirmações do modelo foram descartadas por citação/ID não verificável.")
    if failed:
        warnings.append(f"{len(failed)} trechos falharam na análise; confira a lista de erros e não assuma cobertura integral.")
    total_batches = 0
    for name, _ in SECTION_INSTRUCTIONS:
        picked, _ = _chosen(name, evidence)
        total_batches += len(_batches(picked))
    batch_number = 0
    for name, purpose in SECTION_INSTRUCTIONS:
        picked, capped = _chosen(name, evidence)
        points, gaps = [], []
        for block in _batches(picked):
            batch_ids = set(re.findall(r'"evidencia":\s*"(E\d+)"', block))
            batch_number += 1
            update(f"Redigindo: {name} ({batch_number}/{total_batches})", 0.72 + 0.25 * (batch_number-1) / max(1, total_batches))
            try:
                result = report_section(model, name, purpose, block)
            except Exception as exc:
                failed.append(f"Síntese ({name}): {type(exc).__name__} ({str(exc)[:180]})")
                continue
            for pt in result.pontos:
                clean_refs = list(dict.fromkeys(x for x in pt.fontes if x in valid_ids and x in batch_ids))
                if not clean_refs:
                    rejected += 1
                    continue
                point = pt.model_dump()
                point["fontes"] = clean_refs
                points.append(point)
            gaps.extend(result.lacunas)
        if capped:
            gaps.append("Seção transversal elaborada com até 65 evidências prioritárias; consultar inventário integral para os demais itens.")
        sections.append({"nome": name, "pontos": points, "lacunas": list(dict.fromkeys(gaps))})
    if failed:
        warnings.append("Ocorreram falhas de leitura/síntese. Relatório PARCIAL: consultar os erros no inventário.")
    if not any(section["pontos"] for section in sections):
        warnings.append("Síntese indisponível: inventário de evidências verificadas continua disponível.")
    update("Preparando relatório Word", 0.98)
    return {
        "modelo": model,
        "arquivos_recebidos": len(uploads),
        "documentos": [dict(asdict(doc), chunks=None) | {"trechos": len(doc.chunks)} for doc in docs],
        "trechos_totais": len(chunks),
        "trechos_analisados": complete,
        "evidencias_verificadas": len(evidence),
        "afirmacoes_descartadas": rejected,
        "tipos": dict(Counter(x["categoria"] for x in evidence)),
        "avisos": warnings,
        "falhas": failed,
        "secoes": sections,
        "evidencias": evidence,
    }
