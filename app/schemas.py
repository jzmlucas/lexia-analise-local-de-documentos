"""Esquemas das respostas do modelo.

Modelos locais pequenos frequentemente ultrapassam limites de tamanho, devolvem
``null`` em vez de texto vazio ou emitem listas longas demais. Rejeitar a resposta
inteira por isso derruba a leitura do documento; aqui os valores são normalizados
(cortados / convertidos) e só estrutura realmente inválida é recusada.

Os limites NÃO aparecem no JSON Schema enviado ao Ollama (``maxLength`` e
``maxItems`` deixam a geração restrita por gramática lenta e instável).
"""
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def _text(limit: int):
    def clip(value):
        if value is None:
            return ""
        if isinstance(value, (int, float)):
            value = str(value)
        if isinstance(value, str):
            return value.strip()[:limit]
        return value

    return BeforeValidator(clip)


def _str_list(limit_items: int, limit_chars: int = 400):
    def clip(value):
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if isinstance(value, list):
            return [str(v).strip()[:limit_chars] for v in value if v is not None][:limit_items]
        return value

    return BeforeValidator(clip)


class LegalItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    categoria: Literal[
        "identificacao", "fato", "alegacao", "pedido", "prova", "decisao",
        "prazo", "fundamento", "contradicao", "outro"
    ]
    titulo: Annotated[str, _text(240)]
    descricao: Annotated[str, _text(2000)]
    data: Annotated[str, _text(80)] = Field(default="", description="Data como está no documento ou texto vazio")
    atores: Annotated[list[str], _str_list(12)] = Field(default_factory=list)
    importancia: Literal["alta", "media", "baixa"]
    natureza: Literal["documentado", "alegado", "inferencia"]
    fonte_id: Annotated[str, _text(120)] = Field(description="Identificador exato do trecho fornecido")
    citacao_literal: Annotated[str, _text(500)] = Field(description="Trecho literal curto da fonte")
    ressalva: Annotated[str, _text(500)] = Field(default="", description="Incerteza ou limitação, ou texto vazio")


class Extraction(BaseModel):
    model_config = ConfigDict(extra="ignore")
    itens: list[LegalItem] = Field(default_factory=list)
    lacunas: Annotated[list[str], _str_list(20, 500)] = Field(default_factory=list)


class ReportPoint(BaseModel):
    model_config = ConfigDict(extra="ignore")
    texto: Annotated[str, _text(1500)]
    fontes: Annotated[list[str], _str_list(40, 20)] = Field(default_factory=list, description="IDs de evidências do inventário")
    natureza: Literal["fato_documentado", "alegacao", "inferencia", "hipotese_para_revisao"]
    prioridade: Literal["alta", "media", "baixa"]


class SectionReport(BaseModel):
    model_config = ConfigDict(extra="ignore")
    pontos: list[ReportPoint] = Field(default_factory=list)
    lacunas: Annotated[list[str], _str_list(20, 500)] = Field(default_factory=list)
