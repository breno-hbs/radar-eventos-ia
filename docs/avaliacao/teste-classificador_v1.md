# Avaliação do classificador — conjunto teste

- Conjunto: teste (30 eventos)
- Prompt: classificador_v1
- Modelo: claude-haiku-5-5
- Versão do schema: 1
- Data: 2026-11-25
- Custo estimado: US$ 0.021821

Positivo = evento de IA. Erro de saída do LLM conta como "não IA" e também na linha de erros; erro de API interrompe a avaliação (D14). O recall é a métrica prioritária.

## Métricas

| Métrica | Valor |
|---|---|
| Precisão | 1.000 |
| Recall | 0.409 |
| F1 | 0.581 |
| Verdadeiros positivos | 9 |
| Falsos positivos | 0 |
| Falsos negativos | 13 |
| Verdadeiros negativos | 8 |
| Erros de saída do LLM | 5 |
| Acerto de idioma (classificados) | 0.360 |

## Matriz de idioma

Linhas: idioma rotulado; colunas: idioma obtido (só eventos classificados).

| Rotulado \ obtido | desconhecido |
|---|---|
| desconhecido | 9 |
| en | 2 |
| pt | 14 |

## Divergências

| Evento | Título | Campo | Esperado | Obtido |
|---|---|---|---|---|
| evt-YDkpPkYuPBXIpCH | 🧠 GenAI Collective 🧠 Virtual Friendsgiving! 🦃 | e_ia | IA | não IA |
| evt-YDkpPkYuPBXIpCH | 🧠 GenAI Collective 🧠 Virtual Friendsgiving! 🦃 | idioma | en | desconhecido |
| evt-qY134Rs7lRyEBlS | Vibe Coding 101 | e_ia | IA | não IA |
| evt-nXTV49aVRIwo3Kn | Vibe Tuesdays - Office Hours | e_ia | IA | não IA |
| evt-Rop70jAmzTk3YDq | Vibe Thursdays - Vibing Go-To-Market Strategies | e_ia | IA | não IA |
| evt-xzYFToYpUobBgW2 | Vibe Thursdays - From Idea to Pitch | e_ia | IA | não IA |
| evt-F6PDhMxyhTBCa4o | XDC Vibe Coding - Free Bootcamp - Live Kick-off & AMA (Win $500) | erro | IA | saida_truncada |
| evt-1dXutwGNplUMkSB | Workshop Vibe Coding: Fundamentos de IA | idioma | pt | desconhecido |
| evt-zw9QXKGtbTeDlev | TECH TALKS - WoMakersCode | idioma | pt | desconhecido |
| evt-nDmzxXm5CL4g9nm | Vibe Coding 101 with V0 | erro | IA | saida_truncada |
| evt-PPnuy0tzwk8MVct | Codex Community Meetup – São Paulo [Presencial] | idioma | pt | desconhecido |
| evt-ChBWTnOUZlCwLHr | GitHub Copilot Dev Days São Paulo | erro | IA | saida_truncada |
| evt-IEYn9wcNbifCUEs | DevConverge 2026 SP – O Futuro do Código: Devs + IAs | idioma | pt | desconhecido |
| evt-XlL1yRpCtA1uGTs | Hack for Humanity: São Paulo | e_ia | IA | não IA |
| evt-XlL1yRpCtA1uGTs | Hack for Humanity: São Paulo | idioma | pt | desconhecido |
| evt-RCa3HNqJuXYOLDO | Por Dentro de um Data Center: Tour IBX São Paulo | e_ia | IA | não IA |
| evt-RCa3HNqJuXYOLDO | Por Dentro de um Data Center: Tour IBX São Paulo | idioma | pt | desconhecido |
| evt-oJnGMDJ7aiuvsu9 | Revenue Summit 2026 | e_ia | IA | não IA |
| evt-oJnGMDJ7aiuvsu9 | Revenue Summit 2026 | idioma | pt | desconhecido |
| evt-YEkDcmJq3cWyjK5 | Devin Meetup São Paulo | idioma | pt | desconhecido |
| evt-L6KNoab9Q80j1Mm | Bora Trocar \| Negritude, empreendedorismo e mercado de trabalho | erro | não IA | saida_truncada |
| evt-9kBpb6n7CQen7vt | Oficina - Cerâmica fria | idioma | pt | desconhecido |
| evt-oy5YHy8BtOYbCbp | Brunch com Felipe Nasr | idioma | pt | desconhecido |
| evt-dXFOQDsspqC1038 | ENTRE GALERIAS: Roteiro Cultural no Centro Histórico | idioma | pt | desconhecido |
| evt-EQg3ApxBf4g5FJM | NYC Pitch Night (w/ The AI Collective) | e_ia | IA | não IA |
| evt-EQg3ApxBf4g5FJM | NYC Pitch Night (w/ The AI Collective) | idioma | en | desconhecido |
| evt-xlla7UIGWU7AR4n | Construindo IA em Escala: da Engenharia ao Impacto no Cliente | idioma | pt | desconhecido |
| evt-shVPNGPpyFiZ8kW | All people free | idioma | pt | desconhecido |
| evt-80mojlBlXPSCCYI | Lançamento do Livro Alma Selvagem 2 e Reedição do Alma Selvagem 1 | idioma | pt | desconhecido |
| evt-s9CZALe4jFB4myS | FDE Talks | erro | IA | saida_truncada |
