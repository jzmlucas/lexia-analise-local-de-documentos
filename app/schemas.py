from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class LegalItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    categoria: Literal[
        "identificacao", "fato", "alegacao", "pedido", "prova", "decisao",
        "prazo", "fundamento", "contradicao", "outro"
    ]
    titulo: str = Field(max_length=240)
    descricao: str = Field(max_length=2000)
    data: str = Field(max_length=80, description="Data como está no documento ou texto vazio")
    atores: list[str] = Field(default_factory=list, max_length=12)
    importancia: Literal["alta", "media", "baixa"]
    natureza: Literal["documentado", "alegado", "inferencia"]
    fonte_id: str = Field(description="Identificador exato do trecho fornecido")
    citacao_literal: str = Field(max_length=500, description="Trecho literal curto da fonte")
    ressalva: str = Field(max_length=500, description="Incerteza ou limitação, ou texto vazio")


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    itens: list[LegalItem] = Field(default_factory=list)
    lacunas: list[str] = Field(default_factory=list, max_length=20)


class ReportPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    texto: str = Field(max_length=1500)
    fontes: list[str] = Field(default_factory=list, description="IDs de evidências do inventário")
    natureza: Literal["fato_documentado", "alegacao", "inferencia", "hipotese_para_revisao"]
    prioridade: Literal["alta", "media", "baixa"]


class SectionReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pontos: list[ReportPoint] = Field(default_factory=list)
    lacunas: list[str] = Field(default_factory=list, max_length=20)
