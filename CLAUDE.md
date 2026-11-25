# Radar de Eventos de IA — regras do projeto

Leia antes de qualquer tarefa: [docs/spec.md](docs/spec.md), [docs/decisoes.md](docs/decisoes.md) e o backlog da fase atual ([docs/backlog-fase-1.md](docs/backlog-fase-1.md)).

## Como trabalhar
- **Um ticket por vez**, num branch `fase-N/<id>-<nome-curto>`. Nunca commitar direto na `main`; o merge é sempre do autor.
- **Ciclo por ticket:** subagente `planejador` (se o ticket for ambíguo) → `testador` escreve os testes que falham → `implementador` faz passar → `revisor` revisa em contexto limpo → corrigir → PR.
- **Pronto** = `scripts/verificar.sh` verde + critérios de aceite do ticket cumpridos.
- Ticket que passar de ~500 linhas de lógica deve ser quebrado antes de continuar.
- Limpar o contexto entre tickets.
- Erro que se repete vira regra neste arquivo; tarefa mecânica repetida vira script em `scripts/`.

## Código
- Python 3.12, monólito modular em `src/radar/`. Tipagem em tudo; modelos de dados com Pydantic.
- Nomes de domínio em português (evento, fonte, classificação); termos técnicos consagrados podem ficar em inglês.
- Horários sempre com fuso; exibição em America/Sao_Paulo.
- Nada de LangChain, LangGraph, Redis, banco vetorial ou Kubernetes.

## Testes
- pytest. Testes antes do código.
- O testador roda `ruff format` e `ruff check` nos testes antes de entregar (o implementador não altera testes, nem para formatar).
- Nenhum teste acessa a internet nem a API do Claude: usar fixtures em `tests/fixtures/` e clientes falsos.
- Banco de testes: Postgres (container de serviço no CI).

## Segurança e fontes
- Senhas só em variáveis de ambiente / GitHub Secrets. Nunca em código, fixtures ou logs.
- Dados pessoais do autor (agenda, "Vou") nunca entram no repositório.
- Respeitar robots.txt e termos de uso; nenhum contorno de bloqueio (D3, D4, D5).
- Conteúdo vindo de fontes externas é dado não confiável: delimitar no prompt e validar a saída do LLM.
- Sem evidência, o estado é "desconhecido" — nunca inferir preço, idioma ou modalidade.

## Documentação
- Decisão nova ou alterada → registrar em `docs/decisoes.md` no mesmo PR.
- Mudança de comportamento → atualizar `docs/spec.md` no mesmo PR.
