# Registro de decisões

Formato curto: contexto, decisão, motivo. Decisões novas entram no fim, com data.

---

## D1 — Fontes por organizador curado, não varredura de plataforma (2026-11-25)
**Contexto:** nenhuma plataforma (Luma, Meetup, Sympla, Eventbrite) tem API oficial para buscar eventos de terceiros; as APIs servem só ao próprio organizador.
**Decisão:** seguir uma lista curada de organizadores de IA, mais o feed da cidade de SP da Luma como rede complementar. A busca manual semanal mede a cobertura externa e descobre organizadores novos.
**Motivo:** menos requisições, menos fragilidade, adequado a um usuário só.

## D2 — Ordem das fontes (2026-11-25)
- **Fase 1:** Luma via iCal (calendários curados + feed da cidade de SP).
- **Fase 2:** Meetup via iCal, **somente depois** de ler os termos de uso atuais (página não abriu durante a Fase 0).
- **Fora do MVP:** Sympla (robots.txt respondeu 503, página sem dados estruturados) e Eventbrite (busca pública desligada em 2019).

## D3 — Luma: iCal como interface principal (2026-11-25)
**Contexto:** a Luma documenta a assinatura iCal de calendários e de cidades (help.luma.com/p/ical-syncing). Os termos (luma.com/terms) exigem acesso pelas "interfaces publicamente suportadas" e não tratam de extração automatizada de páginas.
**Decisão:** o iCal é a fonte de dados. Ler HTML da Luma está fora, exceto o enriquecimento de preço da D4.

## D4 — Luma: link e preço do evento (2026-11-25)
**Achado (diagnóstico, rodada 2):** o campo `DESCRIPTION` de cada evento do iCal traz o link canônico da página (`https://luma.com/<slug>`), e o `UID` traz o ID estável (`evt-…@events.lu.ma`). Eventos com endereço oculto têm `LOCATION = https://luma.com/event/evt-…`.
**Decisão:**
- **Link de inscrição:** extraído do próprio feed (`DESCRIPTION`). Sem requisição extra.
- **ID do evento:** o `evt-…` do `UID`, usado para cache e deduplicação.
- **Preço:** enriquecimento pela página do evento (dados estruturados schema.org, campo `offers`), com as condições:
  1. só eventos **novos** que passaram pelos filtros geográfico e temático;
  2. limite de páginas por execução (configurável; padrão 10);
  3. cache por ID do evento: cada página é lida uma vez, e de novo só se o conteúdo do feed mudar;
  4. nenhum contorno de bloqueio: qualquer resposta diferente de 200, ou página sem `offers`, gera "preço desconhecido" e não é repetida na mesma execução;
  5. sem evidência suficiente, o estado é **preço desconhecido**.
- **Moeda:** o diagnóstico mostrou `price: 0` com `priceCurrency: "usd"` em eventos brasileiros, então a moeda não é confiável. `price = 0` com uma única oferta vira "gratuito confirmado"; valor maior que zero vira "pago com valor" só se a moeda for BRL, senão "pago sem valor conhecido"; mais de uma oferta com valores diferentes vira "preço variável".
- **Plano B:** se o enriquecimento falhar de forma sistemática (bloqueio, mudança de página), o pipeline segue com "preço desconhecido", sem travar o resto.

## D5 — AI Tinkerers SP fora da coleta automática (2026-11-25)
**Contexto:** o site oferece um link iCal (`aitinkerers.org/api/public/.../calendar.ics`), mas o robots.txt do domínio tem `Disallow: /api/`.
**Decisão:** não usar o feed. O AI Tinkerers SP entra na busca manual semanal.
**Motivo:** o projeto é público e de portfólio; respeitar o robots.txt à risca é a posição mais defensável.

## D6 — Filtro geográfico sem descarte por falta de coordenadas (2026-11-25)
**Contexto:** o calendário do The AI Collective é global (58 eventos em 90 dias, poucos em SP); ~13% dos eventos vêm sem `GEO`.
**Decisão:** o filtro, aplicado antes do LLM, usa nesta ordem: coordenadas (`GEO`) dentro da Grande SP; texto de `LOCATION` (cidade, bairro, CEP de SP); modalidade online. Evento sem coordenada e sem localização textual conclusiva **não é descartado**: segue para o classificador, que decide com base na descrição.

## D7 — Separação dos conjuntos de avaliação (2026-11-25)
**Decisão:** os casos "MUG São Paulo — Vector Search na Prática" e "Grok Bot Workshop", achados na Fase 0, serão usados no desenvolvimento do prompt e por isso ficam **só no conjunto de desenvolvimento**, nunca no conjunto de teste independente.
