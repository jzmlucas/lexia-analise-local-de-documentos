"""Gera relatório FICTÍCIO de exposição com simulação explícita, sem executar Ollama."""
from pathlib import Path
import re
from app import pipeline
from app.schemas import Extraction, LegalItem, ReportPoint, SectionReport
from app.report import create_report


def fake_extract(model, chunk):
    source = chunk.text
    terms = [
        ("fato", "Protocolo da inicial", "Petição inicial, protocolada em 10/04/2026.", "10/04/2026", "documentado"),
        ("alegacao", "Data alegada pela autora", "data combinada de 28/02/2026", "28/02/2026", "alegado"),
        ("alegacao", "Versão divergente da ré", "a data combinada era 30/03/2026", "30/03/2026", "alegado"),
        ("prova", "Comprovante juntado", "comprovante de pagamento datado de 16/02/2026", "16/02/2026", "documentado"),
        ("decisao", "Despacho para manifestação", "Intimem-se as partes para se manifestarem sobre eventual produção de prova pericial em quinze dias.", "28/04/2026", "documentado"),
    ]
    items=[]
    for cat,title,q,date,nature in terms:
        if q in source:
            items.append(LegalItem(categoria=cat, titulo=title,
               descricao=title+" — informação presente no texto fictício para revisão do advogado.",
               data=date, atores=[], importancia="alta", natureza=nature,
               fonte_id=chunk.id,citacao_literal=q,ressalva="Exigir análise da fonte integral."))
    return Extraction(itens=items)


def fake_report(model,name,purpose,block,max_points=16):
    ids=re.findall(r'"evidencia": "(E\d+)"',block)
    return SectionReport(pontos=[ReportPoint(
        texto=f"Exemplo fictício de triagem para {name.lower()}. Verificar a divergência entre datas, o comprovante, o despacho e a data da intimação nos autos.",
        fontes=ids[:3], natureza="hipotese_para_revisao", prioridade="alta",
    )] if ids else [], lacunas=["Este é um relatório DEMONSTRATIVO SIMULADO; não constitui análise de IA real."])

pipeline.extract_chunk=fake_extract
pipeline.report_section=fake_report
root=Path(__file__).resolve().parents[1]
source=(root/'exemplos'/'caso_FICTICIO_para_teste.txt').read_bytes()
res=pipeline.process_files('DEMONSTRAÇÃO SIMULADA — SEM OLLAMA', [('caso_FICTICIO_para_teste.txt',source)],False,lambda *args:None)
res['avisos'].insert(0,'DEMONSTRAÇÃO FICTÍCIA COM RESULTADOS SIMULADOS. NÃO GERADO POR OLLAMA.')
create_report(res,str(root/'exemplos'/'relatorio_DEMO_FICTICIO.docx'))
print('Gerado relatório de demonstração com',res['evidencias_verificadas'],'evidências.')
