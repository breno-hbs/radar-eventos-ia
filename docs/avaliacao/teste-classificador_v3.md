# Avaliação do classificador — conjunto teste

- Conjunto: teste (30 eventos)
- Prompt: classificador_v3
- Modelo: claude-haiku-5-5
- Versão do schema: 1
- Data: 2026-11-25
- Custo estimado: US$ 0.015702

Positivo = evento de IA. Erro de saída do LLM conta como "não IA" e também na linha de erros; erro de API interrompe a avaliação (D14). O recall é a métrica prioritária.

## Métricas

| Métrica | Valor |
|---|---|
| Precisão | 0.909 |
| Recall | 0.909 |
| F1 | 0.909 |
| Verdadeiros positivos | 20 |
| Falsos positivos | 2 |
| Falsos negativos | 2 |
| Verdadeiros negativos | 6 |
| Erros de saída do LLM | 1 |
| Acerto de idioma (classificados) | 0.966 |

## Matriz de idioma

Linhas: idioma rotulado; colunas: idioma obtido (só eventos classificados).

| Rotulado \ obtido | desconhecido | en |
|---|---|---|
| desconhecido | 28 | 1 |

## Divergências

| Evento | Título | Campo | Esperado | Obtido |
|---|---|---|---|---|
| evt-zw9QXKGtbTeDlev | TECH TALKS - WoMakersCode | e_ia | não IA | IA |
| evt-HYVwUJMomJQ1WpH | Vibe Space Interview with Victoria D'Hondt from Xpertise Lab Advisory | e_ia | não IA | IA |
| evt-oRMGhmlyDwjJqfX | Workshop: AI Agents on Ethereum | idioma | desconhecido | en |
| evt-XlL1yRpCtA1uGTs | Hack for Humanity: São Paulo | erro | IA | saida_schema_invalido:categoria |
| evt-oJnGMDJ7aiuvsu9 | Revenue Summit 2026 | e_ia | IA | não IA |
