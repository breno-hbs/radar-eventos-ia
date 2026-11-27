# Radar de Eventos de IA (SP)

Radar pessoal que coleta eventos de Inteligência Artificial em São Paulo (presenciais) e online, classifica com LLM, cruza com a agenda e avisa no Telegram.

> Status: **Fase 1 em fechamento.** O radar roda pela Luma 3 vezes por dia (workflow "Radar"); falta a primeira semana real (F1-11).
>
> Documentação: [especificação](docs/spec.md) · [decisões](docs/decisoes.md) · [viabilidade das fontes](docs/fase-0-viabilidade.md) · [backlog da Fase 1](docs/backlog-fase-1.md)

## Fases

| Fase | Entrega |
|---|---|
| 0 | Viabilidade das fontes (termos de uso, robots.txt, feeds iCal, volume) |
| 1 | Fluxo completo com a primeira fonte viável |
| 2 | Demais fontes e deduplicação |
| 3 | Google Calendar, botões "Vou" / "Não tenho interesse", lembretes |
| 4 | Validação real por 4 semanas e relatório |

## Execução

O workflow **Radar** (`.github/workflows/radar.yml`) roda às 7h40, 12h40 e 19h40 (horário de São Paulo) e também pode ser disparado à mão em **Actions → Radar → Run workflow** (sempre no branch `main`: a data do último commit alimenta o alerta de inatividade). Cada execução aplica as migrações (`radar migrar`) e depois roda `radar executar`.

Secrets necessários (em *Settings → Secrets and variables → Actions*; nunca no código): `DATABASE_URL` (Postgres do Neon, URL direta com `sslmode=require`), `ANTHROPIC_API_KEY`, `TELEGRAM_TOKEN` e `TELEGRAM_CHAT_ID`.

## Reativação

O GitHub desativa workflows agendados após 60 dias sem atividade num repositório público. O radar avisa no Telegram a partir de 50 dias sem commit na `main` (alerta de inatividade).

Para reativar:

1. Em **Actions → Radar**, se o workflow aparecer como desativado, clique em **Enable workflow**.
2. E/ou faça um commit real na `main` (uma correção ou atualização de verdade). Nada de commits artificiais só para manter o workflow vivo.
