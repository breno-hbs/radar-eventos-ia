"""Testes do F1-10b — linha de comando `radar` (sem rede, LLM falso, Postgres de teste).

Assinaturas fixadas (radar.cli):
- `Fabricas` (dataclass frozen; todos os campos com padrão de produção):
  http_luma: Callable[[], httpx.Client]; http_telegram: Callable[[], httpx.Client];
  llm: Callable[[Configuracao], ClienteLLM]; abrir_sessao: Callable[[Configuracao],
  ContextManager[Session]]; relogio: Callable[[], datetime]; nova_rodada: Callable[[], UUID];
  migrar: Callable[[SecretStr], None];
- `variaveis_ausentes(config, nomes) -> list[str]`;
- `carregar_configuracao_segura() -> Configuracao | list[str]` (ValidationError → nomes das
  variáveis em maiúsculas, nunca valores);
- `configurar_logs() -> None`;
- `main(argv=None, *, fabricas=Fabricas()) -> int`:
  `radar executar [--fontes caminho]` e `radar migrar`.
Saídas: variáveis ausentes/inválidas → stderr "erro: variáveis ausentes ou inválidas: A, B"
e 2; fontes inválido → 2; erro ao criar clientes → 2; ErroEtapa → "erro na etapa <etapa>:
<Tipo>" e 1; outra exceção → "erro inesperado: <Tipo>" e 1; sucesso → linhas_resumo no
stdout e `codigo_saida`. Nunca traceback nem str(exceção).
"""

from __future__ import annotations

import importlib
import logging
import tomllib
import uuid
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy.orm import Session

from .falsos import AGORA, CHAT, FONTES, RAIZ, TOKEN, LLMFalso, RedeLuma, RedeTelegram

URL_BANCO = "postgresql://usuario:senha-do-banco-XYZ@localhost:5432/radar_falso"
CHAVE_LLM = "sk-ant-SEGREDO-llm"
OBRIGATORIAS = ("DATABASE_URL", "ANTHROPIC_API_KEY", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID")
OUTRAS = (
    "FUSO_HORARIO",
    "LIMITE_CHAMADAS_LLM_POR_EXECUCAO",
    "LIMITE_PAGINAS_ENRIQUECIMENTO_POR_EXECUCAO",
    "LIMITE_AUSENCIAS_NAO_ENCONTRADO",
    "LIMITE_TOKENS_ENTRADA_POR_EXECUCAO",
    "LIMITE_TOKENS_SAIDA_POR_EXECUCAO",
)
SEGREDOS = (TOKEN, "SEGREDO", "senha-do-banco", URL_BANCO, str(CHAT), CHAVE_LLM)


@pytest.fixture
def cli() -> ModuleType:
    return importlib.import_module("radar.cli")


@pytest.fixture(autouse=True)
def ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    for nome in (*OBRIGATORIAS, *OUTRAS):
        monkeypatch.delenv(nome, raising=False)
    monkeypatch.setenv("DATABASE_URL", URL_BANCO)
    monkeypatch.setenv("ANTHROPIC_API_KEY", CHAVE_LLM)
    monkeypatch.setenv("TELEGRAM_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", str(CHAT))
    monkeypatch.setenv("LIMITE_CHAMADAS_LLM_POR_EXECUCAO", "5")


def _fabricas(
    cli: ModuleType,
    sessao: Session | None = None,
    *,
    llm: LLMFalso | None = None,
    telegram: RedeTelegram | None = None,
    migrar: Any = None,
    llm_fabrica: Any = None,
) -> Any:
    @contextmanager
    def abrir_sessao(_config: Any) -> Iterator[Session]:
        assert sessao is not None, "este teste não deveria abrir sessão"
        yield sessao

    rede_telegram = telegram or RedeTelegram()
    falso = llm or LLMFalso()
    return cli.Fabricas(
        http_luma=lambda: RedeLuma().cliente(),
        http_telegram=rede_telegram.http,
        llm=llm_fabrica or (lambda _config: falso),
        abrir_sessao=abrir_sessao,
        relogio=lambda: AGORA,
        nova_rodada=uuid.uuid4,
        migrar=migrar or (lambda _url: None),
    )


def _saidas(capsys: pytest.CaptureFixture[str]) -> tuple[str, str]:
    capturado = capsys.readouterr()
    return capturado.out, capturado.err


def _sem_segredos(*textos: str) -> None:
    for texto in textos:
        for segredo in SEGREDOS:
            assert segredo not in texto
        assert "Traceback" not in texto


# --- executar ------------------------------------------------------------------------------


def test_executar_com_sucesso(
    cli: ModuleType, sessao: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    telegram = RedeTelegram()
    codigo = cli.main(
        ["executar", "--fontes", str(FONTES)], fabricas=_fabricas(cli, sessao, telegram=telegram)
    )
    out, err = _saidas(capsys)
    assert codigo == 0
    assert telegram.corpos
    assert out.strip()
    _sem_segredos(out, err)
    for titulo in ("Devin Meetup", "Vector Search", "AI Collective", "Construindo IA"):
        assert titulo not in out
        assert titulo not in err
    for linha in out.strip().splitlines():
        assert "=" in linha


def test_executar_telegram_401_sai_com_3(
    cli: ModuleType, sessao: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    codigo = cli.main(
        ["executar", "--fontes", str(FONTES)],
        fabricas=_fabricas(cli, sessao, telegram=RedeTelegram(status=401)),
    )
    assert codigo == 3
    _sem_segredos(*_saidas(capsys))


@pytest.mark.parametrize(
    "ausentes",
    [
        ("DATABASE_URL",),
        ("ANTHROPIC_API_KEY", "TELEGRAM_TOKEN"),
        ("TELEGRAM_CHAT_ID",),
        OBRIGATORIAS,
    ],
)
def test_variaveis_ausentes(
    cli: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    ausentes: tuple[str, ...],
) -> None:
    for nome in ausentes:
        monkeypatch.delenv(nome)
    codigo = cli.main(["executar", "--fontes", str(FONTES)], fabricas=_fabricas(cli))
    out, err = _saidas(capsys)
    assert codigo == 2
    assert err.startswith("erro: variáveis ausentes ou inválidas: ")
    for nome in ausentes:
        assert nome in err
    for nome in set(OBRIGATORIAS) - set(ausentes):
        assert nome not in err
    _sem_segredos(out, err)


def test_chat_id_invalido(
    cli: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "abc-segredo")
    codigo = cli.main(["executar", "--fontes", str(FONTES)], fabricas=_fabricas(cli))
    out, err = _saidas(capsys)
    assert codigo == 2
    assert "TELEGRAM_CHAT_ID" in err
    assert "abc-segredo" not in out + err
    _sem_segredos(out, err)


def test_fuso_invalido(
    cli: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FUSO_HORARIO", "Marte/Base-segredo")
    codigo = cli.main(["executar", "--fontes", str(FONTES)], fabricas=_fabricas(cli))
    out, err = _saidas(capsys)
    assert codigo == 2
    assert "FUSO_HORARIO" in err
    assert "Marte" not in out + err
    _sem_segredos(out, err)


def test_fontes_invalido(
    cli: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ruim = tmp_path / "fontes.yaml"
    ruim.write_text("calendarios_luma: [{slug: 'X X'}]\n", encoding="utf-8")
    codigo = cli.main(["executar", "--fontes", str(ruim)], fabricas=_fabricas(cli))
    assert codigo == 2
    _sem_segredos(*_saidas(capsys))


def test_fontes_inexistente(
    cli: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    codigo = cli.main(
        ["executar", "--fontes", str(tmp_path / "nao-existe.yaml")], fabricas=_fabricas(cli)
    )
    assert codigo == 2
    _sem_segredos(*_saidas(capsys))


def test_erro_ao_criar_clientes(cli: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
    def quebra(_config: Any) -> Any:
        raise ValueError(f"chave {CHAVE_LLM} inválida")

    codigo = cli.main(
        ["executar", "--fontes", str(FONTES)], fabricas=_fabricas(cli, llm_fabrica=quebra)
    )
    assert codigo == 2
    _sem_segredos(*_saidas(capsys))


def test_erro_em_etapa(
    cli: ModuleType, sessao: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    llm = LLMFalso(erro=RuntimeError("dado-sensivel-xyz"))
    codigo = cli.main(
        ["executar", "--fontes", str(FONTES)], fabricas=_fabricas(cli, sessao, llm=llm)
    )
    out, err = _saidas(capsys)
    assert codigo == 1
    assert "erro na etapa classificacao: RuntimeError" in err
    assert "dado-sensivel" not in out + err
    _sem_segredos(out, err)


def test_erro_inesperado(cli: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
    @contextmanager
    def abrir_sessao(_config: Any) -> Iterator[Session]:
        raise KeyError("segredo-inesperado")
        yield  # pragma: no cover

    base = _fabricas(cli)
    fabricas = cli.Fabricas(
        http_luma=base.http_luma,
        http_telegram=base.http_telegram,
        llm=base.llm,
        abrir_sessao=abrir_sessao,
        relogio=base.relogio,
        nova_rodada=base.nova_rodada,
        migrar=base.migrar,
    )
    codigo = cli.main(["executar", "--fontes", str(FONTES)], fabricas=fabricas)
    out, err = _saidas(capsys)
    assert codigo == 1
    assert "erro inesperado: KeyError" in err
    assert "segredo-inesperado" not in out + err


# --- migrar --------------------------------------------------------------------------------


def test_migrar_sem_url(
    cli: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DATABASE_URL")
    for nome in ("ANTHROPIC_API_KEY", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(nome)
    chamadas: list[Any] = []
    codigo = cli.main(["migrar"], fabricas=_fabricas(cli, migrar=chamadas.append))
    out, err = _saidas(capsys)
    assert codigo == 2
    assert "DATABASE_URL" in err
    assert chamadas == []


def test_migrar_so_precisa_da_url(
    cli: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for nome in ("ANTHROPIC_API_KEY", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(nome)
    chamadas: list[Any] = []
    codigo = cli.main(["migrar"], fabricas=_fabricas(cli, migrar=chamadas.append))
    assert codigo == 0
    assert len(chamadas) == 1
    assert isinstance(chamadas[0], SecretStr)
    assert chamadas[0].get_secret_value() == URL_BANCO
    _sem_segredos(*_saidas(capsys))


def test_migrar_com_falha(cli: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
    def falha(_url: SecretStr) -> None:
        raise RuntimeError(f"não conectou em {URL_BANCO}")

    codigo = cli.main(["migrar"], fabricas=_fabricas(cli, migrar=falha))
    out, err = _saidas(capsys)
    assert codigo == 1
    assert "erro na migração: RuntimeError" in err
    _sem_segredos(out, err)


# --- Auxiliares ----------------------------------------------------------------------------


def test_carregar_configuracao_segura(cli: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    from radar.config import Configuracao

    assert isinstance(cli.carregar_configuracao_segura(), Configuracao)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "abc-segredo")
    monkeypatch.setenv("LIMITE_CHAMADAS_LLM_POR_EXECUCAO", "zero-segredo")
    nomes = cli.carregar_configuracao_segura()
    assert sorted(nomes) == ["LIMITE_CHAMADAS_LLM_POR_EXECUCAO", "TELEGRAM_CHAT_ID"]


def test_funcao_variaveis_ausentes(cli: ModuleType) -> None:
    from radar.config import Configuracao

    config = Configuracao(database_url=None, anthropic_api_key=CHAVE_LLM, telegram_token=None)
    assert cli.variaveis_ausentes(
        config, ["DATABASE_URL", "ANTHROPIC_API_KEY", "TELEGRAM_TOKEN"]
    ) == ["DATABASE_URL", "TELEGRAM_TOKEN"]


def test_pyproject_expoe_o_comando() -> None:
    dados = tomllib.loads((RAIZ / "pyproject.toml").read_text(encoding="utf-8"))
    assert dados["project"]["scripts"]["radar"] == "radar.cli:main"
    assert (RAIZ / "src" / "radar" / "__main__.py").is_file()


# --- Logs ----------------------------------------------------------------------------------


@pytest.fixture
def root_limpo() -> Iterator[logging.Logger]:
    """Root sem handlers (nem os do pytest, que entram na fase de chamada: por isso cada teste
    chama `_limpar(root)` no início) para observar só os de `configurar_logs`; restaura tudo."""
    root = logging.getLogger()
    handlers, nivel = list(root.handlers), root.level
    try:
        yield root
    finally:
        for handler in root.handlers:
            if handler not in handlers:
                handler.close()
        root.handlers = handlers
        root.setLevel(nivel)
        logging.captureWarnings(False)


def _limpar(root: logging.Logger) -> None:
    root.handlers = []


def test_configurar_logs_nao_falha(cli: ModuleType, root_limpo: logging.Logger) -> None:
    _limpar(root_limpo)
    assert cli.configurar_logs() is None
    assert root_limpo.handlers


def test_logs_de_bibliotecas_sem_conteudo(
    cli: ModuleType, root_limpo: logging.Logger, capsys: pytest.CaptureFixture[str]
) -> None:
    _limpar(root_limpo)
    cli.configurar_logs()
    try:
        raise RuntimeError("dado-sensivel na exceção")
    except RuntimeError:
        logging.getLogger("psycopg").error(
            "falha em %s", "host=segredo.neon.tech user=u", exc_info=True
        )
        logging.getLogger("sqlalchemy.pool").error(
            "conexão segredo perdida: %s", "dado-sensivel", exc_info=True
        )
    with warnings.catch_warnings():
        warnings.simplefilter("always")
        warnings.warn("dado-sensivel no aviso", UserWarning, stacklevel=1)
    logging.getLogger("radar.teste").warning("mensagem do radar: %s", "visivel")
    for handler in root_limpo.handlers:
        handler.flush()
    out, err = _saidas(capsys)
    texto = out + err
    assert "psycopg ERROR" in texto
    assert "sqlalchemy.pool ERROR" in texto
    assert "py.warnings WARNING" in texto
    assert "mensagem do radar: visivel" in texto
    for proibido in ("segredo", "dado-sensivel", "Traceback", "neon.tech", "RuntimeError"):
        assert proibido not in texto


def test_resumo_sai_uma_vez(
    cli: ModuleType,
    sessao: Session,
    root_limpo: logging.Logger,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _limpar(root_limpo)
    codigo = cli.main(["executar", "--fontes", str(FONTES)], fabricas=_fabricas(cli, sessao))
    out, err = _saidas(capsys)
    assert codigo == 0
    linhas_out = [linha for linha in out.splitlines() if linha.strip()]
    assert linhas_out
    texto = out + err
    for linha in linhas_out:
        assert texto.count(linha) == 1, linha


# --- Cliente do LLM fechado ----------------------------------------------------------------


@dataclass
class LLMComClose(LLMFalso):
    fechamentos: int = 0

    def close(self) -> None:
        self.fechamentos += 1


def test_llm_fechado_apos_sucesso(cli: ModuleType, sessao: Session) -> None:
    llm = LLMComClose()
    codigo = cli.main(
        ["executar", "--fontes", str(FONTES)], fabricas=_fabricas(cli, sessao, llm=llm)
    )
    assert codigo == 0
    assert llm.fechamentos == 1


def test_llm_fechado_quando_telegram_falha_na_criacao(
    cli: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    llm = LLMComClose()
    base = _fabricas(cli, llm=llm)

    def quebra() -> Any:
        raise RuntimeError(f"sem rede {TOKEN}")

    fabricas = cli.Fabricas(
        http_luma=base.http_luma,
        http_telegram=quebra,
        llm=base.llm,
        abrir_sessao=base.abrir_sessao,
        relogio=base.relogio,
        nova_rodada=base.nova_rodada,
        migrar=base.migrar,
    )
    codigo = cli.main(["executar", "--fontes", str(FONTES)], fabricas=fabricas)
    assert codigo == 2
    assert llm.fechamentos == 1
    _sem_segredos(*_saidas(capsys))


def test_close_que_falha_nao_vaza(
    cli: ModuleType, sessao: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    import httpx

    fechados: list[str] = []

    @dataclass
    class LLMCloseQuebra(LLMFalso):
        def close(self) -> None:
            fechados.append("llm")
            raise RuntimeError("dado-sensivel no close do llm")

    class ClienteLumaQuebra(httpx.Client):
        def close(self) -> None:
            fechados.append("luma")
            super().close()
            raise RuntimeError("dado-sensivel no close da luma")

    class ClienteTelegramContado(httpx.Client):
        def close(self) -> None:
            fechados.append("telegram")
            super().close()

    luma, telegram = RedeLuma(), RedeTelegram()
    llm = LLMCloseQuebra()
    base = _fabricas(cli, sessao, llm=llm, telegram=telegram)
    fabricas = cli.Fabricas(
        http_luma=lambda: ClienteLumaQuebra(
            transport=httpx.MockTransport(luma), follow_redirects=False
        ),
        http_telegram=lambda: ClienteTelegramContado(transport=httpx.MockTransport(telegram)),
        llm=base.llm,
        abrir_sessao=base.abrir_sessao,
        relogio=base.relogio,
        nova_rodada=base.nova_rodada,
        migrar=base.migrar,
    )
    codigo = cli.main(["executar", "--fontes", str(FONTES)], fabricas=fabricas)
    out, err = _saidas(capsys)
    assert codigo == 0
    assert telegram.corpos
    assert sorted(fechados) == ["llm", "luma", "telegram"]
    assert "dado-sensivel" not in out + err
    assert "Traceback" not in out + err


# --- F1-10c: --ultimo-commit-em ------------------------------------------------------------


@pytest.mark.parametrize(
    "valor", ["abc-segredo", "2026-10-01T00:00:00", "2026-13-01T00:00:00-03:00", ""]
)
def test_ultimo_commit_em_invalido(
    cli: ModuleType, capsys: pytest.CaptureFixture[str], valor: str
) -> None:
    codigo = cli.main(
        ["executar", "--fontes", str(FONTES), "--ultimo-commit-em", valor],
        fabricas=_fabricas(cli),
    )
    out, err = _saidas(capsys)
    assert codigo == 2
    assert "erro: argumento --ultimo-commit-em inválido" in err
    if valor:
        assert valor not in out + err
    _sem_segredos(out, err)


def test_ultimo_commit_em_antigo_gera_alerta(
    cli: ModuleType, sessao: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    from .dados_banco import gravar_marco

    gravar_marco(sessao, AGORA)
    sessao.commit()
    telegram = RedeTelegram()
    antigo = (AGORA - timedelta(days=55)).isoformat()
    codigo = cli.main(
        ["executar", "--fontes", str(FONTES), "--ultimo-commit-em", antigo],
        fabricas=_fabricas(cli, sessao, telegram=telegram),
    )
    out, err = _saidas(capsys)
    assert codigo == 0
    assert any("Reativação" in c["text"] for c in telegram.corpos)
    assert "alerta_inatividade=1" in out
    _sem_segredos(out, err)
