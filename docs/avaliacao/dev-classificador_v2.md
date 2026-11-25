# Avaliação do classificador — conjunto dev

- Conjunto: dev (10 eventos)
- Prompt: classificador_v2
- Modelo: claude-haiku-5-5
- Versão do schema: 1
- Data: 2026-11-25
- Custo estimado: US$ 0.005053

Positivo = evento de IA. Erro de saída do LLM conta como "não IA" e também na linha de erros; erro de API interrompe a avaliação (D14). O recall é a métrica prioritária.

## Métricas

| Métrica | Valor |
|---|---|
| Precisão | 1.000 |
| Recall | 0.700 |
| F1 | 0.824 |
| Verdadeiros positivos | 7 |
| Falsos positivos | 0 |
| Falsos negativos | 3 |
| Verdadeiros negativos | 0 |
| Erros de saída do LLM | 3 |
| Acerto de idioma (classificados) | 0.857 |

## Matriz de idioma

Linhas: idioma rotulado; colunas: idioma obtido (só eventos classificados).

| Rotulado \ obtido | desconhecido | en |
|---|---|---|
| desconhecido | 6 | 1 |

## Divergências

| Evento | Título | Campo | Esperado | Obtido |
|---|---|---|---|---|
| evt-Bx1NvyWPMwbMXdJ | Cognition Merge: São Paulo | erro | IA | saida_localizacao_indevida |
| evt-adJgGwc0E2oQzMB | IA na Prática | erro | IA | saida_schema_invalido |
| evt-14lTTiq5UPNPpai | Grok Bot Workshop São Paulo | erro | IA | saida_schema_invalido |
| evt-w5kL9z7mEreKiYc | From AI Hype to Actual Impact (w/ The AI Collective) | idioma | desconhecido | en |
