import io
from pathlib import Path

import fitz
import pytest
from docx import Document

from app.ingestion import ingest_file
from app.pipeline import process_files, quote_is_in_chunk
from app.report import create_report
from app.schemas import Extraction, LegalItem, ReportPoint, SectionReport
from app import pipeline


def test_quote_validation():
    assert quote_is_in_chunk("a parte NÃO comprovou", "O juiz: a parte NÃO   comprovou os fatos.")
    assert quote_is_in_chunk('"versão preliminar para homologação"', "E-mail: ‘versão preliminar para homologação.’")
    assert not quote_is_in_chunk("a parte comprovou", "O juiz: a parte não comprovou os fatos.")
    assert not quote_is_in_chunk("", "texto")


def test_pdf_has_page_references():
    doc = fitz.open()
    for idx in range(2):
        page = doc.new_page()
        page.insert_text((50, 50), f"PAGINA {idx+1} DESPACHO: requerimento de documentos aceito para exame posterior.")
    data = doc.tobytes()
    result = ingest_file("D001", "autos.pdf", data)
    assert result.total_pages == 2
    assert len(result.chunks) == 2
    assert result.chunks[0].id == "D001-P0001-T01"
    assert result.chunks[1].location.startswith("página 2")


def test_short_page_preserved():
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50,50), "INDEFIRO")
    result = ingest_file("D001", "curta.pdf", doc.tobytes())
    assert len(result.chunks) == 1
    assert result.sparse_pages == [1]


def test_docx_no_fake_page_number():
    buffer = io.BytesIO()
    word = Document()
    word.add_paragraph("A parte autora requer apresentação dos comprovantes de pagamento e perícia técnica.")
    word.save(buffer)
    result = ingest_file("D001", "peticao.docx", buffer.getvalue())
    assert len(result.chunks) == 1
    assert "sem página verificável" in result.chunks[0].location


def test_example_case_docx_is_readable():
    path = Path(__file__).parent.parent / "exemplos" / "processo_FICTICIO_Contrato_Tecnologia.docx"
    result = ingest_file("D001", path.name, path.read_bytes())
    text = " ".join(chunk.text for chunk in result.chunks)
    assert len(result.chunks) >= 2
    assert "IDENTIFICADOR DE SIMULAÇÃO: LEXIA-TESTE-2026-017" in text
    assert "AGENDADO" in text
    assert "versão preliminar para homologação" in text


def test_end_to_end_fake_model(tmp_path, monkeypatch):
    def mock_extract(model, chunk):
        quote = chunk.text[0: min(90, len(chunk.text))]
        return Extraction(itens=[LegalItem(
            categoria="alegacao", titulo="Reclamação contratual",
            descricao="Uma parte registra uma reclamação no trecho.",
            data="15/02/2026", atores=["parte autora"], importancia="alta",
            natureza="alegado", fonte_id=chunk.id,
            citacao_literal=quote, ressalva="Exige prova",
        )])

    def mock_synthesis(model, name, purpose, block, max_points=16):
        import re
        m = re.search(r'E\d{5}', block)
        return SectionReport(pontos=[ReportPoint(
            texto=f"Rever documentação relacionada a {name}.", fontes=[m.group(0)],
            natureza="hipotese_para_revisao", prioridade="media",
        )])
    monkeypatch.setattr(pipeline, "extract_chunk", mock_extract)
    monkeypatch.setattr(pipeline, "report_section", mock_synthesis)
    text = (Path(__file__).parent.parent / "exemplos" / "caso_FICTICIO_para_teste.txt").read_bytes()
    result = process_files("teste-local", [("ficticio.txt",text)], False, lambda *args:None)
    assert result["evidencias_verificadas"] > 0
    assert result["trechos_totais"] == result["trechos_analisados"]
    assert result["secoes"][0]["pontos"]
    out = tmp_path / "relatorio.docx"
    create_report(result, str(out))
    doc = Document(out)
    full_text = "\n".join(p.text for p in doc.paragraphs)
    assert "RELATÓRIO DE TRIAGEM JURÍDICA" in full_text
    assert "ANEXO I" in full_text
    assert "ficticio.txt" in full_text


def test_fake_quote_rejected(monkeypatch):
    def invalid(*args):
        chunk = args[1]
        return Extraction(itens=[LegalItem(
            categoria="fato", titulo="Inventado", descricao="Afirmação inexistente",
            data="", atores=[], importancia="alta", natureza="documentado",
            fonte_id=chunk.id, citacao_literal="Não existe no documento e foi fabricado",
            ressalva="",
        )])
    monkeypatch.setattr(pipeline, "extract_chunk", invalid)
    with pytest.raises(ValueError, match="citações literais verificáveis"):
        process_files("teste", [("documento.txt", b"As partes solicitaram a juntada da peticao em data incerta.")], False, lambda *args:None)


def test_model_failures_are_not_reported_as_unreadable_document(monkeypatch):
    def broken(*args):
        raise ValueError("resposta inválida do modelo")
    monkeypatch.setattr(pipeline, "extract_chunk", broken)
    with pytest.raises(ValueError, match="extração não foi concluída pelo modelo"):
        process_files("teste", [("documento.txt", "Texto legível e suficiente para a leitura do documento.".encode())], False, lambda *args:None)


def test_synthesis_failure_recovers_sections_from_verified_evidence(monkeypatch):
    def extract(model, chunk):
        quote = "As partes solicitaram a juntada da petição em data incerta."
        return Extraction(itens=[LegalItem(
            categoria="alegacao", titulo="Pedido de juntada", descricao="Pedido registrado no trecho.",
            data="", atores=[], importancia="alta", natureza="alegado", fonte_id=chunk.id,
            citacao_literal=quote, ressalva="",
        )])
    def broken_synthesis(*args, **kwargs):
        raise ValueError("resposta inválida")
    monkeypatch.setattr(pipeline, "extract_chunk", extract)
    monkeypatch.setattr(pipeline, "report_section", broken_synthesis)
    result = process_files("teste", [("documento.txt", "As partes solicitaram a juntada da petição em data incerta.".encode())], False, lambda *args:None)
    assert result["evidencias_verificadas"] == 1
    assert result["secoes"][0]["pontos"][0]["fontes"] == ["E00001"]
    assert result["secoes"][0]["pontos"][0]["texto"].startswith("Pedido de juntada:")
    assert any("recuperada pelo inventário" in recovery for recovery in result["recuperacoes"])
    assert not any("Relatório PARCIAL" in warning for warning in result["avisos"])


def test_example_docx_keeps_every_verified_evidence_when_synthesis_fails(monkeypatch):
    def extract(model, chunk):
        quote = chunk.text[:120]
        return Extraction(itens=[LegalItem(
            categoria="fato", titulo=f"Trecho {chunk.id}", descricao="Conteúdo extraído para revisão.",
            data="", atores=[], importancia="media", natureza="documentado", fonte_id=chunk.id,
            citacao_literal=quote, ressalva="",
        )])
    calls = 0
    def occasionally_broken_synthesis(model, name, purpose, block, max_points=16):
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise ValueError("resposta JSON inválida")
        import re
        match = re.search(r'"evidencia":\s*"(E\d+)"', block)
        return SectionReport(pontos=[ReportPoint(
            texto=f"Revisar evidência da seção {name}.", fontes=[match.group(1)],
            natureza="fato_documentado", prioridade="media",
        )]) if match else SectionReport()
    monkeypatch.setattr(pipeline, "extract_chunk", extract)
    monkeypatch.setattr(pipeline, "report_section", occasionally_broken_synthesis)
    path = Path(__file__).parent.parent / "exemplos" / "processo_FICTICIO_Contrato_Tecnologia.docx"
    result = process_files("teste", [(path.name, path.read_bytes())], False, lambda *args:None)
    assert result["trechos_totais"] == result["trechos_analisados"] == 2
    assert result["evidencias_verificadas"] == 2
    assert result["secoes"][0]["pontos"][0]["fontes"] == ["E00001"]
    assert result["secoes"][4]["pontos"]
    assert len(result["recuperacoes"]) == 1
    assert not result["falhas"]
    assert not any("Relatório PARCIAL" in warning for warning in result["avisos"])
