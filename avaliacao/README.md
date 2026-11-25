# Conjuntos de avaliação do classificador

Rótulos feitos à mão pelo autor, a partir da planilha de candidatos tirados das fixtures
(`tests/fixtures/luma/`). Regras de amostragem, rótulo e métricas: D7 e D14 em
[`docs/decisoes.md`](../docs/decisoes.md).

- `teste.jsonl`: conjunto de teste fixo (30 eventos). Nunca usado para escrever o prompt.
- `dev.jsonl`: conjunto de desenvolvimento (10 eventos), inclusive os casos usados no prompt
  (Vector Search e Grok Bot, D7).

Os **dois** arquivos precisam existir para avaliar qualquer um deles: a validação da D7 (nenhum
evento nos dois conjuntos; casos do prompt só no dev) lê ambos antes da primeira chamada.

Um evento por linha, em JSON:

```json
{"calendario": "codex-sp", "id_fonte": "evt-…", "e_ia": true, "idioma": "pt"}
```

- `calendario`: slug do calendário; o evento é lido de `tests/fixtures/luma/<calendario>.ics`.
- `id_fonte`: id `evt-…` do evento no feed; não pode se repetir nem aparecer nos dois conjuntos.
- `e_ia`: a verdade sobre o evento (o autor pode abrir o link para decidir).
- `idioma`: `pt`, `en`, `bilingue`, `outro` ou `desconhecido` — só o que o texto **declara**
  sobre o idioma das apresentações; texto escrito em português não basta (D15). Sem menção
  explícita, `desconhecido`.

Eventos de calendários com `comunidade_ia: true` em `config/fontes.yaml` contam como IA
depois do LLM, também na avaliação (D15).

Sem descrições nem dados pessoais aqui: só ids e rótulos. A avaliação chama a API de
verdade e roda só pelo workflow manual **Avaliar classificador** (`.github/workflows/avaliar.yml`):

```bash
python -m radar.avaliacao --conjunto teste --saida docs/avaliacao/teste-classificador_v1.md \
  --diagnostico diagnostico-avaliacao   # opcional: resposta bruta dos erros de saída (D15)
```
