from dataclasses import dataclass, field
import hashlib
import io
import csv
import re
from pathlib import Path

import fitz
from docx import Document

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".csv", ".xlsx", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
MAX_FILE_BYTES = 30 * 1024 * 1024
MAX_TOTAL_BYTES = 120 * 1024 * 1024
MAX_FILES = 40
CHUNK_CHARS = 6500
CHUNK_OVERLAP = 350


@dataclass
class Chunk:
    id: str
    doc_id: str
    filename: str
    location: str
    text: str


@dataclass
class IngestedDoc:
    id: str
    filename: str
    sha256: str
    total_pages: int
    unreadable_pages: list[int] = field(default_factory=list)
    ocr_pages: list[int] = field(default_factory=list)
    sparse_pages: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    chunks: list[Chunk] = field(default_factory=list)


def _chunks_from_text(doc: IngestedDoc, text: str, position: str, marker: str) -> None:
    if not text.strip():
        return
    text = text.replace("\x00", " ").strip()
    starts = list(range(0, len(text), CHUNK_CHARS - CHUNK_OVERLAP))
    for index, start in enumerate(starts, 1):
        chunk = text[start : start + CHUNK_CHARS].strip()
        if not chunk or len(chunk) < CHUNK_OVERLAP and index > 1:
            continue
        doc.chunks.append(
            Chunk(
                id=f"{doc.id}-{marker}-T{index:02d}",
                doc_id=doc.id,
                filename=doc.filename,
                location=f"{position} · trecho {index}",
                text=chunk,
            )
        )


def _ocr_page(page) -> str | None:
    try:
        import pytesseract
        from PIL import Image

        image = page.get_pixmap(matrix=fitz.Matrix(1.8, 1.8), alpha=False)
        pil = Image.frombytes("RGB", [image.width, image.height], image.samples)
        return pytesseract.image_to_string(pil, lang="por+eng", config="--psm 3")
    except Exception:
        return None


def _docx_blocks(data: bytes) -> str:
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(io.BytesIO(data))
    blocks = []
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            paragraph = Paragraph(child, document)
            if paragraph.text.strip():
                blocks.append(paragraph.text)
        elif child.tag == qn("w:tbl"):
            table = Table(child, document)
            for row in table.rows:
                blocks.append(" | ".join(cell.text.strip().replace("\n", " / ") for cell in row.cells))
    return "\n".join(blocks)


def _image_text(data: bytes) -> str:
    try:
        from PIL import Image
        import pytesseract
        return pytesseract.image_to_string(Image.open(io.BytesIO(data)), lang="por+eng")
    except Exception:
        return ""


def ingest_file(doc_id: str, name: str, data: bytes, enable_ocr: bool = False) -> IngestedDoc:
    filename = Path(name.replace("\\", "/")).name
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Formato não suportado: {filename}")
    if not data:
        raise ValueError(f"Arquivo vazio: {filename}")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"Arquivo maior que 30 MB: {filename}")
    doc = IngestedDoc(
        id=doc_id,
        filename=filename,
        sha256=hashlib.sha256(data).hexdigest(),
        total_pages=1,
    )
    if ext == ".pdf":
        try:
            pdf = fitz.open(stream=data, filetype="pdf")
        except Exception as exc:
            raise ValueError(f"PDF inválido ou protegido: {filename}") from exc
        with pdf:
            if pdf.needs_pass:
                raise ValueError(f"PDF protegido por senha: {filename}")
            doc.total_pages = len(pdf)
            for page_index, page in enumerate(pdf, 1):
                text = page.get_text(sort=True)
                # Texto digitalizado pode ter só número de página; OCR opcional.
                if len(re.sub(r"\s+", "", text)) < 40 and enable_ocr:
                    ocr_text = _ocr_page(page)
                    if ocr_text and len(re.sub(r"\s+", "", ocr_text)) >= 40:
                        text = ocr_text
                        doc.ocr_pages.append(page_index)
                if not text.strip():
                    doc.unreadable_pages.append(page_index)
                    continue
                if len(re.sub(r"\s+", "", text)) < 40:
                    doc.sparse_pages.append(page_index)
                _chunks_from_text(doc, text, f"página {page_index}", f"P{page_index:04d}")
        if doc.sparse_pages:
            doc.warnings.append("Texto muito escasso nas páginas " + ", ".join(map(str, doc.sparse_pages[:60])) + "; verificar extração/OCR manualmente.")
        if doc.unreadable_pages:
            doc.warnings.append(
                f"{len(doc.unreadable_pages)} página(s) sem texto legível: "
                + ", ".join(map(str, doc.unreadable_pages[:60]))
                + ("..." if len(doc.unreadable_pages) > 60 else "")
                + (". Ative OCR local ou forneça PDF pesquisável." if not enable_ocr
                   else ". OCR indisponível ou insuficiente nessas páginas.")
            )
    elif ext == ".docx":
        try:
            text = _docx_blocks(data)
        except Exception as exc:
            raise ValueError(f"DOCX inválido: {filename}") from exc
        _chunks_from_text(doc, text, "trecho textual (sem página verificável)", "DOC")
        doc.warnings.append("DOCX não oferece paginação confiável: referências indicam trechos identificados.")
    elif ext == ".xlsx":
        try:
            from openpyxl import load_workbook
            book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            for sheet in book:
                lines = (f"linha {i}: " + " | ".join(str(v) if v is not None else "" for v in row)
                         for i, row in enumerate(sheet.iter_rows(values_only=True), 1))
                _chunks_from_text(doc, "\n".join(lines), f"planilha {sheet.title}", f"XLS-{len(doc.chunks)+1}")
            book.close()
        except Exception as exc:
            raise ValueError(f"XLSX inválido: {filename}") from exc
    elif ext in {".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
        if not enable_ocr:
            doc.warnings.append("Imagem requer OCR ativado para leitura textual.")
        else:
            _chunks_from_text(doc, _image_text(data), "imagem (OCR; conferir original)", "IMG")
            if not doc.chunks:
                doc.warnings.append("OCR de imagem indisponível ou sem texto legível.")
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = data.decode("cp1252")
            except UnicodeError as exc:
                raise ValueError(f"Codificação de texto não suportada: {filename}") from exc
        if ext == ".csv":
            rows = csv.reader(io.StringIO(text))
            text = "\n".join(f"linha {i}: " + " | ".join(row) for i, row in enumerate(rows, 1))
        _chunks_from_text(doc, text, "trecho textual (sem página verificável)", "TXT")
        doc.warnings.append("Texto simples não tem páginas: referências indicam trechos identificados.")
    if not doc.chunks:
        doc.warnings.append("Nenhum trecho legível foi extraído deste documento.")
    return doc
