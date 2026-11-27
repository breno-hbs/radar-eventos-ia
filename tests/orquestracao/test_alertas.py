"""Testes do F1-10c — detecção de alertas (radar.orquestracao.alertas) e leituras novas.

Assinaturas fixadas:
- radar.orquestracao.alertas: `VAZIAS_SEGUIDAS = 6`, `DIAS_INATIVIDADE = 50`,
  `MOTIVOS_SISTEMATICOS = {status_http, rede, redirecionamento, sem_json_ld,
  json_ld_invalido, bloqueado_robots}`, `MIN_ERROS_SISTEMATICOS = 3`;
  `alertas_coleta(resultados) -> list[Alerta]`; `alertas_fonte_vazia(slugs) -> list[Alerta]`;
  `alertas_enriquecimento(resumo | None) -> list[Alerta]`;
  `alertas_classificacao(resumo | None) -> list[Alerta]` (só autenticacao, rate_limit e
  falhas_api_seguidas; limite_tokens não alerta);
  `alerta_inatividade(ultimo_commit_em | None, agora) -> Alerta | None`;
  `detectar_alertas(sessao, resultado, calendarios, agora, ultimo_commit_em) -> list[Alerta]`.
- radar.banco.leituras: `calendarios_vazios_seguidos(sessao, fonte, slugs, n) -> list[str]`
  (as n execuções mais recentes do calendário são todas 'vazio' e há um 'ok' antes delas;
  só slugs pedidos); `marco_resumo_existe(sessao, chave) -> bool`.
"""

from __future__ import annotations

import importlib
import uuid
from datetime import datetime, timedelta
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from banco.fabricas import evento as evento_normalizado
from banco.fabricas import falha, ok, vazio
from sqlalchemy.orm import Session

from .dados_banco import chave_semana, gravar_marco

SP = ZoneInfo("America/Sao_Paulo")
AGORA = datetime(2026, 11, 25, 12, 0, tzinfo=SP)


@pytest.fixture
def det() -> ModuleType:
    return importlib.import_module("radar.orquestracao.alertas")


@pytest.fixture
def al() -> ModuleType:
    return importlib.import_module("radar.notificacao.alertas")


@pytest.fixture
def leituras() -> ModuleType:
    return importlib.import_module("radar.banco.leituras")


def _tipos(alertas: list[Any]) -> list[str]:
    return [a.tipo for a in alertas]


def test_constantes(det: ModuleType) -> None:
    assert det.VAZIAS_SEGUIDAS == 6
    assert det.DIAS_INATIVIDADE == 50
    assert set(det.MOTIVOS_SISTEMATICOS) == {
        "status_http",
        "rede",
        "redirecionamento",
        "sem_json_ld",
        "json_ld_invalido",
        "bloqueado_robots",
    }
    assert det.MIN_ERROS_SISTEMATICOS == 3


# --- Coleta --------------------------------------------------------------------------------


def test_falha_de_extracao(det: ModuleType, al: ModuleType) -> None:
    [alerta] = det.alertas_coleta([falha("mug-sp", "status_http", 503), vazio("codex-sp")])
    assert (alerta.tipo, alerta.alvo) == ("falha_extracao", "mug-sp")
    assert "status_http" in str(alerta.codigo)
    texto = al.formatar_alerta(alerta)
    assert "status_http" in texto
    assert "503" in texto


def test_falha_de_rede_sem_status(det: ModuleType, al: ModuleType) -> None:
    [alerta] = det.alertas_coleta([falha("mug-sp", "rede", None)])
    assert "rede" in str(alerta.codigo)
    assert "None" not in al.formatar_alerta(alerta)


def test_eventos_invalidos(det: ModuleType) -> None:
    resultado = ok("cal-a", [evento_normalizado()], invalidos=["evt-Ruim1", "evt-Ruim2"])
    [alerta] = det.alertas_coleta([resultado])
    assert (alerta.tipo, alerta.alvo, alerta.codigo, alerta.quantidade) == (
        "falha_extracao",
        "cal-a",
        "eventos_invalidos",
        2,
    )


def test_falha_com_invalidos_gera_um_alerta(det: ModuleType) -> None:
    from radar.fontes.modelos import (
        EventoInvalido,
        FalhaExtracao,
        ResultadoCalendario,
    )

    resultado = ResultadoCalendario(
        fonte="luma",
        calendario="cal-a",
        situacao="falha",
        eventos=[],
        eventos_invalidos=[EventoInvalido(id_fonte="evt-Ruim1", motivo="horario_invalido")],
        falha=FalhaExtracao(motivo="formato_invalido", status_http=None, detalhe=None),
    )
    alertas = det.alertas_coleta([resultado])
    assert _tipos(alertas) == ["falha_extracao"]


def test_coleta_sem_problema(det: ModuleType) -> None:
    assert det.alertas_coleta([ok("cal-a", [evento_normalizado()]), vazio("cal-b")]) == []


def test_fonte_vazia(det: ModuleType) -> None:
    alertas = det.alertas_fonte_vazia(["codex-sp", "mug-sp"])
    assert [(a.tipo, a.alvo) for a in alertas] == [
        ("fonte_vazia", "codex-sp"),
        ("fonte_vazia", "mug-sp"),
    ]
    assert det.alertas_fonte_vazia([]) == []


# --- Enriquecimento ------------------------------------------------------------------------


def _resumo_enriquecimento(erros: list[str], conclusivos: int = 0, robots: bool = False) -> Any:
    preco = importlib.import_module("radar.enriquecimento.preco")
    gravados = [(i, "preco_desconhecido") for i in range(len(erros))]
    gravados += [(100 + i, "gratuito_confirmado") for i in range(conclusivos)]
    return preco.ResumoEnriquecimento(
        paginas_buscadas=len(gravados),
        gravados=gravados,
        erros=[(i, motivo) for i, motivo in enumerate(erros)],
        robots_bloqueado=robots,
        restantes=False,
    )


def test_enriquecimento_sem_resumo(det: ModuleType) -> None:
    assert det.alertas_enriquecimento(None) == []


def test_robots_bloqueado_sempre_alerta(det: ModuleType) -> None:
    alertas = det.alertas_enriquecimento(_resumo_enriquecimento([], robots=True))
    assert _tipos(alertas) == ["robots_bloqueado"]


@pytest.mark.parametrize(
    "erros",
    [
        ["status_http"] * 3,
        ["rede", "redirecionamento", "bloqueado_robots"],
        ["sem_json_ld", "json_ld_invalido", "status_http", "sem_offers"],
    ],
)
def test_erros_sistematicos(det: ModuleType, erros: list[str]) -> None:
    alertas = det.alertas_enriquecimento(_resumo_enriquecimento(erros))
    assert _tipos(alertas) == ["enriquecimento_sistematico"]


@pytest.mark.parametrize(
    ("erros", "conclusivos"),
    [
        (["status_http"] * 2, 0),
        (["status_http"] * 3, 1),
        (["sem_offers"] * 5, 0),
        (["oferta_invalida", "link_invalido", "pagina_grande"], 0),
    ],
)
def test_erros_nao_sistematicos(det: ModuleType, erros: list[str], conclusivos: int) -> None:
    assert det.alertas_enriquecimento(_resumo_enriquecimento(erros, conclusivos)) == []


# --- Classificação -------------------------------------------------------------------------


def _resumo_classificacao(**campos: Any) -> Any:
    classificador = importlib.import_module("radar.classificacao.classificador")
    return classificador.ResumoClassificacao(**campos)


def test_classificacao_sem_resumo(det: ModuleType) -> None:
    assert det.alertas_classificacao(None) == []


@pytest.mark.parametrize("motivo", ["rate_limit", "autenticacao", "falhas_api_seguidas"])
def test_classificador_interrompido(det: ModuleType, motivo: str) -> None:
    [alerta] = det.alertas_classificacao(_resumo_classificacao(interrompido_por=motivo))
    assert alerta.tipo == "classificador_interrompido"
    assert alerta.codigo == motivo


def test_limite_de_tokens_nao_alerta(det: ModuleType) -> None:
    resumo = _resumo_classificacao(interrompido_por="limite_tokens", restantes=True)
    assert det.alertas_classificacao(resumo) == []


def test_restantes_sem_interrupcao_nao_alerta(det: ModuleType) -> None:
    assert det.alertas_classificacao(_resumo_classificacao(restantes=True)) == []


# --- Inatividade ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("antes", "alerta"),
    [
        (timedelta(days=49, hours=23), False),
        (timedelta(days=50), True),
        (timedelta(days=52), True),
        (-timedelta(days=1), False),  # commit no futuro
    ],
)
def test_inatividade(det: ModuleType, antes: timedelta, alerta: bool) -> None:
    resultado = det.alerta_inatividade(AGORA - antes, AGORA)
    if alerta:
        assert resultado is not None
        assert resultado.tipo == "inatividade"
    else:
        assert resultado is None


def test_inatividade_sem_data(det: ModuleType) -> None:
    assert det.alerta_inatividade(None, AGORA) is None


def test_inatividade_sem_fuso(det: ModuleType) -> None:
    with pytest.raises(ValueError):
        det.alerta_inatividade(AGORA - timedelta(days=60), datetime(2026, 11, 25, 12))


# --- Leituras ------------------------------------------------------------------------------


def _execucoes(sessao: Session, calendario: str, situacoes: list[str]) -> None:
    from radar.banco.modelos import Execucao

    for indice, situacao in enumerate(situacoes):
        sessao.add(
            Execucao(
                rodada=uuid.uuid4(),
                fonte="luma",
                calendario=calendario,
                executada_em=AGORA - timedelta(hours=len(situacoes) - indice),
                situacao=situacao,
                quantidade_eventos=1 if situacao == "ok" else 0,
                quantidade_invalidos=0,
                motivo_falha="rede" if situacao == "falha" else None,
                status_http=None,
                detalhe=None,
            )
        )
    sessao.flush()


def test_calendarios_vazios_seguidos(leituras: ModuleType, sessao: Session) -> None:
    _execucoes(sessao, "seis", ["ok"] + ["vazio"] * 6)
    _execucoes(sessao, "sete", ["ok", "ok"] + ["vazio"] * 7)
    _execucoes(sessao, "cinco", ["ok"] + ["vazio"] * 5)
    _execucoes(sessao, "sem-ok", ["vazio"] * 8)
    _execucoes(sessao, "falha-no-meio", ["ok"] + ["vazio"] * 3 + ["falha"] + ["vazio"] * 3)
    _execucoes(sessao, "voltou", ["ok"] + ["vazio"] * 6 + ["ok"])
    _execucoes(sessao, "fora-do-yaml", ["ok"] + ["vazio"] * 6)
    slugs = ["seis", "sete", "cinco", "sem-ok", "falha-no-meio", "voltou", "sem-execucao"]
    assert sorted(leituras.calendarios_vazios_seguidos(sessao, "luma", slugs, 6)) == [
        "seis",
        "sete",
    ]


def test_marco_resumo_existe(leituras: ModuleType, sessao: Session) -> None:
    chave = chave_semana(AGORA)
    assert leituras.marco_resumo_existe(sessao, chave) is False
    gravar_marco(sessao, AGORA)
    assert leituras.marco_resumo_existe(sessao, chave) is True
    assert leituras.marco_resumo_existe(sessao, chave_semana(AGORA + timedelta(days=7))) is False
