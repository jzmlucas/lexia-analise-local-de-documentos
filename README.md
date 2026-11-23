# LexIA Local — análise documental para advocacia

LexIA extrai evidências de documentos, confere citações literais e produz relatório Word e auditoria JSON. Ollama é o provedor principal padrão; o administrador pode selecionar Gemini, Anthropic ou OpenAI no `.env`. A interpretação exige revisão profissional.

## Índice

1. [Inventário](#inventário)
2. [Clone e Docker local](#clone-e-docker-local)
3. [API keys e privacidade](#api-keys-e-privacidade)
4. [Tutorial de uso](#tutorial-de-uso)
5. [Execução Python e testes](#execução-python-e-testes)
6. [Arquitetura e limites](#arquitetura-e-limites)
7. [Prompts jurídicos](#prompts-jurídicos)
8. [Biblioteca de referências](#biblioteca-de-referências)

## Inventário

- Upload múltiplo: PDF, DOCX, XLSX, CSV, TXT, MD, PNG, JPG/JPEG e TIFF. Limites de 40 arquivos, 30 MB por arquivo e 120 MB no total.
- Extração de PDF por página; OCR opcional para PDF escasso e imagens via Tesseract português/inglês. DOCX tem referência por trecho; CSV/XLSX incluem número de linha no texto extraído.
- Trechos identificados, hash SHA-256 do arquivo, citação literal validada, natureza classificada como documento, alegação ou inferência. Afirmações sem citação válida são descartadas.
- Inventário por documento e síntese consolidada: resumo, contexto, cronologia, alegações, fundamentos, pedidos, provas, decisões, prazos mencionados, contradições, riscos e providências de revisão.
- Relatório DOCX com fontes e anexo de evidências; JSON com resultados, cobertura, avisos e falhas.
- Casos persistentes, notas humanas rotuladas e busca lexical por evidência em `GET /api/cases/{id}/search?q=termo`. Anotações não se tornam fatos automaticamente.
- Catálogo de fontes em `GET /api/legal-sources`: Planalto, STF e STJ, com categoria/território. Não confirma vigência nem indexa conteúdo.
- Biblioteca local de referências com upload, categorias, metadados, busca textual, download e exclusão. Os trechos recuperados podem orientar hipóteses do relatório, com IDs distintos das provas do caso.
- Autenticação HTTP Basic com token único no Docker; provedor principal configurável entre Ollama, Gemini, Claude/Anthropic e OpenAI, com fallback opcional.
- API: `/api/health`, `/api/cases`, `/api/cases/{id}/notes`, `/api/cases/{id}/search`, `/api/legal-sources`, `/api/library`, `/api/library/search`, `/api/jobs`, `/api/jobs/{id}` e downloads.

## Clone e Docker local

Pré-requisitos: Git, Docker com Compose, memória para o modelo e internet inicial para imagens e modelo. Substitua a URL de clone pelo endereço real do repositório:

```bash
git clone https://github.com/SEU-USUARIO/lexia-analise-local-de-documentos.git
cd lexia-analise-local-de-documentos
cp .env.example .env
```

No PowerShell: `Copy-Item .env.example .env`. O arquivo deve ter **uma variável por linha, sem barras no fim**. Defina `LEXIA_API_TOKEN` com um valor longo e aleatório, sem aspas. Você pode gerar um token com `-join ((1..4) | ForEach-Object { [guid]::NewGuid().ToString('N') })` no PowerShell. Não publique o `.env`; ele está no `.gitignore`. Se o Compose reclamar que `LEXIA_API_TOKEN` está ausente, confirme que o arquivo se chama exatamente `.env`, está ao lado de `compose.yaml` e que a linha não está vazia.

`OLLAMA_MODEL` no `.env` define o modelo baixado e usado pela aplicação; o padrão é `qwen3.5:9b`. Se mudar, escolha um modelo compatível com saída JSON estruturada e com os recursos da máquina.

```bash
docker compose up -d --build
docker compose ps
```

Na primeira subida com Ollama como principal ou fallback, o serviço `ollama-pull` espera o Ollama iniciar, baixa `OLLAMA_MODEL` e termina. A LexIA só inicia após a etapa concluir. O primeiro download pode demorar minutos e requer internet. Acompanhe com `docker compose logs -f ollama-pull`. Se falhar por conexão ou espaço, corrija a causa e repita `docker compose up -d --build`. Para conferir o modelo: `docker compose exec ollama ollama list`.

Abra [http://127.0.0.1:8000](http://127.0.0.1:8000). No HTTP Basic, use qualquer usuário (ex.: `lexia`) e a senha igual ao valor de `LEXIA_API_TOKEN` do `.env`. Não existe senha padrão. O Compose publica apenas no loopback. O padrão usa CPU; GPU requer configuração adicional do Docker. `docker compose down` mantém os volumes; `down -v` apaga dados e modelos.

## API keys e privacidade

Por padrão, `LEXIA_PRIMARY_PROVIDER=ollama` e `LEXIA_ALLOW_CLOUD=false`; nenhuma chave é necessária. Para fazer **Gemini o provedor principal**, use estas linhas no `.env` (uma por linha, sem barras no fim):

```dotenv
LEXIA_PRIMARY_PROVIDER=gemini
LEXIA_ALLOW_CLOUD=true
GEMINI_API_KEY=cole_sua_chave_aqui
GEMINI_MODEL=gemini-2.5-flash
LEXIA_FALLBACK_PROVIDER=
```

Recrie o serviço com `docker compose up -d --build --force-recreate lexia`. Ao abrir a página, o status mostrará “gemini configurado”. Nesse modo, os trechos dos documentos são enviados ao Gemini para extração e síntese. O Compose pula o download do modelo Ollama quando ele não é principal nem fallback. Para usar Ollama se o Gemini falhar, defina `LEXIA_FALLBACK_PROVIDER=ollama`. Sem isso, a falha do Gemini é mostrada no job.

Para Ollama principal com fallback em nuvem, use `LEXIA_PRIMARY_PROVIDER=ollama`, `LEXIA_ALLOW_CLOUD=true`, `LEXIA_FALLBACK_PROVIDER=gemini` e `GEMINI_API_KEY`. Também é possível escolher `anthropic` ou `openai` como principal, usando a chave correspondente. Não exponha as chaves no Git ou na interface. A seleção de provedor precisa ser autorizada para documentos sigilosos e pode gerar custos no serviço externo.

## Tutorial de uso

1. Autentique-se em `http://127.0.0.1:8000`, confira o provedor exibido e, se usar Ollama, atualize a lista de modelos após o download.
2. Crie um caso pelo nome ou mantenha análise avulsa. O caso associa análises persistentes.
3. Em “Biblioteca jurídica”, clique em “Adicionar documentos” para cadastrar materiais de referência. Escolha a categoria e, se souber, informe origem, território e vigência. Esses arquivos ficam disponíveis para análises futuras e são separados dos autos.
4. Escolha os documentos do caso no bloco “Envie os autos”. Ative OCR para imagens e PDF digitalizado.
5. Inicie e acompanhe a análise; ao concluir, leia os avisos e baixe DOCX e JSON.
6. Confira cada citação e localização no original. O arquivo `exemplos/processo_FICTICIO_Contrato_Tecnologia.docx` serve para ensaio.
7. Consulte análises anteriores por `GET /api/jobs/ID` e evidências do caso por `GET /api/cases/ID/search?q=pagamento`. A interface cria casos; notas e busca estão na API.

## Execução Python e testes

Requer Python 3.11+, `uv`, Ollama local e Tesseract com `por`/`eng` para OCR.

```bash
uv sync
ollama pull qwen3.5:9b
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
uv run pytest -q
```

Defina `LEXIA_API_TOKEN` para exigir senha. Sem token, a API aceita apenas loopback. `LEXIA_DATA_DIR` define a pasta persistente (padrão `data/`). `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `OLLAMA_NUM_CTX` e `OLLAMA_NUM_PREDICT` configuram Ollama. `OLLAMA_ALLOWED_HOST` é usado no Compose para o serviço privado `ollama`.

O modelo de embeddings local é configurado com `OLLAMA_EMBED_MODEL` (padrão `nomic-embed-text`) e baixado junto com o modelo de chat pelo Compose. A biblioteca armazena vetores por trecho e combina similaridade semântica com busca lexical; instalações sem suporte ao endpoint `/api/embed` continuam funcionando em modo lexical. A prévia da análise chega progressivamente pela rota SSE `/api/jobs/{id}/events`; o endpoint de status também expõe os últimos eventos. Para casos persistidos, notas humanas e pontos de síntese de até três análises anteriores entram no contexto como memória, claramente separados das provas atuais.

## Arquitetura e limites

`app/ingestion.py` extrai; `app/llm.py` chama os modelos; `app/pipeline.py` valida e sintetiza; `app/report.py` gera Word; `app/storage.py` persiste casos e jobs; `app/library.py` mantém a biblioteca; `app/main.py` serve API e `app/static/`. `compose.yaml` cria FastAPI e Ollama com volumes `lexia_data` e `ollama_data`. Originais dos autos são processados em memória; relatórios, auditorias, metadados e os originais da biblioteca persistem. Um job interrompido por reinício não é retomado automaticamente.


Há um único token de operador. O campo de escritório é metadado, sem isolamento multiempresa. A biblioteca é compartilhada por todos os casos desta instância. Não exponha o serviço publicamente. Proteja host, volumes e backups. Embeddings são executados localmente quando Ollama está configurado; se indisponíveis, a busca retorna ao modo lexical.

O catálogo jurídico é apenas um diretório de fontes oficiais. A biblioteca oferece recuperação textual sobre os arquivos carregados pelo escritório; não existe corpus legislativo ou jurisprudencial nacional pré-carregado, cobertura nacional completa, atualização automática, verificação de vigência ou monitoramento de tribunais. Confira redação, vigência e território de normas na fonte oficial. O sistema não calcula prazos nem confirma andamento. Uma citação literal prova somente a presença do texto no trecho, não a veracidade da alegação. OCR pode errar. O relatório não deve ser usado como parecer ou peça sem revisão jurídica.

## Prompts jurídicos

Os prompts ficam em [`prompts/`](prompts/): [prompt mestre](prompts/PROMPT_MESTRE_ANALISE_JURIDICA.md) e [estratégia](prompts/PROMPTS_ESTRATEGIA.md). Ao iniciar, `app/prompts.py` lê os quatro blocos de extração/síntese do prompt mestre e acrescenta o bloco “Instruções operacionais” da estratégia aos prompts de sistema. Assim, esses arquivos são usados pela análise, inclusive no Docker. As seções JSON do documento mestre são referência; os esquemas efetivos estão em `app/schemas.py`.

Para alterar o comportamento, edite os blocos `text` desses arquivos e **reinicie a aplicação**. Com Docker, execute `docker compose up -d --build` para copiar os prompts atualizados à imagem. Não altere os marcadores `{filename}`, `{location}`, `{source_id}`, `{content}`, `{section_name}`, `{section_purpose}`, `{max_points}` e `{evidence_block}`: são campos obrigatórios do fluxo. O sistema interrompe a inicialização se um bloco ou marcador necessário estiver ausente. As propostas de versões futuras na estratégia são documentação e não ativam recursos por si só.

## Biblioteca de referências

Na interface, abra “Biblioteca jurídica” → “Adicionar documentos”. Escolha uma categoria: legislação, jurisprudência, doutrina, modelos/peças ou outras referências. Selecione arquivos PDF, DOCX, XLSX, CSV, TXT, MD ou imagens. O upload extrai texto e aplica OCR às imagens e páginas PDF com texto escasso. Material sem texto recuperável é recusado. Você pode pesquisar trechos, baixar o original e excluir um documento pela mesma área.

No volume `lexia_data`, cada referência fica em `/data/biblioteca/escritorio-local/<categoria>/<id>/` com `original.ext`, `metadata.json` e `index.json`. O nome enviado aparece nos metadados; o caminho físico usa ID gerado para evitar conflitos e travessia de diretórios. Limites por envio: 40 arquivos, 30 MB cada e 120 MB no total. A exclusão remove original e índice, mas relatórios já gerados preservam o trecho e os metadados que utilizaram.

Na análise, uma busca lexical nos títulos das evidências recupera até cinco trechos da biblioteca. O sistema os apresenta ao modelo apenas em seções jurídicas pertinentes, com IDs `R0001` etc. Pontos baseados somente em referências são marcados como hipótese para revisão. O Word inclui um anexo com os trechos recuperados; o JSON mantém os mesmos registros. O ID `E` designa evidência extraída dos autos; o ID `R` designa referência da biblioteca. A recuperação não verifica vigência, autoridade, território ou aplicabilidade. Origem, território e vigência são informações fornecidas pelo usuário e precisam de conferência.
