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
    with pytest.raises(ValueError, match="Nenhuma evidência"):
        process_files("teste", [("documento.txt", b"As partes solicitaram a juntada da peticao em data incerta.")], False, lambda *args:None)
