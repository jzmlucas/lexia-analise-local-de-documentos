# LexIA Local — Roteiro de apresentação para concurso

## Pitch de 30 segundos

> Escritórios recebem processos com dezenas ou centenas de páginas. O problema não é apenas ler rápido, mas encontrar o que importa sem perder a origem da informação. O LexIA Local organiza documentos jurídicos em um inventário auditável de fatos, alegações, pedidos, provas e decisões, cita de qual arquivo e página cada evidência saiu, e entrega um relatório editável. E faz isso com modelo de IA local, reduzindo a necessidade de compartilhar peças com provedores externos.

## Demonstração em aproximadamente três minutos

1. Abrir `http://127.0.0.1:8000`, mostrar a conexão ao Ollama local.
2. Arrastar dois ou mais PDFs/DOCX/TXT **fictícios ou autorizados** com narrativas divergentes. Selecionar modelo baixado no Ollama.
3. Iniciar a análise e mostrar o avanço por trecho, sem envio a endpoints externos de IA.
4. Ao terminar, mostrar contagem de evidências, rejeições por citações inválidas, páginas ilegíveis e falhas de leitura.
5. Abrir o Word: demonstrar resumo, linha do tempo, riscos, prazo *mencionado* (sem cálculo) e Inventário Integral com citações literais e localização.
6. Abrir JSON de auditoria para explicar como cada conclusão pode ser verificada.

## O que diferencia de um chatbot com um PDF anexado?

- Análise por unidades de texto em vez de depender de um contexto único gigante.
- Separação explícita de **alegação** e **fato documentado**.
- Exigência de fonte literal, com validação por código (não apenas pedir que o próprio LLM 'seja cuidadoso').
- Painel de cobertura para não esconder páginas/falhas.
- Word editável + inventário completo, útil no fluxo do advogado.
- Processamento local, sem API de IA paga.

## Métricas técnicas recomendadas para avaliação

| Métrica | Como calcular |
|---|---|
| Cobertura dos trechos | trechos analisados / trechos extraídos |
| Taxa de referências rejeitadas | itens rejeitados / total de itens retornados pelo modelo |
| *Recall* de fatos importantes | fatos críticos identificados pelo sistema / total de fatos anotados por especialistas |
| Precisão das afirmações | afirmações corretas avaliadas por especialista / afirmações avaliadas |
| Fidelidade das fontes | % de pontos em que a fonte sustenta exatamente a interpretação feita |
| Economia de tempo | tempo de triagem manual − tempo com LexIA, em teste comparável |
| Privacidade operacional | verificar que Ollama e interface escutam apenas em loopback e que não há APIs externas de análise |

**Importante:** a validação literal de citação **não** mede fidelidade semântica. Uma frase pode existir na página e o modelo ainda assim interpretá-la incorretamente. A métrica de fidelidade requer avaliação humana independente.

## Checklist de preparo para a apresentação

- Baixar o modelo com antecedência: `ollama pull qwen3.5:9b` ou modelo menor se hardware exigir.
- Executar `ollama ps` para verificar CPU/GPU e `python -m pytest -q` para testes internos.
- Ter PDFs pesquisáveis e um conjunto sintético que permita conferir referências claramente.
- Validar OCR offline se for mostrar páginas escaneadas.
- Usar documentos fictícios ou autorizados e confirmar previamente que o relatório fica legível no Word.
- Nunca prometer 100% de precisão, 'substituição do advogado' nem cálculo automático de prazo sem calendário e dados oficiais.

## Evolução: versão para escritórios

1. Visor PDF que abre exatamente a página da evidência citada.
2. Banco local criptografado com versionamento de processos, permissões por advogado e retenção configurável.
3. RAG local semântico/híbrido para cotejar alegações que aparecem em peças muito distantes.
4. Agente auditor independente para procurar omissões, contradições e citações semanticamente desconexas.
5. Painel de revisão humana para aceitar/rejeitar cada ponto e exportar somente conteúdo aprovado.
6. Testes com processos sintéticos e avaliadores jurídicos para medir precisão e recall antes de qualquer uso sensível.

## Encerramento sugerido

> O LexIA Local não tenta substituir a advocacia. Ele transforma leitura documental repetitiva em uma análise organizada, local e verificável. O advogado continua decidindo; a tecnologia ajuda a encontrar o que precisa ser visto.
