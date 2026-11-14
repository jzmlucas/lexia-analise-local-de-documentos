from datetime import datetime
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt, RGBColor


def _cell(row, text: str):
    row.text = str(text)


def _setup(doc):
    sec = doc.sections[0]
    sec.top_margin = Inches(.83)
    sec.bottom_margin = Inches(.72)
    sec.left_margin = Inches(.95)
    sec.right_margin = Inches(.95)
    styles = doc.styles
    styles["Normal"].font.name = "Aptos"
    styles["Normal"].font.size = Pt(10)
    styles["Normal"].paragraph_format.space_after = Pt(6)
    for heading in ["Title", "Heading 1", "Heading 2"]:
        styles[heading].font.name = "Aptos Display"
        styles[heading].font.color.rgb = RGBColor(22, 83, 78)
    footer = sec.footer.paragraphs[0]
    footer.text = "LexIA Local  •  Relatório automatizado para conferência profissional  •  Confidencial"
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.style = "Caption"


def create_report(data: dict, out_path: str) -> None:
    doc = Document()
    _setup(doc)
    title = doc.add_paragraph(style="Title")
    title.add_run("RELATÓRIO DE TRIAGEM JURÍDICA").bold = True
    subtitle = doc.add_paragraph("Análise documental assistida por IA local • Não substitui revisão jurídica")
    subtitle.style = "Subtitle"
    doc.add_paragraph(f"Gerado em {datetime.now():%d/%m/%Y às %H:%M}  |  Modelo: {data['modelo']}")
    doc.add_heading("Painel de cobertura e confiabilidade", level=1)
    cover = doc.add_table(rows=0, cols=2)
    cover.style = "Light Shading Accent 1"
    for label, value in [
        ("Arquivos recebidos", data["arquivos_recebidos"]),
        ("Arquivos com extração iniciada", len(data["documentos"])),
        ("Trechos extraídos", data["trechos_totais"]),
        ("Trechos analisados com êxito", data["trechos_analisados"]),
        ("Evidências com citação verificada", data["evidencias_verificadas"]),
        ("Afirmações descartadas por referência inválida", data["afirmacoes_descartadas"]),
        ("Falhas no processamento", len(data["falhas"])),
    ]:
        row = cover.add_row().cells
        _cell(row[0], label)
        _cell(row[1], str(value))
    doc.add_paragraph(
        "Atenção: uma citação textual válida NÃO confirma a veracidade de uma alegação, "
        "nem comprova que a interpretação da IA esteja correta. Conferir autos completos e andamento oficial."
    )
    if data["avisos"]:
        doc.add_heading("Advertências e limitações", level=2)
        for warning in dict.fromkeys(data["avisos"]):
            doc.add_paragraph(warning, style="List Bullet")
    doc.add_heading("Arquivos considerados", level=1)
    for docmeta in data["documentos"]:
        page_text = f"{docmeta['total_pages']} página(s)" if docmeta['filename'].lower().endswith('.pdf') else "paginação indisponível"
        doc.add_paragraph(
            f"{docmeta['id']} | {docmeta['filename']} | {page_text} | "
            f"{docmeta['trechos']} trecho(s) | SHA-256: {docmeta['sha256']}",
            style="List Bullet",
        )
    index = {x["evidencia_id"]: x for x in data["evidencias"]}
    for section in data["secoes"]:
        doc.add_heading(section["nome"], level=1)
        if not section["pontos"]:
            doc.add_paragraph("Não foram consolidados pontos com referências suficientes nesta seção.")
        for point in section["pontos"]:
            p = doc.add_paragraph(style="List Bullet")
            lead = p.add_run(f"[{point['natureza']} | {point['prioridade']}] ")
            lead.bold = True
            p.add_run(point["texto"])
            refs = []
            for evidence_id in point["fontes"]:
                if evidence_id in index:
                    evidence = index[evidence_id]
                    refs.append(f"{evidence_id}: {evidence['arquivo']} ({evidence['localizacao']})")
            if refs:
                r = doc.add_paragraph("FONTES: " + "; ".join(refs))
                r.style = "Caption"
        if section["lacunas"]:
            doc.add_paragraph("Pendências e ressalvas:").runs[0].bold = True
            for gap in section["lacunas"]:
                doc.add_paragraph(gap, style="List Bullet")
    doc.add_page_break()
    doc.add_heading("ANEXO I — Inventário integral de evidências verificadas", level=1)
    doc.add_paragraph(
        "Cada item abaixo possui uma citação literal encontrada no trecho identificado. "
        "O inventário permanece completo mesmo quando as seções sintéticas usam uma amostra."
    )
    for evidence in data["evidencias"]:
        p = doc.add_paragraph()
        r = p.add_run(f"{evidence['evidencia_id']} · {evidence['titulo']} ")
        r.bold = True
        p.add_run(f"[{evidence['categoria']} · {evidence['natureza']} · {evidence['importancia']}]")
        doc.add_paragraph(evidence["descricao"])
        if evidence["data"]:
            doc.add_paragraph("Data informada: " + evidence["data"])
        literal = doc.add_paragraph('Citação literal: “' + evidence["citacao_literal"] + '”')
        literal.style = "Quote"
        line = f"Origem: {evidence['arquivo']} · {evidence['localizacao']} · ID {evidence['fonte_id']}"
        doc.add_paragraph(line).style = "Caption"
        if evidence["ressalva"]:
            doc.add_paragraph("Ressalva: " + evidence["ressalva"]).style = "Caption"
    if data["falhas"]:
        doc.add_heading("ANEXO II — Falhas que exigem reprocessamento", level=1)
        for failure in data["falhas"]:
            doc.add_paragraph(failure, style="List Bullet")
    doc.add_heading("Termo de uso e revisão", level=1)
    doc.add_paragraph(
        "Documento elaborado automaticamente para apoiar triagem documental. O modelo pode omitir "
        "informações, interpretar incorretamente peças ou produzir hipóteses equivocadas. "
        "A avaliação probatória, a apuração de prazos e a decisão jurídica permanecem sob "
        "responsabilidade do profissional habilitado. Não utilizar como parecer ou peça processual "
        "sem verificação das fontes originais."
    )
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_path)
