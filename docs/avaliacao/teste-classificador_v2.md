# Avaliação do classificador — conjunto teste

- Conjunto: teste (30 eventos)
- Prompt: classificador_v2
- Modelo: claude-haiku-5-5
- Versão do schema: 1
- Data: 2026-11-25
- Custo estimado: US$ 0.015700

Positivo = evento de IA. Erro de saída do LLM conta como "não IA" e também na linha de erros; erro de API interrompe a avaliação (D14). O recall é a métrica prioritária.

## Métricas

| Métrica | Valor |
|---|---|
| Precisão | 0.905 |
| Recall | 0.864 |
| F1 | 0.884 |
| Verdadeiros positivos | 19 |
| Falsos positivos | 2 |
| Falsos negativos | 3 |
| Verdadeiros negativos | 6 |
| Erros de saída do LLM | 7 |
| Acerto de idioma (classificados) | 0.957 |

## Matriz de idioma

Linhas: idioma rotulado; colunas: idioma obtido (só eventos classificados).

| Rotulado \ obtido | desconhecido | en |
|---|---|---|
| desconhecido | 22 | 1 |

## Divergências

| Evento | Título | Campo | Esperado | Obtido |
|---|---|---|---|---|
| evt-zw9QXKGtbTeDlev | TECH TALKS - WoMakersCode | e_ia | não IA | IA |
| evt-HYVwUJMomJQ1WpH | Vibe Space Interview with Victoria D'Hondt from Xpertise Lab Advisory | e_ia | não IA | IA |
| evt-oRMGhmlyDwjJqfX | Workshop: AI Agents on Ethereum | idioma | desconhecido | en |
| evt-ChBWTnOUZlCwLHr | GitHub Copilot Dev Days São Paulo | erro | IA | saida_localizacao_indevida |
| evt-XlL1yRpCtA1uGTs | Hack for Humanity: São Paulo | erro | IA | saida_localizacao_indevida |
| evt-oJnGMDJ7aiuvsu9 | Revenue Summit 2026 | erro | IA | saida_localizacao_indevida |
| evt-L6KNoab9Q80j1Mm | Bora Trocar \| Negritude, empreendedorismo e mercado de trabalho | erro | não IA | saida_localizacao_indevida |
| evt-9kBpb6n7CQen7vt | Oficina - Cerâmica fria | erro | não IA | saida_localizacao_indevida |
| evt-shVPNGPpyFiZ8kW | All people free | erro | não IA | saida_localizacao_indevida |
| evt-80mojlBlXPSCCYI | Lançamento do Livro Alma Selvagem 2 e Reedição do Alma Selvagem 1 | erro | não IA | saida_localizacao_indevida |
