# Backlog — Fase 1 (fluxo completo com a Luma)

**Objetivo da fase:** o radar roda sozinho 3 vezes ao dia no GitHub Actions, lê os feeds iCal da Luma, filtra SP e online, classifica com o Claude, busca o preço dos aprovados e avisa no Telegram — sem avisar duas vezes e alertando quando uma fonte quebrar.

**Fora da Fase 1:** Meetup, deduplicação entre fontes, Google Calendar, botões, lembretes e resumo semanal.

## Regras dos tickets
- Cada ticket é uma fatia vertical que cabe numa janela de contexto (até ~500 linhas de lógica).
- **AFK:** o agente faz do começo ao fim. **HITL:** precisa de uma ação sua (indicada em "Sua parte").
- Fluxo de cada ticket: testador escreve os testes → implementador faz passar → revisor revisa em contexto limpo → PR → seu merge.
- Pronto = `scripts/verificar.sh` verde + critérios de aceite cumpridos + PR aberto.

## Tickets

| ID | Ticket | Tipo | Depende de |
|---|---|---|---|
| F1-01 | Esqueleto do projeto e CI | AFK | — |
| F1-02 | Fixtures reais da Luma | AFK + 1 clique | F1-01 |
| F1-03 | Coletor Luma (iCal → evento normalizado) | AFK | F1-02 |
| F1-04 | Filtro geográfico e horizonte | AFK | F1-03 |
| F1-05 | Banco: modelo, migrations e ciclo de vida | AFK | F1-03 |
| F1-06 | Classificador com LLM | AFK | F1-05 |
| F1-07 | Dataset de avaliação e métricas | HITL | F1-06 |
| F1-08 | Enriquecimento de preço da Luma | AFK | F1-05 |
| F1-09 | Notificador Telegram | HITL | F1-05 |
| F1-10 | Orquestração, agendamento e alertas | HITL | F1-04, F1-06, F1-08, F1-09 |
| F1-11 | Primeira semana real | HITL | F1-10 |

---

### F1-01 — Esqueleto do projeto e CI
- Estrutura `src/radar/` (pacotes `fontes`, `filtros`, `classificacao`, `enriquecimento`, `notificacao`, `banco`, `config`) e `tests/`.
- `pyproject.toml` com dependências fixadas; ruff (lint + formatação) e pytest.
- `scripts/verificar.sh`: ruff + pytest; é o critério de "pronto" de todo ticket.
- Workflow `testes.yml`: roda `verificar.sh` em todo push e PR.
- Configuração por variáveis de ambiente validadas com Pydantic Settings; `.env.exemplo` sem valores reais.
- **Aceite:** CI verde num PR com um teste trivial.

### F1-02 — Fixtures reais da Luma
- Workflow manual (`workflow_dispatch`) que baixa uma vez os feeds iCal da lista de organizadores e da cidade e 2 páginas de evento, e salva em `tests/fixtures/luma/` (regras de privacidade e de acesso na D9).
- Os feeds do Codex e do Vibe Space são confirmados aqui.
- **Sequência:**
  1. PR com o script (`radar.fontes.captura_fixtures`) e o workflow → merge na `main`.
  2. O autor clica em "Run workflow" (Actions → Capturar fixtures da Luma).
  3. O workflow captura, verifica e-mails e cria o branch `fase-1/f1-02-fixtures-dados-<N>` com as fixtures, em que `<N>` é o número da execução (`github.run_number`). Cada execução gera um branch novo; refazer não exige apagar o anterior.
  4. Claude revisa os dados do branch da execução escolhida, atualiza `docs/fase-0-viabilidade.md` com o resultado do Codex/Vibe Space e abre o segundo PR a partir desse branch.
  - Observação: push feito pelo `GITHUB_TOKEN` não dispara o `testes.yml`; o segundo PR precisa de um commit do Claude para o CI rodar.
- **Sua parte:** fazer os dois merges e clicar em "Run workflow" (1 clique).
- **Aceite:** fixtures versionadas; IDs do Codex e Vibe Space confirmados ou marcados como inválidos em `docs/fase-0-viabilidade.md`.

### F1-03 — Coletor Luma
- Lê iCal e produz `EventoNormalizado` (Pydantic): `id_fonte` (o `evt-…` do UID), título, início/fim em America/Sao_Paulo, local textual, coordenadas, status da fonte, link de inscrição (da DESCRIPTION), descrição, organizador.
- Lista de calendários em arquivo de configuração versionado (`config/fontes.yaml`).
- Erros por calendário isolados: um feed quebrado não derruba os outros; resultado diferencia "falha de extração" de "zero eventos".
- **Aceite:** testes sobre as fixtures cobrindo evento com endereço, com endereço oculto (`luma.com/event/evt-…`), sem GEO e cancelado.

### F1-04 — Filtro geográfico e horizonte
- Ordem da D6: GEO dentro da Grande SP → local textual → online. Sem evidência conclusiva: segue para o classificador marcado `localizacao=indefinida`.
- Horizonte: 30 dias (meetups/online) e 90 dias (conferências); como o tipo ainda não é conhecido antes do LLM, o filtro usa 90 dias e o corte de 30 é aplicado depois da classificação.
- **Aceite:** testes com eventos de SP, Helsinque, online e sem localização; nenhum descartado só por falta de GEO.

### F1-05 — Banco: modelo, migrations e ciclo de vida
- SQLAlchemy + Alembic. Tabelas: `eventos`, `execucoes`, `classificacoes`, `enriquecimentos`, `notificacoes`.
- Upsert por (`fonte`, `id_fonte`); `hash_conteudo` sobre campos relevantes normalizados.
- Estados: ativo, alterado, cancelado (só quando a fonte diz), encerrado, não encontrado (após N execuções ausente).
- Testes rodam em Postgres (container de serviço no CI).
- **Aceite:** migrations sobem do zero; testes de cada transição de estado.

### F1-06 — Classificador com LLM
- Saída Pydantic: `e_ia`, categoria, tópicos, tipo de evento, modalidade, idioma, tem tradução, estado de preço (só com evidência), resumo de 1 linha, trecho de evidência.
- Prompt versionado em arquivo (`prompts/classificador_v1.md`); chave = hash do conteúdo + versão do prompt + modelo + versão do schema.
- Registro por chamada: tokens, latência, custo; limite de chamadas por execução; conteúdo externo delimitado como dado.
- Testes com cliente falso (sem chamar a API).
- **Aceite:** reclassifica só quando a chave muda; saída inválida do LLM vira erro registrado, nunca evento aprovado em silêncio.

### F1-07 — Dataset de avaliação e métricas
- `avaliacao/dev.jsonl` e `avaliacao/teste.jsonl` separados; Vector Search e Grok Bot só no `dev` (D7).
- Script `avaliar.py`: precision, recall e F1 no conjunto escolhido, com a lista de erros.
- **Sua parte:** rotular ~30 eventos (eu preparo a planilha com os candidatos tirados das fixtures; você marca "é IA / não é" e o idioma).
- **Aceite:** primeiro relatório de métricas do prompt v1 no conjunto de teste, salvo em `docs/`.

### F1-08 — Enriquecimento de preço da Luma
- Implementa a D4: só eventos novos aprovados, limite por execução (padrão 10), cache por `id_fonte`, nenhuma repetição após erro, regras de moeda e oferta.
- **Aceite:** testes com as páginas das fixtures (gratuito, sem offers, resposta 403/429 → "preço desconhecido").

### F1-09 — Notificador Telegram
- Mensagem: título, data e hora, local ou "online", estado de preço, link de inscrição, resumo, etiquetas de idioma.
- Tabela de notificações com chave única (evento, tipo); "enviando" → envia → "enviado".
- Envio só para o `chat_id` configurado.
- **Sua parte:** criar o bot no @BotFather, mandar uma mensagem para ele e cadastrar `TELEGRAM_TOKEN` e `TELEGRAM_CHAT_ID` nos Secrets (eu passo o passo a passo).
- **Aceite:** testes com cliente falso; nenhuma notificação duplicada ao rodar duas vezes.

### F1-10 — Orquestração, agendamento e alertas
- Comando `radar executar`: coleta → filtro → banco → classificação → enriquecimento → avisos.
- Workflow agendado: 7h40, 12h40 e 19h40 (horário de SP), com grupo de concorrência.
- Alertas no Telegram: falha de extração; fonte vazia por várias execuções; inatividade perto do dia 50.
- **Sua parte:** criar o banco gratuito (Neon ou Supabase), cadastrar `DATABASE_URL` e `ANTHROPIC_API_KEY` nos Secrets e definir o limite mensal de gasto no console da Anthropic.
- **Aceite:** uma execução manual completa ponta a ponta com aviso real no seu Telegram.

### F1-11 — Primeira semana real
- O radar roda sozinho por 7 dias.
- **Sua parte:** a busca manual semanal (~15 min) e anotar falsos positivos e eventos perdidos.
- **Aceite:** lista de ajustes priorizada; prompt v2 se o recall ficar abaixo da referência.
