"""Testes do F1-07 — execução da avaliação (`python -m radar.avaliacao`) com cliente falso.

Assinatura fixada (radar.avaliacao.__main__ ou radar.avaliacao, importável como
`radar.avaliacao.executar`):
`executar(conjunto: Literal["teste", "dev"], saida: Path, *, config: Configuracao,
cliente: ClienteLLM | None, dir_avaliacao: Path, dir_fixtures: Path, agora: datetime)`.
- lê `<dir_avaliacao>/teste.jsonl` e `<dir_avaliacao>/dev.jsonl`, carrega os eventos e
  valida a independência dos dois conjuntos ANTES de qualquer chamada;
- `cliente=None` → `criar_cliente_anthropic(config)` (sem chave → ValueError, sem chamada);
- conjunto com mais eventos que `config.limite_chamadas_llm_por_execucao` → ValueError antes
  de começar;
- arquivo de rótulos ausente (teste.jsonl ou dev.jsonl) → ValueError com o nome do arquivo,
  antes de qualquer chamada;
- QUALQUER erro de API (`erro_api` não nulo) → RuntimeError com a categoria na mensagem e
  nenhum relatório gravado;
- limite de tokens atingido → RuntimeError com "limite_tokens"; nenhum relatório que pareça
  completo (ou não grava, ou o arquivo diz "INCOMPLETO");
- saída inválida do LLM (saida_*) é só uma divergência: o relatório é gravado.
- CLI: erro esperado → "erro: …" no stderr, código 1, sem traceback.
- O relatório nunca traz texto da descrição dos eventos (só o título).
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from radar.config import Configuracao

from .fabricas import (
    CALENDARIO,
    TRECHO_RESERVADO,
    ClienteFalso,
    escrever_ics,
    escrever_jsonl,
    rotulo,
    saida_sintetica,
    vevento,
)

RAIZ = Path(__file__).resolve().parents[2]
AGORA = datetime(2026, 11, 25, 12, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))


@pytest.fixture
def avaliacao() -> ModuleType:
    return importlib.import_module("radar.avaliacao")


@pytest.fixture
def cliente_mod() -> ModuleType:
    return importlib.import_module("radar.classificacao.cliente")


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    """(dir_avaliacao, dir_fixtures) com 3 eventos no teste e 1 no dev."""
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    escrever_ics(
        fixtures,
        CALENDARIO,
        [
            vevento("evt-Sint1", "Meetup Um"),
            vevento("evt-Sint2", "Meetup Dois"),
            vevento("evt-Sint3", "Meetup Três"),
            vevento("evt-Dev1", "Meetup do Dev"),
            vevento("evt-Vector1", "MUG - Vector Search na Prática"),
        ],
    )
    avaliacao_dir = tmp_path / "avaliacao"
    avaliacao_dir.mkdir()
    escrever_jsonl(
        avaliacao_dir / "teste.jsonl",
        [rotulo("evt-Sint1"), rotulo("evt-Sint2"), rotulo("evt-Sint3", e_ia=False)],
    )
    escrever_jsonl(avaliacao_dir / "dev.jsonl", [rotulo("evt-Dev1"), rotulo("evt-Vector1")])
    return avaliacao_dir, fixtures


def _resposta(cliente_mod: ModuleType, **campos: Any) -> Any:
    tokens = {"tokens_entrada": 1_000, "tokens_saida": 200}
    tokens.update({k: campos.pop(k) for k in list(campos) if k.startswith("tokens_")})
    texto = campos.pop("texto", None)
    return cliente_mod.RespostaLLM(
        texto=texto if texto is not None else json.dumps(saida_sintetica(**campos)),
        stop_reason="end_turn",
        **tokens,
    )


def _config(**campos: Any) -> Configuracao:
    campos.setdefault("anthropic_api_key", None)
    return Configuracao(**campos)


def _executar(
    avaliacao: ModuleType,
    dirs: tuple[Path, Path],
    saida: Path,
    cliente: Any,
    conjunto: str = "teste",
    **config: Any,
) -> Any:
    dir_avaliacao, dir_fixtures = dirs
    return avaliacao.executar(
        conjunto,
        saida,
        config=_config(**config),
        cliente=cliente,
        dir_avaliacao=dir_avaliacao,
        dir_fixtures=dir_fixtures,
        agora=AGORA,
    )


def _sem_relatorio_completo(saida: Path) -> bool:
    return not saida.exists() or "INCOMPLETO" in saida.read_text(encoding="utf-8").upper()


# --- Caminho feliz -------------------------------------------------------------------------


def test_grava_o_relatorio(
    avaliacao: ModuleType, cliente_mod: ModuleType, dirs: tuple[Path, Path], tmp_path: Path
) -> None:
    prompt = importlib.import_module("radar.classificacao.prompt")
    modelos = importlib.import_module("radar.classificacao.modelos")
    cliente = ClienteFalso(padrao=_resposta(cliente_mod))
    saida = tmp_path / "relatorio" / "teste.md"
    saida.parent.mkdir()

    _executar(avaliacao, dirs, saida, cliente)

    assert len(cliente.chamadas) == 3
    assert all(c.max_tokens == 1024 for c in cliente.chamadas)
    texto = saida.read_text(encoding="utf-8")
    for esperado in (
        "teste",
        prompt.VERSAO_PROMPT,
        modelos.MODELO,
        "2026-11-25",
        "evt-Sint3",  # rótulo não IA, classificado como IA: divergência
        "Meetup Três",
    ):
        assert esperado in texto
    assert "INCOMPLETO" not in texto.upper()
    assert TRECHO_RESERVADO not in texto
    assert "XYZ987" not in texto


def test_conjunto_dev(
    avaliacao: ModuleType, cliente_mod: ModuleType, dirs: tuple[Path, Path], tmp_path: Path
) -> None:
    cliente = ClienteFalso(padrao=_resposta(cliente_mod))
    saida = tmp_path / "dev.md"
    _executar(avaliacao, dirs, saida, cliente, conjunto="dev")
    assert len(cliente.chamadas) == 2
    assert saida.is_file()


def test_saida_invalida_vira_divergencia_e_grava(
    avaliacao: ModuleType, cliente_mod: ModuleType, dirs: tuple[Path, Path], tmp_path: Path
) -> None:
    cliente = ClienteFalso(
        padrao=_resposta(cliente_mod), fila=[_resposta(cliente_mod, texto="não é json")]
    )
    saida = tmp_path / "teste.md"
    _executar(avaliacao, dirs, saida, cliente)
    assert len(cliente.chamadas) == 3
    texto = saida.read_text(encoding="utf-8")
    assert "saida_json_invalido" in texto
    assert "evt-Sint1" in texto


# --- Erros antes de começar ----------------------------------------------------------------


def test_sem_chave_e_sem_cliente_e_erro(
    avaliacao: ModuleType,
    dirs: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    saida = tmp_path / "teste.md"
    with pytest.raises(ValueError):
        _executar(avaliacao, dirs, saida, None)
    assert not saida.exists()


def test_mais_eventos_que_o_limite_de_chamadas_falha_antes(
    avaliacao: ModuleType, cliente_mod: ModuleType, dirs: tuple[Path, Path], tmp_path: Path
) -> None:
    cliente = ClienteFalso(padrao=_resposta(cliente_mod))
    saida = tmp_path / "teste.md"
    with pytest.raises(ValueError):
        _executar(avaliacao, dirs, saida, cliente, limite_chamadas_llm_por_execucao=2)
    assert cliente.chamadas == []
    assert not saida.exists()


def test_conjuntos_nao_independentes_falham_antes(
    avaliacao: ModuleType, cliente_mod: ModuleType, dirs: tuple[Path, Path], tmp_path: Path
) -> None:
    dir_avaliacao, _ = dirs
    escrever_jsonl(dir_avaliacao / "dev.jsonl", [rotulo("evt-Dev1"), rotulo("evt-Sint1")])
    cliente = ClienteFalso(padrao=_resposta(cliente_mod))
    saida = tmp_path / "dev.md"
    with pytest.raises(ValueError):
        _executar(avaliacao, dirs, saida, cliente, conjunto="dev")
    assert cliente.chamadas == []
    assert not saida.exists()


def test_caso_da_d7_no_teste_falha_antes(
    avaliacao: ModuleType, cliente_mod: ModuleType, dirs: tuple[Path, Path], tmp_path: Path
) -> None:
    dir_avaliacao, _ = dirs
    escrever_jsonl(dir_avaliacao / "teste.jsonl", [rotulo("evt-Sint1"), rotulo("evt-Vector1")])
    escrever_jsonl(dir_avaliacao / "dev.jsonl", [rotulo("evt-Dev1")])
    cliente = ClienteFalso(padrao=_resposta(cliente_mod))
    saida = tmp_path / "teste.md"
    with pytest.raises(ValueError):
        _executar(avaliacao, dirs, saida, cliente)
    assert cliente.chamadas == []
    assert not saida.exists()


@pytest.mark.parametrize("ausente", ["teste.jsonl", "dev.jsonl"])
def test_arquivo_de_rotulos_ausente_e_erro_antes(
    avaliacao: ModuleType,
    cliente_mod: ModuleType,
    dirs: tuple[Path, Path],
    tmp_path: Path,
    ausente: str,
) -> None:
    dir_avaliacao, _ = dirs
    (dir_avaliacao / ausente).unlink()
    cliente = ClienteFalso(padrao=_resposta(cliente_mod))
    saida = tmp_path / "teste.md"
    with pytest.raises(ValueError) as info:
        _executar(avaliacao, dirs, saida, cliente)
    assert ausente in str(info.value)
    assert cliente.chamadas == []
    assert not saida.exists()


# --- Interrupções --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("categoria", "status"),
    [
        ("autenticacao", 401),
        ("rate_limit", 429),
        ("servidor", 500),
        ("timeout", None),
        ("conexao", None),
        ("requisicao", 400),
        ("outro", None),
    ],
)
def test_qualquer_erro_de_api_interrompe_sem_relatorio(
    avaliacao: ModuleType,
    cliente_mod: ModuleType,
    dirs: tuple[Path, Path],
    tmp_path: Path,
    categoria: str,
    status: int | None,
) -> None:
    cliente = ClienteFalso(
        padrao=_resposta(cliente_mod),
        fila=[cliente_mod.ErroAPI(categoria=categoria, status_http=status)],
    )
    saida = tmp_path / "teste.md"
    with pytest.raises(RuntimeError) as info:
        _executar(avaliacao, dirs, saida, cliente)
    assert categoria in str(info.value)
    assert len(cliente.chamadas) == 1
    assert not saida.exists()


def test_erro_de_servidor_no_segundo_evento_interrompe(
    avaliacao: ModuleType, cliente_mod: ModuleType, dirs: tuple[Path, Path], tmp_path: Path
) -> None:
    cliente = ClienteFalso(
        padrao=_resposta(cliente_mod),
        fila=[
            _resposta(cliente_mod),
            cliente_mod.ErroAPI(categoria="servidor", status_http=500),
        ],
    )
    saida = tmp_path / "teste.md"
    with pytest.raises(RuntimeError) as info:
        _executar(avaliacao, dirs, saida, cliente)
    assert "servidor" in str(info.value)
    assert len(cliente.chamadas) == 2
    assert not saida.exists()
    assert list(tmp_path.glob("teste.md*")) == []


def test_limite_de_tokens_interrompe(
    avaliacao: ModuleType, cliente_mod: ModuleType, dirs: tuple[Path, Path], tmp_path: Path
) -> None:
    cliente = ClienteFalso(padrao=_resposta(cliente_mod, tokens_entrada=600))
    saida = tmp_path / "teste.md"
    with pytest.raises(RuntimeError) as info:
        _executar(avaliacao, dirs, saida, cliente, limite_tokens_entrada_por_execucao=1_000)
    assert "limite_tokens" in str(info.value)
    assert len(cliente.chamadas) == 2
    assert _sem_relatorio_completo(saida)


# --- Linha de comando ----------------------------------------------------------------------


def test_cli_recusa_conjunto_desconhecido(tmp_path: Path) -> None:
    ambiente = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    ambiente["PYTHONPATH"] = str(RAIZ / "src")
    processo = subprocess.run(
        [
            sys.executable,
            "-m",
            "radar.avaliacao",
            "--conjunto",
            "producao",
            "--saida",
            str(tmp_path / "x.md"),
        ],
        cwd=tmp_path,
        env=ambiente,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert processo.returncode == 2
    assert "--conjunto" in processo.stderr
    assert not (tmp_path / "x.md").exists()


def test_cli_sem_rotulos_imprime_erro_e_sai_com_1(tmp_path: Path) -> None:
    # cwd vazio: os rótulos do diretório padrão não existem
    ambiente = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    ambiente["PYTHONPATH"] = str(RAIZ / "src")
    saida = tmp_path / "x.md"
    processo = subprocess.run(
        [sys.executable, "-m", "radar.avaliacao", "--conjunto", "teste", "--saida", str(saida)],
        cwd=tmp_path,
        env=ambiente,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert processo.returncode == 1
    assert processo.stderr.startswith("erro: ")
    assert "teste.jsonl" in processo.stderr
    assert "Traceback" not in processo.stderr
    assert not saida.exists()
