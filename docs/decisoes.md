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

## D8 — Configuração só por variáveis de ambiente (2026-11-25)
**Decisão:** `radar.config.Configuracao` (Pydantic Settings) lê apenas o ambiente, sem prefixo e sem carregar `.env` automaticamente. Segredos (`DATABASE_URL`, `ANTHROPIC_API_KEY`, `TELEGRAM_TOKEN`) são `SecretStr`; variável exportada vazia equivale a não configurada. `TELEGRAM_CHAT_ID` é dado pessoal: fica fora do `repr` para não aparecer em log. `FUSO_HORARIO` precisa ser um fuso IANA válido (padrão America/Sao_Paulo); `LIMITE_CHAMADAS_LLM_POR_EXECUCAO` ≥ 1 (padrão 50); `LIMITE_PAGINAS_ENRIQUECIMENTO_POR_EXECUCAO` ≥ 0 (padrão 10; 0 desliga o enriquecimento).
**Motivo:** no CI os valores vêm do GitHub Secrets; não ler arquivo evita carregar segredos por acidente e mantém os testes isolados.

## D9 — Fixtures públicas: dados pessoais minimizados (2026-11-25)
**Contexto:** o F1-02 versiona feeds iCal e páginas reais da Luma num repositório público; esses arquivos trazem e-mails, nomes, telefones e fotos de organizadores, palestrantes e participantes.
**Decisão:**
- **Páginas de evento:** reduzidas por **lista de permissão** ao primeiro bloco JSON-LD cujo `@type` termina em `Event`; todo o resto do HTML é descartado. Campos mantidos:
  - Event: `@context`, `@type`, `name`, `url`, `startDate`, `endDate`, `eventStatus`, `eventAttendanceMode`, `location`, `offers`;
  - `location` (Place): `@type`, `name`, `address` (ou o local em texto);
  - `address` (PostalAddress): `@type`, `streetAddress`, `addressLocality`, `addressRegion`, `postalCode`, `addressCountry` (ou o endereço em texto);
  - cada oferta de `offers` (objeto ou lista): `@type`, `name`, `price`, `priceCurrency`, `availability`, `url`.
  Qualquer outra chave, em qualquer nível, some. Página sem bloco de Event não é salva nem listada.
- **Redação de e-mails:** todo endereço vira `redigido@exemplo.invalid`; a forma codificada `…%40…` vira `redigido%40exemplo.invalid`. Escapes do iCal antes do endereço (`\n`, `\N`, `\t`, `\\`, `\;`, `\,`) são preservados.
- **Checagem bruta independente:** depois da redação, cada arquivo passa por uma checagem que não depende da regex de e-mail: qualquer `@`, `%40`, `%2540`, `&#64;`, `&#x40;` (sem diferenciar maiúsculas) ou `＠` faz o **arquivo inteiro ser descartado** (não é gravado e entra em `descartados_por_dados_pessoais` no `captura.json`). Isso inclui menções `@perfil`, de propósito: a regra é conservadora. Se ela zerar as fixtures reais, será revista com o autor.
- **Exceções da checagem:** o UID `evt-<id>@events.lu.ma` da Luma (ID estável do evento, D4, necessário no F1-03), os marcadores `redigido@exemplo.invalid` e `redigido%40exemplo.invalid` (desde que não estejam colados a restos de um endereço, exceto depois de um escape do iCal) e as chaves JSON-LD `"@context"` e `"@type"`. Qualquer outro endereço em `events.lu.ma` ou `lu.ma` é redigido e checado como os demais.
- **Fora do código:** nomes de organizadores que vêm no iCal (`ORGANIZER;CN=`) são mantidos por serem informação pública do evento. Telefones, nomes e outros dados de terceiros em texto livre não são tratados por código — no iCal (`DESCRIPTION`, `LOCATION`, `SUMMARY`) e nos campos de texto mantidos nas páginas (`name`, `location.name`, `address`, `offers.name`, `offers.url`). Também escapam da checagem bruta formas raras de arroba (`﹫`, `&#0064;`, `&commat;`, `[at]`). Tudo isso é verificado na revisão humana (passo 4 do F1-02) antes do PR de dados.
- **robots.txt:** lido por host antes da primeira requisição a ele, com o User-agent `radar-eventos-ia`. Falha segura: 404/410 libera; qualquer outra resposta diferente de 200 (3xx, 401, 403, 5xx) ou erro de rede bloqueia o host. URLs bloqueadas ficam em `bloqueadas_robots`.
- **Rede:** só `https` em `api.lu.ma` e `luma.com`; nenhum redirecionamento é seguido (3xx conta como falha). Captura única e manual (`workflow_dispatch` em `capturar-fixtures.yml`), pausa de 3 s antes de cada requisição (inclusive robots.txt), no máximo 2 páginas de evento e nenhuma repetição de requisição que falhou.
- **Publicação:** o workflow roda a mesma checagem bruta (`--verificar`) sobre `tests/fixtures/luma/` antes do commit; se algo aparecer, o job falha sem publicar e o log mostra só nomes de arquivo. O resultado vai para o branch `fase-1/f1-02-fixtures-dados` por push sem `--force`, nunca para a `main`; para refazer a captura, apagar o branch de dados antes.
**Motivo:** fixtures reais tornam os testes do coletor fiéis à fonte sem expor dados pessoais de terceiros nem sobrecarregar a Luma; a lista de permissão e a checagem bruta falham para o lado de não publicar.
