# Especificação — Radar de Eventos de IA (SP)

Versão 2.2 (2026-11-25). Aprovada. Decisões detalhadas em [decisoes.md](decisoes.md); viabilidade em [fase-0-viabilidade.md](fase-0-viabilidade.md).

## 1. Problema e objetivo
Eventos de IA em São Paulo ficam espalhados por várias plataformas, sem filtro de IA, sem deduplicação e sem cruzamento com a agenda. O radar coleta, classifica, cruza com a agenda e avisa no Telegram.

- **Usuário:** um só (o autor). Repositório público, também usado como portfólio.
- **Métrica de sucesso:** não perder nenhum evento de IA gratuito que caiba na agenda.
- **Fora do MVP:** página pública, outros usuários, compra de ingresso, Sympla, Eventbrite.

## 2. Escopo dos eventos
- **Tipos:** meetups, palestras, hackathons, conferências e workshops sobre IA.
- **Formato:** presenciais na Grande SP e online. Gratuitos e pagos.
- **Horizonte:** 30 dias para meetups, palestras, workshops e online; 90 dias para conferências pagas. Antes do LLM vale 90 dias para todos; o corte de 30 dias vem depois da classificação (D11).
- **Idioma:**

| Situação | Ação |
|---|---|
| Português, bilíngue ou com tradução | Aviso |
| Idioma não informado | Aviso com etiqueta "idioma não confirmado" |
| Presencial só em inglês, sem tradução | Resumo semanal, etiqueta "em inglês" |
| Online só em inglês | Descartado |

Página em inglês não implica evento em inglês; o classificador procura sinais explícitos e guarda o trecho de evidência.

## 3. Fontes
- **Fase 1:** Luma via iCal: calendários de organizadores curados + feed da cidade de SP (D1, D2, D3).
- **Fase 2:** Meetup via iCal, condicionado à leitura dos termos de uso.
- **Busca manual semanal (~15 min):** mede o que o radar perdeu, descobre organizadores novos e cobre o AI Tinkerers SP (D5).

## 4. Fluxo
1. **Coleta** do iCal, um coletor por fonte, isolados.
2. **Extração direta:** título, início/fim, local, coordenadas, status, ID (`UID`) e link de inscrição (`DESCRIPTION`, D4). Fuso de São Paulo.
3. **Filtros, antes do LLM:**
   - **Geográfico:** coordenadas na caixa da Grande SP → local textual (UF, país estrangeiro, CEP 01000-000 a 09999-999, "São Paulo") → modalidade online (plataforma de transmissão na URL do local ou "online" no texto). Só é descartado o evento com evidência de estar fora da Grande SP; sem coordenada e sem local conclusivo, segue para o classificador como `indefinida` e nunca é descartado só por isso (D6, D11).
   - **Horizonte:** descarta o que já terminou e o que começa depois de 90 dias; o corte de 30 dias por tipo (§2) é aplicado depois da classificação (D11).
4. **Deduplicação** entre fontes.
5. **Classificação com LLM** (só eventos novos ou alterados):
   - responde: é IA, categoria, tópicos, idioma, tradução, resumo de 1 linha, trecho de evidência;
   - plano B para modalidade e preço quando faltarem, só com evidência;
   - saída JSON validada com Pydantic; conteúdo externo tratado como dado não confiável (o LLM não tem ferramentas);
   - chave de classificação = hash do conteúdo relevante normalizado + versão do prompt + modelo + versão do schema;
   - registro por chamada: versão do prompt, modelo, tokens, latência, custo; limite de chamadas por execução.
6. **Enriquecimento de preço (Luma):** página do evento, só eventos novos aprovados nos filtros, com limite por execução, cache e sem contorno de bloqueio (D4).
7. **Estados de preço:** gratuito confirmado, pago com valor, pago sem valor conhecido, preço variável, preço desconhecido.
8. **Estados do evento:** ativo, alterado, cancelado (só quando a fonte diz), encerrado, não encontrado (some por N execuções seguidas com todos os seus calendários lidos com sucesso; padrão 3; não gera aviso). Só eventos aprovados nos filtros entram no banco; regras de hash e transições na D12.
9. **Agenda (Fase 3):** Google Calendar só com escopo FreeBusy; margem de deslocamento configurável para presenciais.
10. **Avisos no Telegram:**
    - na próxima execução: evento de IA que cabe na agenda, com estado de preço e link;
    - resumo semanal: conflitos de agenda, presenciais em inglês, pendentes;
    - Fase 3: botões "Vou" e "Não tenho interesse" (via webhook serverless), lembrete na véspera (confere a agenda de novo) e aviso de mudança para eventos com "Vou".

## 5. Agendamento (GitHub Actions)

| Tarefa | Quando |
|---|---|
| Coleta, classificação e avisos | 7h40, 12h40 e 19h40 |
| Lembrete da véspera (Fase 3) | 17h50 |
| Resumo semanal (Fase 3) | Segunda 7h50 |

- Aviso "na próxima execução", não "na hora".
- Grupo de concorrência: nunca duas execuções simultâneas.
- Alerta de inatividade por volta do dia 50 (o GitHub desliga workflows agendados após 60 dias sem atividade em repositório público) e procedimento de reativação no README. Sem commits artificiais.

## 6. Confiabilidade e observabilidade
- **Falha de extração** (rede, formato inesperado, campos obrigatórios ausentes): alerta imediato.
- **Evento com campo obrigatório ausente** (UID, título, início) ou início sem fuso ou malformado: descartado sozinho, sem derrubar o feed; entra em `eventos_invalidos` e conta como falha de extração para o alerta. Feed em que todos os eventos são inválidos vira falha de formato (D10).
- **Zero eventos com leitura normal:** não é falha; aviso só após várias execuções vazias numa fonte que costuma ter eventos.
- **Tabela de execuções:** fonte, horário, status, quantidade de eventos, erro.
- **Notificações:** tabela com chave única (evento, tipo); grava "enviando" → envia → grava "enviado". Entrega "pelo menos uma vez"; duplicata rara aceita.

## 7. Segurança e privacidade
- Bot responde só ao user_id e chat_id do autor.
- Senhas no GitHub Secrets; nunca no código nem nos logs.
- Dados pessoais (agenda, "Vou") só no banco; nunca no repositório.
- Limite mensal de gasto no console da Anthropic e limite de chamadas no código.
- Respeito a robots.txt e termos de uso de cada fonte; nenhum contorno de bloqueio.

## 8. Avaliação do classificador
- Métricas: precision, recall e F1, com prioridade ao recall.
- Dataset inicial: 30 eventos rotulados à mão (positivos, negativos, ambíguos, IA só como marketing, páginas em inglês de eventos em português).
- Conjunto de desenvolvimento separado do conjunto de teste fixo. Casos usados para escrever o prompt ficam só no desenvolvimento (D7).
- "Não tenho interesse" alimenta uma fila de revisão manual, nunca o dataset automaticamente.
- Metas (recall ≥ 95%, precision ≥ 90%, F1 ≥ 90%) são referência até ~100 casos.

## 9. Validação do produto (Fase 4, 4 semanas)
- Automáticas: eventos encontrados, duplicados, tempo entre descoberta e aviso, "Vou", "Não tenho interesse", custo por classificação, disponibilidade dos coletores.
- Manual: eventos achados só na busca manual (referência, não garantia de cobertura).
- Relatório final gerado pelo sistema, publicado no README.

## 10. Stack
Python (monólito modular), httpx, icalendar, Pydantic, SQLAlchemy, Alembic, BeautifulSoup (só para o enriquecimento), PostgreSQL gratuito hospedado (Supabase ou Neon), API do Claude (modelo mais barato), python-telegram-bot, API do Google Calendar (FreeBusy), pytest com feeds e páginas de exemplo salvos, GitHub Actions. Fora: LangChain, LangGraph, Redis, banco vetorial, Kubernetes.

## 11. Desenvolvimento
Claude Code com CLAUDE.md de regras e subagentes: planejador, implementador (um módulo por vez), testador (testes antes do código) e revisor (contexto limpo). Código em branches; o merge é sempre do autor.

## 12. Fases

| Fase | Entrega |
|---|---|
| 0 | ✅ Viabilidade das fontes ([relatório](fase-0-viabilidade.md)) |
| 1 | Fluxo completo com a Luma: coleta, filtro geográfico, classificação, enriquecimento de preço, banco, avisos, alertas de falha, dataset de 30 casos |
| 2 | Meetup (após termos) e deduplicação |
| 3 | Google Calendar, "Vou", "Não tenho interesse", webhook, lembrete da véspera, resumo semanal, avisos de mudança |
| 4 | Validação real por 4 semanas e relatório |
| Depois | Comandos no bot e página pública |

## 13. Pendências
- Termos de uso do Meetup (bloqueia só a Fase 2).
- Método de autorização do Google Calendar (Fase 3).
