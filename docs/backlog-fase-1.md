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
| F1-07b | Ajustes após a 1ª avaliação (diagnóstico, comunidades, prompt v2) | HITL | F1-07 |
| F1-08 | Enriquecimento de preço da Luma | AFK | F1-05 |
| F1-09 | Notificador Telegram | HITL | F1-05 |
| F1-10a | Decisão de destino | AFK | F1-06, F1-08 |
| F1-10b | Orquestração e CLI | AFK | F1-04, F1-06, F1-08, F1-09, F1-10a |
| F1-10c | Resumo semanal e alertas | AFK | F1-10b |
| F1-10d | Agendamento, banco real e aceite ponta a ponta | HITL | F1-10c |
| F1-11 | Primeira semana real | HITL | F1-10d |

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
- `python -m radar.avaliacao --conjunto teste|dev --saida <arquivo.md>`: precision, recall e F1 no conjunto escolhido, com a lista de erros.
- Workflow manual `avaliar.yml` publica o relatório em `docs/avaliacao/` num branch novo (D14).
- **Sua parte:**
  - [x] criar a workspace na Anthropic com limite de US$ 5 e cadastrar `ANTHROPIC_API_KEY` nos Secrets;
  - [x] rotular os 40 eventos da planilha (é IA / não é, e o idioma), D14;
  - [x] gerar `avaliacao/teste.jsonl` (30) e `avaliacao/dev.jsonl` (10) a partir da planilha;
  - [x] rodar **Avaliar classificador** com `teste` e revisar o relatório (`docs/avaliacao/teste-classificador_v1.md`, recall 0,41 → F1-07b).
- **Aceite:** primeiro relatório de métricas do prompt v1 no conjunto de teste, salvo em `docs/avaliacao/`.

### F1-07b — Ajustes após a 1ª avaliação
- **Parte A — diagnóstico, comunidades e rótulos (D15):** `comunidade_ia` em `config/fontes.yaml` (AI Collective, Vibe Space, Codex SP, WoMakersCode) conta como IA depois do LLM, só com saída válida — aplicado na avaliação (`classificar_evento`); no radar fica para a leitura no F1-10, sem gravar no banco; `--diagnostico` grava a resposta bruta dos erros `saida_*`, publicada como artifact (7 dias), nunca no banco nem no repositório; rótulos de idioma corrigidos mecanicamente (idioma só onde o texto declara).
- **Parte B — prompt v2 (D16):** causa das respostas cortadas era o thinking adaptativo do Haiku 5.5 (tokens de thinking no `max_tokens`) → `thinking: disabled` em toda chamada; `prompts/classificador_v2.md` com termos que contam como IA (vibe coding, agentes/agents, LLM, GenAI, ferramentas), "na dúvida, e_ia true", idioma só com declaração explícita e trechos de até 120 caracteres; v1 mantido como histórico; workflow com sufixo `classificador_v2`; nova avaliação comparada com a v1 (o teste já foi visto: comparação, não medida independente).
- **Parte C — localização e prompt v3 (D17):** avaliação do v2 (teste P 0,905 / R 0,864 / F1 0,884; dev R 0,700) sem truncamentos; os 10 erros restantes eram do campo localização com filtro decidido → a localização do LLM é descartada antes da validação quando o filtro decidiu (fim de `saida_localizacao_indevida`); `prompts/classificador_v3.md` com "título em inglês não é evidência" de idioma; workflow com sufixo `classificador_v3`; nova avaliação do v3.
- **Aceite:** parte A com testes e `verificar.sh` verde; parte B com relatório da v2 em `docs/avaliacao/`.
- **Resultado final (v3, 2026-11-25):** teste P 0,909 / R 0,909 / F1 0,909 (20 de 22 eventos de IA; perdas: "Revenue Summit", sem IA no texto, e 1 `saida_schema_invalido:categoria` em "Hack for Humanity"); dev P 1,0 / R 1,0. Custo do teste US$ 0,016. Relatórios v1, v2 e v3 em `docs/avaliacao/`. Concluído.
- **Pendências registradas (fora do F1-07b):** `categoria` passou de 40 caracteres uma vez — o schema não expressa o limite ao modelo; em produção o evento é tentado de novo até 3 vezes e depois vai ao resumo semanal (D13), então decidir no F1-10 se vale cortar a categoria no código (resolvido na D20: não cortar, mantém o erro de schema); conjunto de teste já visto (D15) e com poucos negativos — renovar com uma captura nova antes da próxima versão do prompt.

### F1-08 — Enriquecimento de preço da Luma
- Implementa a D4, detalhada na D18: só eventos classificados como IA (ou de comunidade de IA) sem enriquecimento para o hash atual, limite por execução (padrão 10) contando eventos processados, nenhuma repetição após erro, regras de moeda e oferta, combinação com o preço do LLM (`estado_preco_efetivo`). Sem BeautifulSoup (`html.parser`).
- Testes com fixtures sintéticas em `tests/fixtures/enriquecimento/` (no formato do JSON-LD real de `tests/fixtures/luma/pagina_1.html`) e `httpx.MockTransport`.
- **Aceite:** testes com as páginas das fixtures (gratuito, sem offers, resposta 403/429 → "preço desconhecido").

### F1-09 — Notificador Telegram
- Implementa a D19: httpx (sem python-telegram-bot), texto puro sem prévia de link, cortes por campo e resumo semanal em partes, sanitização de URL e texto, token protegido, `enviando`/`enviado` com commit por mensagem.
- Mensagem: título, data e hora, local ou "online", estado de preço, link de inscrição, resumo, etiquetas de idioma.
- Tabela de notificações com chave única (evento, tipo); "enviando" → envia → "enviado".
- Envio só para o `chat_id` configurado.
- Nunca notificar evento com `localizacao='fora'`, mesmo que esteja `ativo`/`alterado` no banco (revisão do F1-05).
- Antes de exibir `link_inscricao`/`local_url`: recusar URL com usuário/senha (`https://user:senha@…`) e remover caracteres invisíveis de formatação (categoria Cf, ex.: U+202E) (revisão do F1-03).
- **Sua parte:** criar o bot no @BotFather, mandar uma mensagem para ele e cadastrar `TELEGRAM_TOKEN` e `TELEGRAM_CHAT_ID` nos Secrets (eu passo o passo a passo).
- Etiqueta "idioma não confirmado" para idioma `desconhecido`; etiqueta com o idioma para estrangeiro confirmado (`en`/`outro`) no resumo semanal (D13).
- Mensagens do resumo semanal para eventos em falha de classificação ("Não classificado — confira manualmente") e do aviso excepcional (D13).
- **Aceite:** testes com cliente falso; nenhuma notificação duplicada ao rodar duas vezes.

### F1-10a — Decisão de destino
- Função pura `radar.orquestracao.destino.decidir_destino` com as regras e a ordem da D20: estado, `fora`, terminou, falha de classificação (aviso excepcional antes do próximo resumo, senão resumo semanal), sem classificação, não IA com exceção da comunidade de IA (D15, aplicada na leitura, nunca gravada), `indefinida` com LLM `fora` descartado (D19), horizonte por tipo (conferência e hackathon 90 dias; demais 30, limite inclusivo, D13), encaminhamento por idioma (D13) com "exclusivamente online" da D20. Preço nunca muda o destino.
- **Aceite:** testes da função pura; nada de banco nem rede.

### F1-10b — Orquestração e CLI
- Implementa a D21: ordem e commits entre etapas, `ErroEtapa` com rollback só da etapa, `radar executar` e `radar migrar` separados, reenvio das pendentes antes dos pedidos novos, resumo semanal e próximo resumo provisórios até o F1-10c, log só com contagens e códigos, códigos de saída 0/1/2/3, um único cliente HTTP para coleta e enriquecimento.
- Comando `radar executar`: coleta → filtro → banco → classificação → enriquecimento → decisão de destino → avisos; e `radar migrar`.
- Leituras de repositório para a decisão (saída `ok` da chave atual, enriquecimento do hash atual, `eventos_com_falha_classificacao`).
- Commit depois da classificação (o classificador só faz `flush`) e registro do `ResumoClassificacao` (interrupções, restantes, custo estimado).
- Chamar `enriquecer_precos` depois do commit da classificação (a fila depende da classificação `ok`) e commitar em seguida (D18).
- Commitar classificação, enriquecimento e estado dos eventos antes de chamar `notificar` (ele recusa transação com escritas pendentes, D19).
- Usar `estado_preco_efetivo` (página × LLM) na mensagem (D18).
- Em toda execução, pedir de novo as `notificacoes_pendentes` ('enviando' de execuções anteriores), inclusive partes do resumo semanal, sem esperar segunda (D19).
- **Aceite:** execução completa com fontes, LLM e Telegram falsos e Postgres de teste.

### F1-10c — Resumo semanal e alertas
- `proximo_resumo` (D20): na 1ª execução de segunda sem marco semanal, `agora`; senão, a próxima segunda 00:00 em SP. Validar `proximo_resumo >= agora` em `decidir_destino` (pré-condição com `ValueError`, revisão do F1-10a).
- Migração `0002`: registro de envios sem evento (alertas), com deduplicação própria — não cabem em `notificacoes` (D19).
- Resumo semanal na 1ª execução de segunda-feira (D19), com marco semanal; eventos em falha de classificação entram como "Não classificado — confira manualmente" e aviso excepcional se acontecerem antes do próximo resumo (D13).
- Alertas no Telegram (regras da D20, seção F1-10c): falha de extração; fonte vazia (6 execuções `vazio` seguidas num calendário que já teve `ok`); `robots_bloqueado` e erros sistemáticos do enriquecimento (Plano B da D4, D18); inatividade a partir de 50 dias sem commit na `main`. No máximo 1 alerta por (tipo, alvo) por dia de SP.
- **Aceite:** testes de deduplicação e dos gatilhos de cada alerta.

### F1-10d — Agendamento, banco real e aceite ponta a ponta (HITL)
- Workflow agendado `40 10,15,22 * * *` (UTC = 7h40, 12h40 e 19h40 de SP), com grupo de concorrência; README com o procedimento de reativação após inatividade.
- Banco Neon com URL direta (sem pooler) e `sslmode=require` (D20).
- **Sua parte:** criar o banco no Neon, cadastrar `DATABASE_URL` e `ANTHROPIC_API_KEY` nos Secrets e definir o limite mensal de gasto no console da Anthropic.
- **Aceite:** uma execução manual completa ponta a ponta com aviso real no seu Telegram.

### F1-11 — Primeira semana real
- O radar roda sozinho por 7 dias.
- **Sua parte:** a busca manual semanal (~15 min) e anotar falsos positivos e eventos perdidos.
- **Aceite:** lista de ajustes priorizada; prompt v2 se o recall ficar abaixo da referência.
