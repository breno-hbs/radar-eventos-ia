# Avaliação do classificador — conjunto dev

- Conjunto: dev (10 eventos)
- Prompt: classificador_v3
- Modelo: claude-haiku-5-5
- Versão do schema: 1
- Data: 2026-11-25
- Custo estimado: US$ 0.005100

Positivo = evento de IA. Erro de saída do LLM conta como "não IA" e também na linha de erros; erro de API interrompe a avaliação (D14). O recall é a métrica prioritária.

## Métricas

| Métrica | Valor |
|---|---|
| Precisão | 1.000 |
| Recall | 1.000 |
| F1 | 1.000 |
| Verdadeiros positivos | 10 |
| Falsos positivos | 0 |
| Falsos negativos | 0 |
| Verdadeiros negativos | 0 |
| Erros de saída do LLM | 0 |
| Acerto de idioma (classificados) | 1.000 |

## Matriz de idioma

Linhas: idioma rotulado; colunas: idioma obtido (só eventos classificados).

| Rotulado \ obtido | desconhecido | pt |
|---|---|---|
| desconhecido | 9 | 0 |
| pt | 0 | 1 |

## Divergências

Nenhuma divergência.
