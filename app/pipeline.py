"""Processa todos os trechos, verifica citações, sintetiza em lotes e entrega inventário integral."""
from collections import Counter
from dataclasses import asdict
import json
import re
import unicodedata
from typing import Callable

from .ingestion import ingest_file, IngestedDoc, Chunk
from .llm import LLMFatalError, LLMRateLimitError, embed_texts, extract_chunk, report_section
from .prompts import SECTION_INSTRUCTIONS
from . import library


def _normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).replace("\u00ad", "").casefold()
    # DOCX/PDF e modelos podem variar aspas, travessões e pontuação sem alterar
    # as palavras citadas. Removê-los deixa a checagem robusta sem aceitar
    # paráfrases: todas as palavras da citação ainda precisam aparecer em ordem.
    return "".join(char for char in s if char.isalnum())


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


def _batches(items: list[dict], budget: int = 8000) -> list[str]:
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


def _evidence_points(items: list[dict]) -> list[dict]:
    """Deterministic synthesis fallback that preserves every verified source."""
    points = []
    for item in items:
        description = item["descricao"].strip()
        text = f"{item['titulo']}: {description}" if description else item["titulo"]
        points.append({
            "texto": text[:1500],
            "fontes": [item["evidencia_id"]],
            "natureza": "alegacao" if item["natureza"] == "alegado" else "fato_documentado",
            "prioridade": item["importancia"],
        })
    return points


def _extract(model: str, chunks: list[Chunk], update: Callable[[str, float], None]):
    evidence, rejected, failed = [], 0, []
    complete = 0
    rate_limited = None
    for n, chunk in enumerate(chunks, 1):
        update(f"Analisando trecho {n}/{len(chunks)} · {chunk.filename} · {chunk.location}", 0.08 + 0.62 * (n-1) / max(1, len(chunks)))
        try:
            extracted = extract_chunk(model, chunk)
        except LLMFatalError:
            raise  # Ollama fora do ar / modelo ausente: afeta todos os trechos; mostrar a causa real.
        except LLMRateLimitError as exc:
            rate_limited = str(exc)
            failed.append(f"{chunk.id}: {type(exc).__name__} ({rate_limited})")
            update(rate_limited, 0.08 + 0.62 * (n-1) / max(1, len(chunks)))
            break
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
        update(f"Trecho {n}/{len(chunks)} concluído · {len(evidence)} evidências verificadas", 0.08 + 0.62 * n / max(1, len(chunks)), {"type": "evidence", "evidence": evidence[-12:], "total": len(evidence)})
    if rate_limited and not evidence:
        raise LLMRateLimitError(f"Análise interrompida: {rate_limited} Nenhuma evidência foi validada; aguarde o limite reiniciar ou configure LEXIA_FALLBACK_PROVIDER=ollama.")
    return evidence, rejected, failed, complete


def process_files(model: str, uploads: list[tuple[str, bytes]], enable_ocr: bool,
                  update: Callable[[str, float], None], memory: list[str] | None = None) -> dict:
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
        if failed:
            raise ValueError(
                "A extração não foi concluída pelo modelo em nenhum trecho. "
                "Confira o provedor/modelo configurado e tente novamente. "
                f"Falhas: {len(failed)}. Primeiro erro: {failed[0]}"
            )
        raise ValueError(
            "O modelo não retornou citações literais verificáveis para o conteúdo extraído. "
            "O arquivo foi lido, mas a resposta do modelo não pôde ser confirmada; tente outro modelo."
        )
    try:
        vectors = embed_texts([item["titulo"] + " " + item["descricao"] for item in evidence])
        for item, vector in zip(evidence, vectors):
            item["embedding"] = vector
    except Exception:
        pass
    valid_ids = {x["evidencia_id"] for x in evidence}
    query = " ".join(x["titulo"] for x in evidence if x["importancia"] == "alta")[:1200]
    if not query:
        query = " ".join(x["titulo"] for x in evidence[:12])[:1200]
    references = library.search(query, limit=5) if query else []
    if evidence:
        try:
            query_vector = embed_texts([" ".join(item["titulo"] + " " + item["descricao"] for item in evidence[:20])])[0]
            semantic = []
            for item in evidence:
                vector = item.get("embedding")
                if vector and len(vector) == len(query_vector):
                    dot = sum(a * b for a, b in zip(vector, query_vector))
                    na = sum(a * a for a in vector) ** 0.5
                    nb = sum(a * a for a in query_vector) ** 0.5
                    item["relevance"] = dot / (na * nb) if na and nb else 0
                    semantic.append(item)
            evidence.sort(key=lambda item: item.get("relevance", -1), reverse=True)
        except Exception:
            pass
    for index, reference in enumerate(references, 1):
        reference["referencia_id"] = f"R{index:04d}"
    reference_ids = {x["referencia_id"] for x in references}
    reference_block = "\n".join(json.dumps({
        "referencia": x["referencia_id"], "categoria": x["category"],
        "arquivo": x["name"], "localizacao": x["location"],
        "trecho": x["excerpt"][:500], "fonte_url": x["source_url"],
        "territorio": x["territory"], "vigencia_informada": x["effective_date"],
        "verificacao": x["verification"],
    }, ensure_ascii=False) for x in references)
    memory = [x[:1500] for x in (memory or []) if x.strip()][:12]
    memory_block = "\n".join(f"- {item}" for item in memory)
    sections = []
    recoveries = []
    warnings = [f"{doc.filename}: {warn}" for doc in docs for warn in doc.warnings]
    if references:
        warnings.append("Referências da biblioteca foram recuperadas por termos; conteúdo, autoria, território e vigência exigem conferência humana.")
    if any(item.get("embedding") for item in evidence):
        for item in evidence:
            item.pop("embedding", None)
            item.pop("relevance", None)
        warnings.append("Busca semântica de embeddings locais aplicada para priorizar evidências; a ordenação não substitui a leitura integral.")
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
            use_references = name in {"Alegações, fundamentos e pedidos", "Contradições, riscos e questões para revisão", "Providências sugeridas para conferência humana"}
            if use_references and reference_block:
                block += "\nREFERÊNCIAS DE BIBLIOTECA (não são fatos do caso; vigência não verificada):\n" + reference_block
                batch_ids |= reference_ids
            if memory_block:
                block += "\nMEMÓRIA DO CASO (contexto fornecido anteriormente; não constitui prova dos documentos atuais):\n" + memory_block
            batch_number += 1
            update(f"Redigindo: {name} ({batch_number}/{total_batches})", 0.72 + 0.25 * (batch_number-1) / max(1, total_batches))
            result = None
            last_error = None
            for attempt in range(2):
                try:
                    result = report_section(model, name, purpose, block)
                    break
                except LLMFatalError:
                    raise
                except Exception as exc:
                    last_error = exc
                    if attempt == 0:
                        update(f"Repetindo síntese: {name}", 0.72 + 0.25 * (batch_number-1) / max(1, total_batches))
            if result is None:
                # Synthesis is editorial. Preserve a useful, source-linked section
                # from the already verified evidence instead of losing the analysis.
                recoveries.append(f"Síntese ({name}) recuperada pelo inventário: {type(last_error).__name__} ({str(last_error)[:180]})")
                points.extend(_evidence_points(picked))
            else:
                for pt in result.pontos:
                    clean_refs = list(dict.fromkeys(x for x in pt.fontes if x in (valid_ids | reference_ids) and x in batch_ids))
                    if not clean_refs:
                        rejected += 1
                        continue
                    point = pt.model_dump()
                    point["fontes"] = clean_refs
                    if not any(x in valid_ids for x in clean_refs):
                        point["natureza"] = "hipotese_para_revisao"
                    points.append(point)
                gaps.extend(result.lacunas)
        if capped:
            gaps.append("Seção transversal elaborada com até 65 evidências prioritárias; consultar inventário integral para os demais itens.")
        sections.append({"nome": name, "pontos": points, "lacunas": list(dict.fromkeys(gaps))})
        update(f"Seção pronta: {name}", 0.72 + 0.25 * batch_number / max(1, total_batches), {"type": "section", "section": sections[-1]})
    extraction_failures = [item for item in failed if not item.startswith("Síntese (")]
    if extraction_failures:
        warnings.append("Ocorreram falhas na extração de trechos. Relatório PARCIAL: não presuma cobertura integral; consulte as falhas no inventário.")
    if recoveries:
        warnings.append("Uma ou mais seções foram compostas diretamente do inventário de evidências verificadas após falha de síntese.")
        warnings.append(f"{len(recoveries)} seção(ões) recuperada(s) do inventário; detalhes registrados na auditoria.")
    if failed:
        warnings.append(f"Detalhes técnicos das {len(failed)} falha(s) e recuperações foram registrados no inventário de auditoria local.")
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
        "recuperacoes": recoveries,
        "secoes": sections,
        "evidencias": evidence,
        "referencias": references,
    }
