# LexIA Local ⚖️

**Analisador de documentos jurídicos com IA executada localmente no Ollama.** O advogado arrasta arquivos para a interface web, o sistema extrai e analisa o conteúdo em trechos, confere as citações textuais retornadas pelo modelo e gera um relatório **Word (`.docx`)** acompanhado de uma auditoria **JSON**.

> **Atenção:** ferramenta de apoio à leitura. Não substitui a análise de um advogado, não valida automaticamente a veracidade das alegações e **não calcula prazos legais**. Toda conclusão deve ser revisada no documento original.

## 1. Requisitos

- **Windows 10/11**, Linux ou macOS.
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) instalado (`uv --version`). O projeto não requer `pip`.
- **Python 3.11+** (o `uv` pode instalar uma versão compatível, quando necessário).
- [Ollama](https://ollama.com/download) instalado e funcionando localmente.
- Internet **apenas para instalar dependências e baixar o modelo**. A análise documental é local.
- Opcional: **Tesseract OCR**, com idiomas português (`por`) e inglês (`eng`), para PDFs digitalizados.

## 2. Instalação rápida com uv (Windows / PowerShell)

Abra o terminal **na pasta `lexia-local`**, onde estão `pyproject.toml` e `README.md`.

**Passo 1 — Instalar as dependências Python:**

```powershell
uv sync
```

O `uv` cria automaticamente a pasta `.venv` e instala as dependências definidas em `pyproject.toml`. **Não é necessário ativar o ambiente virtual.** Na primeira execução com internet, o comando também gera `uv.lock` se ele ainda não existir.

**Passo 2 — Baixar o modelo no Ollama:**

```powershell
ollama pull qwen3.5:9b
```

**Passo 3 — Iniciar a aplicação:**

```powershell
uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**Passo 4 — Acessar a interface:**

Abra **http://127.0.0.1:8000**. Arraste os arquivos, selecione o modelo, inicie a análise e baixe o relatório `.docx` e a auditoria `.json`.

Nas próximas execuções, basta repetir o **Passo 3**, com o Ollama em execução. Você também pode usar `iniciar_windows.bat`, que chama `uv run`.

### Alternativa: escolher explicitamente o Python

Se o seu computador ainda não tiver um Python compatível:

```powershell
uv python install 3.12
uv sync --python 3.12
```

## 3. Instalação com uv (Linux / macOS)

```bash
# Na pasta lexia-local
uv sync
ollama pull qwen3.5:9b
uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Acesse **http://127.0.0.1:8000**. Se necessário, execute `ollama serve` em outro terminal; alguns sistemas já iniciam esse serviço automaticamente.

## 4. Testar com um documento fictício

O arquivo **`exemplos/processo_FICTICIO_Contrato_Tecnologia.docx`** é um dossiê inteiramente inventado, criado para testar o LexIA. Ele contém um contrato, relatos de duas partes, alegações de pagamento, mensagens, registros técnicos e uma decisão processual fictícia.

1. Inicie o Ollama e a aplicação pelos comandos acima.
2. Abra `http://127.0.0.1:8000` e arraste **`exemplos/processo_FICTICIO_Contrato_Tecnologia.docx`**.
3. Escolha **`qwen3.5:9b`** e inicie a análise.
4. Baixe `relatorio_juridico.docx` e `auditoria.json`.
5. Confira se o relatório distingue **alegações** de **provas**, identifica as **inconsistências intencionais** e apresenta citações localizáveis no arquivo.

**Pontos de teste esperados** (não são resultados garantidos do modelo):

- A empresa autora declara ter recebido apenas uma parcela, enquanto a ré alega ter pago duas; o comprovante da segunda parcela contém o status **"AGENDADO"**, não confirmação de liquidação.
- A autora afirma entrega concluída em **15/03/2026**, mas uma comunicação posterior descreve uma **"versão preliminar para homologação"**.
- Há registro de uma falha técnica e controvérsia sobre a conclusão do treinamento.
- O despacho simulado menciona prazo de manifestação, **sem informar data de intimação/publicação**: o sistema não deve calcular vencimento por suposição.

**Importante:** em arquivos DOCX o LexIA referencia **trechos**, não páginas fixas; use um PDF pesquisável quando quiser referências página a página. O documento fictício pode ser convertido em PDF pesquisável para esse teste adicional.

## 5. Comandos úteis do uv

| Comando | Função |
|---|---|
| `uv sync` | Cria/atualiza `.venv` com as dependências do projeto |
| `uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000` | Inicia a aplicação |
| `uv run pytest -q` | Executa os testes automatizados |
| `uv add nome-do-pacote` | Adiciona uma dependência de produção |
| `uv add --dev nome-do-pacote` | Adiciona uma dependência de desenvolvimento |
| `uv lock` | Atualiza o arquivo de versões resolvidas |
| `uv python list` | Lista as versões de Python detectadas/disponíveis |

O arquivo `requirements.txt` é mantido apenas como compatibilidade com instalações antigas; o fluxo recomendado agora é **`pyproject.toml` + `uv sync` + `uv run`**.

## 6. Recursos implementados

- Interface web com arrastar/soltar de até **40 arquivos**, com limite de **30 MB por arquivo** e **120 MB no total**.
- Formatos: **PDF, DOCX, TXT e MD**.
- PDFs pesquisáveis com referência à página; DOCX/TXT/MD com referência ao trecho.
- OCR local opcional via Tesseract para páginas PDF com texto insuficiente; páginas não legíveis são sinalizadas.
- Divisão em trechos com sobreposição e identificadores de fonte.
- Extração e síntese em duas etapas por meio de prompts especializados; saída JSON validada por Pydantic.
- Rejeição de citações que não estejam literalmente presentes no trecho correspondente.
- Relatório Word e inventário/auditoria JSON.
- Painel de cobertura do material: trechos analisados, avisos e falhas.

## 7. Arquitetura e principais arquivos

```text
lexia-local/
├── app/
│   ├── main.py              # API FastAPI, interface e tarefas
│   ├── ingestion.py         # Extração de PDF/DOCX/TXT/MD e OCR opcional
│   ├── prompts.py           # Prompts de extração e consolidação
│   ├── schemas.py           # Esquemas das respostas estruturadas
│   ├── llm.py               # Comunicação com o Ollama local
│   ├── pipeline.py          # Orquestração e verificação de evidências
│   ├── report.py            # Geração de relatórios .docx
│   └── static/              # Interface web
├── exemplos/
│   ├── processo_FICTICIO_Contrato_Tecnologia.docx
│   └── caso_FICTICIO_para_teste.txt
├── tests/                   # Testes automatizados
├── pyproject.toml           # Dependências e configuração para uv
├── requirements.txt         # Legado: não necessário com uv sync
├── iniciar_windows.bat
├── PROMPT_MESTRE_ANALISE_JURIDICA.md
└── PROMPTS_ESTRATEGIA.md
```

### Onde melhorar os prompts

- `app/prompts.py`: instruções de extração, síntese e seções jurídicas.
- `app/schemas.py`: estrutura JSON exigida do modelo.
- `app/pipeline.py`: validação textual independente do modelo.
- `PROMPT_MESTRE_ANALISE_JURIDICA.md` e `PROMPTS_ESTRATEGIA.md`: documentação detalhada.

## 8. Configuração opcional do Ollama

| Variável | Valor padrão | Significado |
|---|---|---|
| `OLLAMA_MODEL` | `qwen3.5:9b` | Modelo selecionado por padrão |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Endpoint Ollama local; o programa rejeita hosts externos |
| `OLLAMA_NUM_CTX` | `8192` | Contexto solicitado (aumentar pode consumir mais RAM/VRAM) |

Exemplo no PowerShell, **antes de iniciar a aplicação**:

```powershell
$env:OLLAMA_MODEL = "qwen3.5:9b"
$env:OLLAMA_NUM_CTX = "8192"
uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Se o modelo for pesado para o computador, instale outro modelo compatível com JSON estruturado no Ollama e selecione-o na interface. Verifique o uso real da GPU/CPU com `ollama ps`.

## 9. Executar os testes

```powershell
uv sync
uv run pytest -q
```

Os testes existentes usam respostas simuladas do modelo, portanto **não precisam de Ollama**. Eles verificam partes da extração, referências e geração de relatório, mas **não comprovam precisão jurídica de casos reais**.

## 10. Solução de problemas

**`uv` não é reconhecido:** instale o `uv` seguindo a [documentação oficial](https://docs.astral.sh/uv/getting-started/installation/) e reabra o terminal.

**`uv sync` não encontra pacotes:** verifique o acesso à internet e ao PyPI. O `uv.lock` não foi incluído no pacote inicial porque não houve acesso ao índice de pacotes para resolvê-lo neste ambiente.

**Ollama não responde:** execute `ollama list` para confirmar que o modelo foi baixado; inicie `ollama serve` se o serviço não estiver ativo.

**`ModuleNotFoundError`:** execute os comandos **dentro de `lexia-local`** e repita `uv sync`.

**PDF digitalizado sem texto:** instale o Tesseract com os idiomas adequados e ative o OCR local na interface. Revise as páginas com texto deficiente.

**Relatório incompleto:** consulte `auditoria.json` e os avisos de cobertura; reduza o volume de arquivos por execução ou use um modelo mais capaz. Não considere páginas ilegíveis como analisadas.

## 11. Segurança e limitações

- Use somente no computador local ou em ambiente explicitamente protegido. Este MVP **não tem autenticação nem isolamento multiusuário**. Não exponha `0.0.0.0` à internet.
- Os documentos são processados pelo Ollama local, mas relatórios temporários são gravados no disco. Use criptografia, controle de acesso e práticas adequadas de retenção.
- Citação literal validada **não demonstra a veracidade** da afirmação nem a correção da interpretação jurídica.
- PDFs digitalizados exigem OCR e validação manual; DOCX não mantém paginação fixa.
- O sistema **não consulta** andamento de tribunais, legislação atualizada ou jurisprudência e **não determina prazos**.
- A síntese executiva pode priorizar um subconjunto de evidências; consulte o anexo completo e o JSON de auditoria.
- Os jobs ficam em memória; reiniciar o servidor pode interromper análises em andamento.
- Em materiais com dados sensíveis, observe sigilo profissional, LGPD e revisão humana antes de utilizar os resultados.

---

**Demonstração para concurso:** avalie a cobertura, a precisão, o *recall* de fatos críticos, a taxa de falsas conclusões e a facilidade de localizar cada evidência no documento original. O conjunto fictício acompanha o projeto para ensaiar o fluxo completo sem usar dados reais de clientes.
