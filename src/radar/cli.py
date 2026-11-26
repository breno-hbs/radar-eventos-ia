"""Linha de comando: `radar executar [--fontes caminho]` e `radar migrar` (D21).

Saídas nunca trazem traceback, `str(exceção)`, valores de variáveis, título, URL, token,
chat_id nem a URL do banco: só nomes de variáveis, tipos de exceção, etapas e contagens.
Códigos: 0 concluída; 1 erro inesperado ou de etapa; 2 configuração; 3 Telegram recusou o bot.
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from pydantic import SecretStr, ValidationError
from sqlalchemy.orm import Session

from radar.banco.migrar import migrar_para_head
from radar.banco.sessao import criar_engine, criar_fabrica_sessoes
from radar.classificacao.cliente import ClienteLLM, criar_cliente_anthropic
from radar.config import Configuracao
from radar.fontes.calendarios import carregar_calendarios
from radar.fontes.rede import criar_cliente
from radar.notificacao.telegram import criar_cliente_telegram
from radar.orquestracao.executar import ErroEtapa, executar, linhas_resumo

FONTES_PADRAO = Path("config/fontes.yaml")
OBRIGATORIAS_EXECUTAR = ("DATABASE_URL", "ANTHROPIC_API_KEY", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID")
TIMEOUT_TELEGRAM = 30.0
# Bibliotecas que registram URLs (com slugs de evento) em INFO.
_LOGGERS_RUIDOSOS = ("httpx", "httpcore", "anthropic", "alembic")
FORMATO_LOG = "%(levelname)s %(name)s: %(message)s"


class _StderrAtual(logging.StreamHandler):  # type: ignore[type-arg]
    """Escreve no `sys.stderr` do momento (não no que existia ao configurar)."""

    @property  # type: ignore[override]
    def stream(self) -> object:
        return sys.stderr

    @stream.setter
    def stream(self, _valor: object) -> None:
        pass


class _SoNomeENivel(logging.Filter):
    """Logs de fora do radar viram só "<nome> <NÍVEL>": mensagem, argumentos, exceção e
    pilha de bibliotecas (e de warnings, em "py.warnings") podem trazer segredo ou dado."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "radar" or record.name.startswith("radar."):
            return True
        record.msg = f"{record.name} {record.levelname}"
        record.args = None
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def _http_telegram() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(TIMEOUT_TELEGRAM), follow_redirects=False)


@contextmanager
def _abrir_sessao(config: Configuracao) -> Iterator[Session]:
    assert config.database_url is not None  # conferido antes por variaveis_ausentes
    engine = criar_engine(config.database_url)
    try:
        with criar_fabrica_sessoes(engine)() as sessao:
            yield sessao
    finally:
        engine.dispose()


def _agora() -> datetime:
    return datetime.now(ZoneInfo("America/Sao_Paulo"))


@dataclass(frozen=True)
class Fabricas:
    """Pontos de troca para os testes; os padrões são os de produção."""

    http_luma: Callable[[], httpx.Client] = field(default=criar_cliente)
    http_telegram: Callable[[], httpx.Client] = field(default=_http_telegram)
    llm: Callable[[Configuracao], ClienteLLM] = field(default=criar_cliente_anthropic)
    abrir_sessao: Callable[[Configuracao], AbstractContextManager[Session]] = field(
        default=_abrir_sessao
    )
    relogio: Callable[[], datetime] = field(default=_agora)
    nova_rodada: Callable[[], uuid.UUID] = field(default=uuid.uuid4)
    migrar: Callable[[SecretStr], None] = field(default=migrar_para_head)


def variaveis_ausentes(config: Configuracao, nomes: Sequence[str]) -> list[str]:
    return [nome for nome in nomes if getattr(config, nome.lower()) is None]


def carregar_configuracao_segura() -> Configuracao | list[str]:
    """Configuração do ambiente; inválida → nomes das variáveis (nunca os valores)."""
    try:
        return Configuracao()
    except ValidationError as erro:
        return sorted({str(e["loc"][0]).upper() for e in erro.errors() if e.get("loc")})


def configurar_logs() -> None:
    """Um handler no stderr, com o filtro de bibliotecas; warnings passam pelo logging."""
    raiz = logging.getLogger()
    if not any(isinstance(f, _SoNomeENivel) for h in raiz.handlers for f in h.filters):
        handler = _StderrAtual()
        handler.setFormatter(logging.Formatter(FORMATO_LOG))
        handler.addFilter(_SoNomeENivel())
        raiz.addHandler(handler)
    raiz.setLevel(logging.INFO)
    logging.captureWarnings(True)
    for nome in _LOGGERS_RUIDOSOS:
        logging.getLogger(nome).setLevel(logging.WARNING)


def _fechar(*recursos: object) -> None:
    """Fecha todos; falha num close não impede os demais nem muda o código de saída."""
    for recurso in recursos:
        fechar = getattr(recurso, "close", None)
        if not callable(fechar):
            continue
        tipo: str | None = None
        try:
            fechar()
        except Exception as erro:
            tipo = type(erro).__name__
        if tipo is not None:
            # fora do `except`: nada da exceção original como contexto no log
            logging.getLogger("radar.cli").warning("falha ao fechar: %s", tipo)


def _erro(mensagem: str) -> None:
    print(f"erro: {mensagem}", file=sys.stderr)


def _faltando(nomes: Sequence[str]) -> int:
    _erro(f"variáveis ausentes ou inválidas: {', '.join(nomes)}")
    return 2


def _migrar(config: Configuracao, fabricas: Fabricas) -> int:
    ausentes = variaveis_ausentes(config, ["DATABASE_URL"])
    if ausentes or config.database_url is None:
        return _faltando(ausentes)
    try:
        fabricas.migrar(config.database_url)
    except Exception as erro:
        print(f"erro na migração: {type(erro).__name__}", file=sys.stderr)
        return 1
    print("migracao=ok")
    return 0


def _executar(config: Configuracao, fontes: Path, fabricas: Fabricas) -> int:
    ausentes = variaveis_ausentes(config, OBRIGATORIAS_EXECUTAR)
    if ausentes:
        return _faltando(ausentes)
    try:
        calendarios = carregar_calendarios(fontes)
    except Exception as erro:
        _erro(f"arquivo de fontes inválido: {type(erro).__name__}")
        return 2

    abertos: list[object] = []
    try:
        llm = fabricas.llm(config)  # antes do http_telegram (D21)
        abertos.append(llm)
        http_telegram = fabricas.http_telegram()
        abertos.append(http_telegram)
        telegram = criar_cliente_telegram(config, http_telegram)
        http = fabricas.http_luma()
        abertos.append(http)
    except Exception as erro:
        _fechar(*abertos)
        _erro(f"não foi possível criar os clientes: {type(erro).__name__}")
        return 2

    try:
        with fabricas.abrir_sessao(config) as sessao:
            resultado = executar(
                sessao,
                config=config,
                calendarios=calendarios,
                http=http,
                llm=llm,
                telegram=telegram,
                agora=fabricas.relogio(),
                rodada=fabricas.nova_rodada(),
            )
    except ErroEtapa as erro:
        print(f"erro na etapa {erro.etapa}: {erro.tipo}", file=sys.stderr)
        return 1
    except Exception as erro:
        print(f"erro inesperado: {type(erro).__name__}", file=sys.stderr)
        return 1
    finally:
        _fechar(*abertos)

    for linha in linhas_resumo(resultado):
        print(linha)
    return resultado.codigo_saida


def main(argv: Sequence[str] | None = None, *, fabricas: Fabricas | None = None) -> int:
    fabricas = fabricas or Fabricas()
    parser = argparse.ArgumentParser(prog="radar", description="Radar de eventos de IA.")
    comandos = parser.add_subparsers(dest="comando", required=True)
    executar_cmd = comandos.add_parser("executar", help="uma execução completa")
    executar_cmd.add_argument("--fontes", type=Path, default=FONTES_PADRAO)
    comandos.add_parser("migrar", help="aplica as migrations do banco")
    args = parser.parse_args(argv)

    configurar_logs()
    config = carregar_configuracao_segura()
    if isinstance(config, list):
        return _faltando(config)
    if args.comando == "migrar":
        return _migrar(config, fabricas)
    return _executar(config, args.fontes, fabricas)
