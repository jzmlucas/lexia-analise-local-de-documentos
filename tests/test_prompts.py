from app import prompts


def test_master_and_strategy_are_loaded():
    assert "ANALISTA DOCUMENTAL JURÍDICO" in prompts.EXTRACTION_SYSTEM
    assert "Estratégia de leitura:" in prompts.EXTRACTION_SYSTEM
    assert "Estratégia de leitura:" in prompts.SYNTHESIS_SYSTEM
    assert "{source_id}" in prompts.EXTRACTION_USER
    assert "{evidence_block}" in prompts.SYNTHESIS_USER


def test_missing_prompt_block_fails_clearly():
    try:
        prompts._block("## Outro\n\n```text\nTexto\n```", "Ausente")
    except RuntimeError as exc:
        assert "Bloco de prompt ausente" in str(exc)
    else:
        raise AssertionError("Bloco ausente deveria falhar")
