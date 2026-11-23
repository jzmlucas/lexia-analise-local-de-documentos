"""Carrega os prompts versionados em Markdown; documentos enviados são dados não confiáveis."""
from pathlib import Path
import re

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"
MASTER_PATH = PROMPT_DIR / "PROMPT_MESTRE_ANALISE_JURIDICA.md"
STRATEGY_PATH = PROMPT_DIR / "PROMPTS_ESTRATEGIA.md"


def _block(document: str, heading: str) -> str:
    pattern = rf"(?ms)^## {re.escape(heading)}\s*\n+\x60\x60\x60text\s*\n(.*?)\n\x60\x60\x60"
    match = re.search(pattern, document)
    if not match or not match.group(1).strip():
        raise RuntimeError(f"Bloco de prompt ausente: {heading}")
    return match.group(1).strip()


master = MASTER_PATH.read_text(encoding="utf-8")
strategy = STRATEGY_PATH.read_text(encoding="utf-8")
operational = _block(strategy, "Instruções operacionais")
EXTRACTION_SYSTEM = _block(master, "ETAPA 1 — PROMPT DE SISTEMA: EXTRAÇÃO") + "\n\n" + operational
EXTRACTION_USER = _block(master, "ETAPA 1 — PROMPT DE USUÁRIO: EXTRAÇÃO")
SYNTHESIS_SYSTEM = _block(master, "ETAPA 2 — PROMPT DE SISTEMA: SÍNTESE") + "\n\n" + operational
SYNTHESIS_USER = _block(master, "ETAPA 2 — PROMPT DE USUÁRIO: SÍNTESE")

for required, template in {
    "extração": (EXTRACTION_USER, {"filename", "location", "source_id", "content"}),
    "síntese": (SYNTHESIS_USER, {"section_name", "section_purpose", "max_points", "evidence_block"}),
}.items():
    text, fields = template
    missing = {field for field in fields if "{" + field + "}" not in text}
    if missing:
        raise RuntimeError(f"Prompt de {required} sem variáveis obrigatórias: {missing}")

SECTION_INSTRUCTIONS = [
    ("Resumo executivo", "Síntese do objeto, partes, ponto litigioso, momento processual documentado e fatos de maior impacto. Não afirmar andamento atual sem documento."),
    ("Identificação e contexto", "Identificar processo, partes, juízo, natureza da ação, representantes e recorte documental, sem completar dados ausentes."),
    ("Cronologia processual e fática", "Relacionar principais eventos e datas; diferenciar data do fato, protocolo e publicação; alertar inconsistências."),
    ("Alegações, fundamentos e pedidos", "Contrapor narrativa, teses, pedidos e defesas de partes distintas; separar pedido de decisão."),
    ("Provas e elementos documentais", "Identificar peças probatórias, o que elas efetivamente registram, impugnações, pontos controvertidos e limites probatórios."),
    ("Decisões, intimações e prazos mencionados", "Registrar comandos, atos e prazos expressos; jamais calcular vencimento ou afirmar situação atual de prazo."),
    ("Contradições, riscos e questões para revisão", "Comparar versões conflitantes, lacunas relevantes, riscos de interpretação e hipóteses investigativas para o advogado, sem conclusão categórica."),
    ("Providências sugeridas para conferência humana", "Checklist de perguntas e verificações para o advogado: documentos ausentes, status oficial, tempestividade, fontes originais e provas pertinentes."),
]
