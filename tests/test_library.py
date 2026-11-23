from types import SimpleNamespace

import pytest

from app import library
from app.ingestion import Chunk


def test_library_upload_search_and_delete(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "ROOT", tmp_path / "biblioteca")
    extracted = SimpleNamespace(
        sha256="hash", total_pages=2, warnings=[],
        chunks=[Chunk("Rabc-P0002-T01", "Rabc", "lei.pdf", "página 2 · trecho 1",
                      "O contrato de prestação de serviços exige pagamento comprovado.")],
    )
    monkeypatch.setattr(library, "ingest_file", lambda *args: extracted)
    record = library.add_document("legislacao", "../lei.pdf", b"pdf",
                                  source_url="https://example.org/lei", territory="Brasil")
    folder = library.ROOT / "legislacao" / record["id"]
    assert (folder / "original.pdf").read_bytes() == b"pdf"
    assert (folder / "index.json").exists()
    assert record["name"] == "lei.pdf"
    hits = library.search("pagamento contrato")
    assert hits and hits[0]["document_id"] == record["id"]
    assert hits[0]["location"].startswith("página 2")
    assert library.delete_document("legislacao", record["id"])
    assert not folder.exists()


def test_library_rejects_invalid_category_and_url(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="Categoria"):
        library.add_document("../outside", "lei.txt", b"texto")
    with pytest.raises(ValueError, match="URL"):
        library.add_document("legislacao", "lei.txt", b"texto", source_url="http://example.org")
