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

F1-07b: `executar(..., arquivo_fontes: Path, dir_diagnostico: Path | None = None)`.
- comunidades = slugs com `comunidade_ia: true` em `arquivo_fontes`; evento de calendário
  marcado conta como IA (o LLM disse não IA → VP);
- com `dir_diagnostico`, cada evento com erro `saida_*` gera `<dir>/<id_fonte>.json` com
  exatamente {id_fonte, motivo_erro, stop_reason, tokens_entrada, tokens_saida,
  texto_resposta}; sem erros, nenhum arquivo; o diretório é criado se faltar; também grava
  os já coletados quando a avaliação é interrompida por erro de API;
- CLI: `--diagnostico DIR` opcional; padrão de `arquivo_fontes` = config/fontes.yaml.
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
import yaml

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
    escrever_fontes(tmp_path / "fontes.yaml", {CALENDARIO: False})
    return avaliacao_dir, fixtures


def escrever_fontes(caminho: Path, comunidades: dict[str, bool]) -> Path:
    """config/fontes.yaml sintético: {slug: comunidade_ia}."""
    entradas = [
        {
            "slug": slug,
            "nome": f"Calendário {slug}",
            "entidade": "calendar",
            "id": f"cal-Teste{indice}",
            "comunidade_ia": comunidade,
        }
        for indice, (slug, comunidade) in enumerate(comunidades.items())
    ]
    caminho.write_text(
        yaml.safe_dump({"calendarios_luma": entradas}, allow_unicode=True), encoding="utf-8"
    )
    return caminho


def _resposta(cliente_mod: ModuleType, **campos: Any) -> Any:
    tokens = {"tokens_entrada": 1_000, "tokens_saida": 200}
    tokens.update({k: campos.pop(k) for k in list(campos) if k.startswith("tokens_")})
    texto = campos.pop("texto", None)
    stop_reason = campos.pop("stop_reason", "end_turn")
    return cliente_mod.RespostaLLM(
        texto=texto if texto is not None else json.dumps(saida_sintetica(**campos)),
        stop_reason=stop_reason,
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
    *,
    arquivo_fontes: Path | None = None,
    dir_diagnostico: Path | None = None,
    **config: Any,
) -> Any:
    dir_avaliacao, dir_fixtures = dirs
    extra: dict[str, Any] = {}
    if dir_diagnostico is not None:
        extra["dir_diagnostico"] = dir_diagnostico
    return avaliacao.executar(
        conjunto,
        saida,
        config=_config(**config),
        cliente=cliente,
        dir_avaliacao=dir_avaliacao,
        dir_fixtures=dir_fixtures,
        arquivo_fontes=arquivo_fontes or dir_fixtures.parent / "fontes.yaml",
        agora=AGORA,
        **extra,
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


@pytest.mark.parametrize("com_diagnostico", [True, False])
def test_id_fonte_fora_do_padrao_falha_antes(
    avaliacao: ModuleType,
    cliente_mod: ModuleType,
    dirs: tuple[Path, Path],
    tmp_path: Path,
    com_diagnostico: bool,
) -> None:
    dir_avaliacao, _ = dirs
    escrever_jsonl(dir_avaliacao / "teste.jsonl", [rotulo("evt-Sint1"), rotulo("../../evt-Sint2")])
    cliente = ClienteFalso(padrao=_resposta(cliente_mod, texto="não é json"))
    saida = tmp_path / "teste.md"
    diagnostico = tmp_path / "diag" if com_diagnostico else None
    with pytest.raises(ValueError):
        _executar(avaliacao, dirs, saida, cliente, dir_diagnostico=diagnostico)
    assert cliente.chamadas == []
    assert not saida.exists()
    assert not (tmp_path / "diag").exists() or list((tmp_path / "diag").iterdir()) == []


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
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "fontes.yaml").write_text(
        (RAIZ / "config" / "fontes.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
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


# --- F1-07b: comunidades de IA ---------------------------------------------------------------


def test_comunidade_de_ia_conta_como_vp(
    avaliacao: ModuleType, cliente_mod: ModuleType, tmp_path: Path
) -> None:
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    escrever_ics(fixtures, "cal-comunidade", [vevento("evt-Com1", "Encontro da comunidade")])
    escrever_ics(fixtures, CALENDARIO, [vevento("evt-Dev1", "Meetup do Dev")])
    dir_avaliacao = tmp_path / "avaliacao"
    dir_avaliacao.mkdir()
    escrever_jsonl(dir_avaliacao / "teste.jsonl", [rotulo("evt-Com1", calendario="cal-comunidade")])
    escrever_jsonl(dir_avaliacao / "dev.jsonl", [rotulo("evt-Dev1")])
    fontes = escrever_fontes(tmp_path / "fontes.yaml", {"cal-comunidade": True, CALENDARIO: False})
    cliente = ClienteFalso(padrao=_resposta(cliente_mod, e_ia=False))
    saida = tmp_path / "teste.md"

    metricas = _executar(
        avaliacao, (dir_avaliacao, fixtures), saida, cliente, arquivo_fontes=fontes
    )

    assert len(cliente.chamadas) == 1
    assert (metricas.vp, metricas.fn, metricas.fp) == (1, 0, 0)
    assert saida.is_file()


def test_calendario_nao_marcado_nao_vira_ia(
    avaliacao: ModuleType, cliente_mod: ModuleType, dirs: tuple[Path, Path], tmp_path: Path
) -> None:
    cliente = ClienteFalso(padrao=_resposta(cliente_mod, e_ia=False))
    metricas = _executar(avaliacao, dirs, tmp_path / "teste.md", cliente)
    # rótulos: 2 IA + 1 não IA; tudo previsto como não IA
    assert (metricas.vp, metricas.fn, metricas.vn) == (0, 2, 1)


# --- F1-07b: diagnóstico das respostas inválidas ---------------------------------------------

CHAVES_DIAGNOSTICO = {
    "id_fonte",
    "motivo_erro",
    "stop_reason",
    "tokens_entrada",
    "tokens_saida",
    "texto_resposta",
}


def _ler(caminho: Path) -> dict[str, Any]:
    return json.loads(caminho.read_text(encoding="utf-8"))


def test_diagnostico_grava_um_arquivo_por_erro_de_saida(
    avaliacao: ModuleType, cliente_mod: ModuleType, dirs: tuple[Path, Path], tmp_path: Path
) -> None:
    cliente = ClienteFalso(
        padrao=_resposta(cliente_mod),
        fila=[
            _resposta(cliente_mod, texto="{ quebrado", tokens_saida=7),
            _resposta(
                cliente_mod, texto='{"e_ia": tr', stop_reason="max_tokens", tokens_saida=1024
            ),
        ],
    )
    diagnostico = tmp_path / "nao" / "existe" / "diag"
    _executar(avaliacao, dirs, tmp_path / "teste.md", cliente, dir_diagnostico=diagnostico)

    assert sorted(p.name for p in diagnostico.iterdir()) == ["evt-Sint1.json", "evt-Sint2.json"]
    um = _ler(diagnostico / "evt-Sint1.json")
    assert set(um) == CHAVES_DIAGNOSTICO
    assert um == {
        "id_fonte": "evt-Sint1",
        "motivo_erro": "saida_json_invalido",
        "stop_reason": "end_turn",
        "tokens_entrada": 1_000,
        "tokens_saida": 7,
        "texto_resposta": "{ quebrado",
    }
    dois = _ler(diagnostico / "evt-Sint2.json")
    assert dois["motivo_erro"] == "saida_truncada"
    assert dois["stop_reason"] == "max_tokens"
    assert dois["tokens_saida"] == 1024
    assert dois["texto_resposta"] == '{"e_ia": tr'


def test_diagnostico_sem_erros_nao_grava_nada(
    avaliacao: ModuleType, cliente_mod: ModuleType, dirs: tuple[Path, Path], tmp_path: Path
) -> None:
    cliente = ClienteFalso(padrao=_resposta(cliente_mod))
    diagnostico = tmp_path / "diag"
    _executar(avaliacao, dirs, tmp_path / "teste.md", cliente, dir_diagnostico=diagnostico)
    assert not diagnostico.exists() or list(diagnostico.iterdir()) == []


def test_diagnostico_gravado_mesmo_com_interrupcao_por_api(
    avaliacao: ModuleType, cliente_mod: ModuleType, dirs: tuple[Path, Path], tmp_path: Path
) -> None:
    cliente = ClienteFalso(
        padrao=_resposta(cliente_mod),
        fila=[
            _resposta(cliente_mod, texto="não é json"),
            _resposta(cliente_mod),
            cliente_mod.ErroAPI(categoria="servidor", status_http=500),
        ],
    )
    diagnostico = tmp_path / "diag"
    saida = tmp_path / "teste.md"
    with pytest.raises(RuntimeError):
        _executar(avaliacao, dirs, saida, cliente, dir_diagnostico=diagnostico)
    assert not saida.exists()
    assert [p.name for p in diagnostico.iterdir()] == ["evt-Sint1.json"]
    assert _ler(diagnostico / "evt-Sint1.json")["texto_resposta"] == "não é json"


def test_sem_dir_diagnostico_nao_grava_texto_bruto(
    avaliacao: ModuleType, cliente_mod: ModuleType, dirs: tuple[Path, Path], tmp_path: Path
) -> None:
    cliente = ClienteFalso(
        padrao=_resposta(cliente_mod), fila=[_resposta(cliente_mod, texto="BRUTO-SEM-DIAG {")]
    )
    _executar(avaliacao, dirs, tmp_path / "teste.md", cliente)
    for arquivo in tmp_path.rglob("*"):
        if arquivo.is_file():
            assert "BRUTO-SEM-DIAG" not in arquivo.read_text(encoding="utf-8", errors="ignore")


def test_cli_aceita_diagnostico(tmp_path: Path) -> None:
    ambiente = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    ambiente["PYTHONPATH"] = str(RAIZ / "src")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "fontes.yaml").write_text(
        (RAIZ / "config" / "fontes.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    processo = subprocess.run(
        [
            sys.executable,
            "-m",
            "radar.avaliacao",
            "--conjunto",
            "teste",
            "--saida",
            str(tmp_path / "x.md"),
            "--diagnostico",
            str(tmp_path / "diag"),
        ],
        cwd=tmp_path,
        env=ambiente,
        capture_output=True,
        text=True,
        timeout=60,
    )
    # a opção é aceita (não é erro de uso, código 2); falha depois por falta de rótulos
    assert processo.returncode == 1
    assert processo.stderr.startswith("erro: ")
    assert "Traceback" not in processo.stderr
