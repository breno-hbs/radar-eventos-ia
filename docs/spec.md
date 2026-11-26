# Especificação — Radar de Eventos de IA (SP)

Versão 2.10 (2026-11-25). Aprovada. Decisões detalhadas em [decisoes.md](decisoes.md); viabilidade em [fase-0-viabilidade.md](fase-0-viabilidade.md).

## 1. Problema e objetivo
Eventos de IA em São Paulo ficam espalhados por várias plataformas, sem filtro de IA, sem deduplicação e sem cruzamento com a agenda. O radar coleta, classifica, cruza com a agenda e avisa no Telegram.

- **Usuário:** um só (o autor). Repositório público, também usado como portfólio.
- **Métrica de sucesso:** não perder nenhum evento de IA gratuito que caiba na agenda.
- **Fora do MVP:** página pública, outros usuários, compra de ingresso, Sympla, Eventbrite.

## 2. Escopo dos eventos
- **Tipos:** meetups, palestras, hackathons, conferências e workshops sobre IA.
- **Formato:** presenciais na Grande SP e online. Gratuitos e pagos.
- **Horizonte:** 90 dias para conferências e hackathons; 30 dias para todos os demais tipos, inclusive tipo desconhecido (`outro`), independentemente de preço e modalidade (D13). Antes do LLM vale 90 dias para todos; o corte por tipo vem depois da classificação (D11, D13).
- **Idioma:**

| Situação | Ação |
|---|---|
| Português, bilíngue com português ou com tradução para português | Aviso |
| Idioma não informado (`desconhecido`) | Aviso com etiqueta "idioma não confirmado" |
| Estrangeiro confirmado (`en` ou `outro`), sem tradução, presencial | Resumo semanal, etiqueta com o idioma |
| Estrangeiro confirmado, sem tradução, exclusivamente online | Descartado |

"Exclusivamente online" = local efetivo online com modalidade online ou desconhecida, ou local indefinido com modalidade online declarada; na dúvida, vai para o resumo semanal. A decisão de destino completa, com a ordem das regras, está na D20.

Página em inglês não implica evento em inglês; o classificador procura sinais explícitos e guarda o trecho de evidência. Estrangeiro confirmado exige trecho explícito; sem ele o idioma é `desconhecido` e o evento é avisado (D13).

## 3. Fontes
- **Fase 1:** Luma via iCal: calendários de organizadores curados + feed da cidade de SP (D1, D2, D3). Calendários de comunidades de IA (AI Collective, Vibe Space, Codex SP, WoMakersCode) são marcados com `comunidade_ia` em `config/fontes.yaml` (D15).
- **Fase 2:** Meetup via iCal, condicionado à leitura dos termos de uso.
- **Busca manual semanal (~15 min):** mede o que o radar perdeu, descobre organizadores novos e cobre o AI Tinkerers SP (D5).

## 4. Fluxo
1. **Coleta** do iCal, um coletor por fonte, isolados.
2. **Extração direta:** título, início/fim, local, coordenadas, status, ID (`UID`) e link de inscrição (`DESCRIPTION`, D4). Fuso de São Paulo.
3. **Filtros, antes do LLM:**
   - **Geográfico:** coordenadas na caixa da Grande SP → local textual (UF, país estrangeiro, CEP 01000-000 a 09999-999, "São Paulo") → modalidade online (plataforma de transmissão na URL do local ou "online" no texto). Só é descartado o evento com evidência de estar fora da Grande SP; sem coordenada e sem local conclusivo, segue para o classificador como `indefinida` e nunca é descartado só por isso (D6, D11).
   - **Horizonte:** descarta o que já terminou e o que começa depois de 90 dias; o corte de 30 dias por tipo (§2) é aplicado depois da classificação (D11).
4. **Deduplicação** entre fontes.
5. **Classificação com LLM** (só eventos novos ou alterados; detalhes na D13 e na D16):
   - prompt atual `classificador_v3` (v1 e v2 mantidos como histórico): título em inglês não é evidência de idioma (D17); termos de IA no título contam (vibe coding, agentes, LLM, ferramentas como Copilot, Cursor, ChatGPT, Claude…), na dúvida o evento é tratado como IA (prioridade de recall), idioma só com declaração explícita e trechos de até 120 caracteres; thinking desligado em toda chamada, para custo e tamanho de resposta previsíveis (D16);
   - responde: é IA, categoria, tópicos, tipo de evento, modalidade, idioma, tradução, estado de preço, localização (só quando o filtro deixou `indefinida`; nos demais casos a resposta do LLM nesse campo é descartada antes da validação — substitui o antigo erro `saida_localizacao_indevida`, D17), resumo de 1 linha e trecho de evidência;
   - todo campo conhecido traz um trecho copiado literalmente do texto do evento, conferido pelo código; sem evidência, "desconhecido". Trecho literal não garante interpretação correta: a avaliação do §8 continua;
   - saída JSON estruturada, validada com Pydantic e contra o texto do evento; conteúdo externo vai delimitado como dado não confiável, sem link de inscrição, coordenadas nem identificadores, e toda URL dos textos reduzida ao host (o LLM não tem ferramentas);
   - chave de classificação = hash do conteúdo relevante normalizado + versão do prompt + modelo + versão do schema;
   - registro por chamada (inclusive erros): versão do prompt, modelo, tokens, latência e custo estimado; por execução, até 50 chamadas, 200 mil tokens de entrada e 40 mil de saída; uma chamada por evento, sem repetição;
   - a classificação grava a saída do LLM sem alteração e nunca o texto bruto da resposta. A regra de comunidade de IA (evento de calendário marcado `comunidade_ia` conta como IA, só com saída válida) não é gravada: é aplicada na decisão do aviso (F1-10), a partir dos calendários do evento e do `fontes.yaml` vigente, e na avaliação (D15);
   - saída inválida vira erro registrado, nunca evento aprovado; após 3 erros de saída para a mesma chave o evento fica em "falha de classificação" até a chave mudar, e entra no resumo semanal como "Não classificado — confira manualmente" (com aviso excepcional se acontecer antes do próximo resumo).
6. **Enriquecimento de preço (Luma):** página `https://luma.com/<slug>` do evento, lida do JSON-LD (`offers`) só para eventos classificados como IA (ou de comunidade de IA) e sem enriquecimento para o hash atual; até 10 eventos por execução, robots.txt respeitado (ilegível → nada é lido), sem redirecionamento nem repetição: todo resultado, inclusive erro, é gravado e só se repete quando o conteúdo mudar. Na decisão do aviso, a página conclusiva prevalece sobre o LLM, exceto página grátis com LLM pago (vale o LLM) (D4, D18).
7. **Estados de preço:** gratuito confirmado, pago com valor, pago sem valor conhecido, preço variável, preço desconhecido.
8. **Estados do evento:** ativo, alterado, cancelado (só quando a fonte diz), encerrado, não encontrado (some por N execuções seguidas com todos os seus calendários lidos com sucesso; padrão 3; não gera aviso). Só eventos aprovados nos filtros entram no banco; regras de hash e transições na D12.
9. **Agenda (Fase 3):** Google Calendar só com escopo FreeBusy; margem de deslocamento configurável para presenciais.
10. **Avisos no Telegram** (texto puro, sem prévia de link, mensagens até 4096 unidades UTF-16; D19):
    - na próxima execução: evento de IA que cabe na agenda, com estado de preço e link;
    - resumo semanal: conflitos de agenda, presenciais em idioma estrangeiro, pendentes, eventos não classificados (D13);
    - Fase 3: botões "Vou" e "Não tenho interesse" (via webhook serverless), lembrete na véspera (confere a agenda de novo) e aviso de mudança para eventos com "Vou".

## 5. Agendamento (GitHub Actions)

| Tarefa | Quando |
|---|---|
| Coleta, classificação e avisos | 7h40, 12h40 e 19h40 |
| Lembrete da véspera (Fase 3) | 17h50 |
| Resumo semanal | Fase 1: na 1ª execução de segunda-feira (F1-10, D19); Fase 3: segunda 7h50 |

- Aviso "na próxima execução", não "na hora".
- Grupo de concorrência: nunca duas execuções simultâneas.
- Alerta de inatividade por volta do dia 50 (o GitHub desliga workflows agendados após 60 dias sem atividade em repositório público) e procedimento de reativação no README. Sem commits artificiais.

## 6. Confiabilidade e observabilidade
- **Falha de extração** (rede, formato inesperado, campos obrigatórios ausentes): alerta imediato.
- **Evento com campo obrigatório ausente** (UID, título, início) ou início sem fuso ou malformado: descartado sozinho, sem derrubar o feed; entra em `eventos_invalidos` e conta como falha de extração para o alerta. Feed em que todos os eventos são inválidos vira falha de formato (D10).
- **Zero eventos com leitura normal:** não é falha; aviso só após várias execuções vazias numa fonte que costuma ter eventos.
- **Tabela de execuções:** fonte, horário, status, quantidade de eventos, erro.
- **Notificações:** tabela com chave única (evento, tipo); grava "enviando" (commit) → envia → grava "enviado" (commit logo após cada mensagem). "Enviando" que sobrou de uma execução anterior é reenviado na seguinte (o F1-10 pede as pendentes em toda execução); os commits do envio usam uma sessão própria, nunca a do chamador. Entrega "pelo menos uma vez"; duplicata rara aceita, perda nunca (D19).

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
- Fase 1: 40 eventos sorteados das fixtures fora do repositório (planilha; semente só como referência), teste 30 e dev 10, rotulados pelo autor; erro de saída do LLM conta como "não IA" e à parte; erro de API interrompe a avaliação sem relatório; F1 = 0 quando o recall é 0; acerto de idioma entre os classificados. Roda só pelo workflow manual com secret e limite de gastos; relatório em `docs/avaliacao/` (D14).
- Limitação conhecida: nos feeds de calendário a descrição não vem no iCal, então a classificação desses eventos depende do título (D14).
- Após a 1ª avaliação (recall 0,41): comunidades de IA contam como IA também na avaliação; rótulos de idioma só onde o texto declara (o teste atual não mede idioma de forma útil); diagnóstico opcional da resposta bruta dos erros de saída, só como artifact do Actions com retenção de 7 dias; o conjunto de teste já foi visto, então os próximos números são comparação, não medida independente (D15).
- "Não tenho interesse" alimenta uma fila de revisão manual, nunca o dataset automaticamente.
- Metas (recall ≥ 95%, precision ≥ 90%, F1 ≥ 90%) são referência até ~100 casos.

## 9. Validação do produto (Fase 4, 4 semanas)
- Automáticas: eventos encontrados, duplicados, tempo entre descoberta e aviso, "Vou", "Não tenho interesse", custo por classificação, disponibilidade dos coletores.
- Manual: eventos achados só na busca manual (referência, não garantia de cobertura).
- Relatório final gerado pelo sistema, publicado no README.

## 10. Stack
Python (monólito modular), httpx, icalendar, Pydantic, SQLAlchemy, Alembic, PostgreSQL gratuito hospedado (Neon, URL direta com `sslmode=require`, D20), API do Claude (modelo mais barato), httpx para a Bot API do Telegram na Fase 1 (sem python-telegram-bot; mensagens até 4096 unidades UTF-16, D19), API do Google Calendar (FreeBusy), pytest com feeds e páginas de exemplo salvos, GitHub Actions. Fora: LangChain, LangGraph, Redis, banco vetorial, Kubernetes.

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
- Descrição dos eventos de feeds de calendário que vêm sem `DESCRIPTION` no iCal (D14).
