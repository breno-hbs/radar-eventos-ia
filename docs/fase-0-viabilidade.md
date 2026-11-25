# Fase 0 — Viabilidade das fontes

Executada em 2026-11-25. O script e o resultado bruto estão em `diagnostico/`, guardados como histórico. É código descartável: nada dali é reaproveitado no radar sem reescrita e testes.

## Resumo

| Fonte | Acesso | Campos úteis | Situação |
|---|---|---|---|
| Luma (calendários) | iCal oficial, sem login | título, início/fim, local, GEO, descrição com link canônico, UID `evt-…`, status | **Fase 1** |
| Luma (cidade de SP) | iCal oficial (`entity=discover`) | idem; só 12 eventos "populares" | **Fase 1**, rede complementar |
| Luma (página do evento) | HTML com schema.org Event | `offers` (preço), modalidade | Enriquecimento de preço, sob condições (D4) |
| Meetup | iCal do grupo, sem login (200, `text/calendar`) | título, início/fim, descrição, URL | **Fase 2**, após leitura dos termos |
| AI Tinkerers SP | iCal em `/api/`, bloqueado no robots.txt | — | Busca manual (D5) |
| Sympla | robots.txt 503; página sem JSON-LD | — | Fora do MVP |
| Eventbrite | busca pública desligada desde 2019 | — | Fora do MVP |

## Achados que afetam o desenho
- **A API oficial não serve:** Luma exige Luma Plus e cada chave só acessa o próprio calendário; Meetup exige Meetup Pro.
- **Calendário global:** o do The AI Collective tem 300 eventos, 58 nos próximos 90 dias no mundo todo. O filtro geográfico vem antes do LLM (D6).
- **Ruído no feed da cidade:** dos 12 eventos do feed de SP, 3 são de IA; os calendários listados na página de SP são na maioria alheios a IA (cerâmica, encontro de carros). A lista de organizadores precisa ser curada à mão.
- **Duplicação entre grupos:** os grupos AIDev SP e Enterprise AI SP (Meetup) publicam os mesmos 3 webinars; o mesmo evento aparece em várias fontes. A deduplicação é necessária.
- **Palavra-chave não basta:** "Vector Search na Prática" e "Grok Bot Workshop" não foram reconhecidos como IA pela contagem por palavra-chave; justificam o classificador com LLM (casos de desenvolvimento, D7).
- **Moeda inconsistente:** a Luma informa `priceCurrency: "usd"` em eventos gratuitos no Brasil (D4).

## Lista inicial de organizadores

Os IDs da Luma vieram das páginas públicas dos eventos. Todos os seis feeds foram confirmados na captura do F1-02 (execução #6, 2026-11-25): Codex e Vibe Space incluídos.

| Organizador | Fonte | ID / endereço |
|---|---|---|
| The AI Collective (global, filtrar SP) | Luma | `cal-E74MDlDKBaeAwXK` |
| Codex - São Paulo | Luma | `cal-sAAnGigXnTQWpw6` |
| Vibe Space (meetups Cognition/Devin) | Luma | `cal-s9CExmUfS7kMu50` |
| WoMakersCode | Luma | `cal-K9IcbN14WTbkzOs` |
| MUG São Paulo (MongoDB) | Luma | `cal-HtbK5aYvR5B2WAu` |
| What's Happening in São Paulo (cidade) | Luma | `discover` / `discplace-AQZnCu9wl4LmOIp` |
| AIDev SP (AICamp) | Meetup (Fase 2) | `aidev-saopaulo` |
| Enterprise AI São Paulo | Meetup (Fase 2) | `enterprise-ai-sao-paulo` |
| AI Tinkerers SP | busca manual | sao-paulo.aitinkerers.org |
| Mulheres em IA (MIA) | busca manual (Sympla) | — |

## Captura do F1-02 (execução #6, 2026-11-25)

Resultado em `tests/fixtures/luma/captura.json`. Checagem de dados pessoais por evento (D9).

| Feed | Eventos | Aprovados | Descartados | Motivo do descarte |
|---|---|---|---|---|
| ai-collective | 298 | 297 | 1 | `arroba` (menção colada que a redação não reconhece) |
| vibe-space | 231 | 230 | 1 | `arroba_isolada_dominio` (`@` seguido de algo com formato de domínio) |
| codex-sp | 1 | 1 | 0 | — |
| womakerscode | 9 | 9 | 0 | — |
| mug-sp | 1 | 1 | 0 | — |
| cidade-sp | 12 | 12 | 0 | — |

- Nenhum feed bloqueado pelo robots.txt nem com falha de rede.
- 1 página de evento salva (`pagina_1.html`, evento de 2023 em San Francisco, gratuito); a segunda página escolhida não foi salva.
- Revisão humana: nenhum telefone nem e-mail no texto livre (o único número longo encontrado é parte de um link); restam nomes de anfitriões em `ORGANIZER;CN` e "Hosted by", aceitos pela D9.
