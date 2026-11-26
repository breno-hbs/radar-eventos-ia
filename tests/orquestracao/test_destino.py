"""Testes do F1-10a — decisão de destino de um evento (função pura, sem banco nem rede).

Assinaturas fixadas (radar.orquestracao.destino):
- `Destino`, `MotivoDestino` (Literals do plano), `HORIZONTE_DIAS_LONGO = 90`,
  `HORIZONTE_DIAS_PADRAO = 30`;
- `Decisao(destino, motivo, e_ia_por_comunidade: bool = False)` frozen, extra="forbid";
- `horizonte_do_tipo(tipo: TipoEvento | None) -> int` (conferencia/hackathon → 90; demais e
  None → 30);
- `e_comunidade_ia(calendarios, slugs) -> bool`;
- `slugs_comunidade_ia(calendarios: Sequence[CalendarioLuma]) -> frozenset[str]`;
- `localizacao_efetiva(filtro: str, saida: SaidaClassificador | None) -> str`;
- `exclusivamente_online(local_efetivo: str, modalidade: str) -> bool`;
- `decidir_destino(evento, saida, *, em_falha_classificacao, slugs_comunidade_ia, agora,
  proximo_resumo) -> Decisao`, regras na ordem do plano. Preço nunca muda o destino.
- F1-10c (decisão do autor): estrangeiro sem tradução e NÃO exclusivamente online com
  `inicio < proximo_resumo` → "aviso"/"estrangeiro_antes_do_resumo"; senão
  "resumo_semanal"/"estrangeiro_presencial". Exclusivamente online continua descartado.
"""

from __future__ import annotations

import importlib
import typing
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from classificacao.fabricas import campos_evento, saida_valida
from pydantic import ValidationError

SP = ZoneInfo("America/Sao_Paulo")
AGORA = datetime(2026, 11, 25, 12, 0, tzinfo=SP)
PROXIMO_RESUMO = AGORA + timedelta(days=5)
RAIZ = Path(__file__).resolve().parents[2]
COMUNIDADES = frozenset({"ai-collective", "codex-sp"})

MOTIVOS = {
    "estado_inativo",
    "fora_de_sp",
    "terminou",
    "sem_classificacao",
    "nao_ia",
    "localizacao_llm_fora",
    "alem_do_horizonte_do_tipo",
    "idioma_ok",
    "idioma_nao_confirmado",
    "estrangeiro_presencial",
    "estrangeiro_online",
    "estrangeiro_antes_do_resumo",
    "falha_classificacao",
    "falha_antes_do_resumo",
}
DESTINOS = {"aviso", "aviso_excepcional", "resumo_semanal", "aguardar", "descartar"}


@pytest.fixture
def destino() -> ModuleType:
    return importlib.import_module("radar.orquestracao.destino")


def evento(dias: float = 10, **campos: Any) -> Any:
    from radar.banco.modelos import Evento

    inicio = AGORA + timedelta(days=dias)
    campos.setdefault("inicio", inicio)
    campos.setdefault("fim", campos["inicio"] + timedelta(hours=2))
    campos.setdefault("calendarios", ["cal-a"])
    return Evento(**campos_evento(**campos))


_LOCAL_TRECHO = {"sp": "Av. Paulista", "fora": "Rio de Janeiro", "online": "Zoom"}


def saida(**campos: Any) -> Any:
    modelos = importlib.import_module("radar.classificacao.modelos")
    if "localizacao" in campos and "trecho_localizacao" not in campos:
        campos["trecho_localizacao"] = _LOCAL_TRECHO.get(campos["localizacao"], "")
    if "modalidade" in campos and "trecho_modalidade" not in campos:
        campos["trecho_modalidade"] = "" if campos["modalidade"] == "desconhecida" else "x"
    if "idioma" in campos and "trecho_idioma" not in campos:
        sem = campos["idioma"] == "desconhecido" and not campos.get("tem_traducao", False)
        campos["trecho_idioma"] = "" if sem else "x"
    return modelos.SaidaClassificador.model_validate(saida_valida(**campos))


def decidir(
    destino: ModuleType,
    ev: Any = None,
    s: Any = ...,
    *,
    falha: bool = False,
    slugs: frozenset[str] = COMUNIDADES,
    agora: datetime = AGORA,
    proximo: datetime = PROXIMO_RESUMO,
) -> Any:
    return destino.decidir_destino(
        ev if ev is not None else evento(),
        saida() if s is ... else s,
        em_falha_classificacao=falha,
        slugs_comunidade_ia=slugs,
        agora=agora,
        proximo_resumo=proximo,
    )


def _par(decisao: Any) -> tuple[str, str]:
    return decisao.destino, decisao.motivo


# --- Tipos e constantes --------------------------------------------------------------------


def test_literais_e_constantes(destino: ModuleType) -> None:
    assert set(typing.get_args(destino.Destino)) == DESTINOS
    assert set(typing.get_args(destino.MotivoDestino)) == MOTIVOS
    assert destino.HORIZONTE_DIAS_LONGO == 90
    assert destino.HORIZONTE_DIAS_PADRAO == 30


def test_decisao_frozen_e_sem_extras(destino: ModuleType) -> None:
    d = destino.Decisao(destino="aviso", motivo="idioma_ok")
    assert d.e_ia_por_comunidade is False
    with pytest.raises(ValidationError):
        d.destino = "descartar"
    with pytest.raises(ValidationError):
        destino.Decisao(destino="aviso", motivo="idioma_ok", extra=1)
    with pytest.raises(ValidationError):
        destino.Decisao(destino="enviar", motivo="idioma_ok")


@pytest.mark.parametrize(
    ("tipo", "dias"),
    [
        ("conferencia", 90),
        ("hackathon", 90),
        ("meetup", 30),
        ("palestra", 30),
        ("workshop", 30),
        ("outro", 30),
        (None, 30),
    ],
)
def test_horizonte_do_tipo(destino: ModuleType, tipo: str | None, dias: int) -> None:
    assert destino.horizonte_do_tipo(tipo) == dias


def test_slugs_comunidade_do_yaml_real(destino: ModuleType) -> None:
    calendarios_mod = importlib.import_module("radar.fontes.calendarios")
    calendarios = calendarios_mod.carregar_calendarios(RAIZ / "config" / "fontes.yaml")
    assert destino.slugs_comunidade_ia(calendarios) == frozenset(
        {"ai-collective", "vibe-space", "codex-sp", "womakerscode"}
    )
    assert destino.slugs_comunidade_ia([]) == frozenset()


@pytest.mark.parametrize(
    ("calendarios", "slugs", "esperado"),
    [
        (["cal-a", "codex-sp"], COMUNIDADES, True),
        (["cal-a"], COMUNIDADES, False),
        (["codex-sp"], frozenset(), False),
        ([], COMUNIDADES, False),
    ],
)
def test_e_comunidade_ia(
    destino: ModuleType, calendarios: list[str], slugs: frozenset[str], esperado: bool
) -> None:
    assert destino.e_comunidade_ia(calendarios, slugs) is esperado


@pytest.mark.parametrize(
    ("filtro", "llm", "esperado"),
    [
        ("sp", "fora", "sp"),
        ("online", "sp", "online"),
        ("sp", None, "sp"),
        ("indefinida", "sp", "sp"),
        ("indefinida", "online", "online"),
        ("indefinida", "fora", "fora"),
        ("indefinida", "desconhecida", "indefinida"),
        ("indefinida", "nao_se_aplica", "indefinida"),
        ("indefinida", None, "indefinida"),
    ],
)
def test_localizacao_efetiva(
    destino: ModuleType, filtro: str, llm: str | None, esperado: str
) -> None:
    s = None if llm is None else saida(localizacao=llm)
    assert destino.localizacao_efetiva(filtro, s) == esperado


@pytest.mark.parametrize(
    ("local", "modalidade", "esperado"),
    [
        ("online", "online", True),
        ("online", "desconhecida", True),
        ("online", "presencial", False),
        ("online", "hibrido", False),
        ("indefinida", "online", True),
        ("indefinida", "desconhecida", False),
        ("indefinida", "presencial", False),
        ("indefinida", "hibrido", False),
        ("sp", "online", False),
        ("sp", "desconhecida", False),
        ("sp", "presencial", False),
        ("sp", "hibrido", False),
    ],
)
def test_exclusivamente_online(
    destino: ModuleType, local: str, modalidade: str, esperado: bool
) -> None:
    assert destino.exclusivamente_online(local, modalidade) is esperado


# --- 1) Pré-condições ----------------------------------------------------------------------


@pytest.mark.parametrize("campo", ["agora", "proximo"])
def test_horarios_sem_fuso(destino: ModuleType, campo: str) -> None:
    ingenuo = datetime(2026, 11, 25, 12, 0)
    with pytest.raises(ValueError):
        decidir(destino, **{campo: ingenuo})


def test_saida_com_falha_e_incoerente(destino: ModuleType) -> None:
    with pytest.raises(ValueError):
        decidir(destino, s=saida(), falha=True)


# --- 2-4) Descartes iniciais ---------------------------------------------------------------


@pytest.mark.parametrize("estado", ["cancelado", "encerrado", "nao_encontrado"])
def test_estado_inativo(destino: ModuleType, estado: str) -> None:
    assert _par(decidir(destino, evento(estado=estado))) == ("descartar", "estado_inativo")


@pytest.mark.parametrize("estado", ["ativo", "alterado"])
def test_estados_ativos_seguem(destino: ModuleType, estado: str) -> None:
    assert decidir(destino, evento(estado=estado)).destino == "aviso"


@pytest.mark.parametrize("comunidade", [False, True])
def test_fora_sempre_descartado(destino: ModuleType, comunidade: bool) -> None:
    ev = evento(localizacao="fora", calendarios=["codex-sp"] if comunidade else ["cal-a"])
    assert _par(decidir(destino, ev, saida(e_ia=True))) == ("descartar", "fora_de_sp")
    assert _par(decidir(destino, ev, None, falha=True)) == ("descartar", "fora_de_sp")


def test_estado_inativo_vem_antes_de_fora(destino: ModuleType) -> None:
    ev = evento(estado="cancelado", localizacao="fora")
    assert decidir(destino, ev).motivo == "estado_inativo"


def test_terminou_com_fim(destino: ModuleType) -> None:
    ev = evento(inicio=AGORA - timedelta(hours=3), fim=AGORA)  # fim == agora conta
    assert _par(decidir(destino, ev)) == ("descartar", "terminou")


def test_em_andamento_nao_terminou(destino: ModuleType) -> None:
    ev = evento(inicio=AGORA - timedelta(hours=1), fim=AGORA + timedelta(hours=1))
    assert decidir(destino, ev).motivo != "terminou"


def test_terminou_sem_fim_com_hora(destino: ModuleType) -> None:
    ev = evento(inicio=AGORA - timedelta(minutes=1), fim=None)
    assert _par(decidir(destino, ev)) == ("descartar", "terminou")


def test_dia_inteiro_sem_fim(destino: ModuleType) -> None:
    hoje = evento(inicio=datetime(2026, 11, 25, tzinfo=SP), fim=None, dia_inteiro=True)
    assert decidir(destino, hoje).motivo != "terminou"
    ontem = evento(inicio=datetime(2026, 11, 24, tzinfo=SP), fim=None, dia_inteiro=True)
    assert _par(decidir(destino, ontem)) == ("descartar", "terminou")


# --- 5) Falha de classificação -------------------------------------------------------------


def test_falha_antes_do_resumo_e_aviso_excepcional(destino: ModuleType) -> None:
    ev = evento(dias=2)
    assert _par(decidir(destino, ev, None, falha=True)) == (
        "aviso_excepcional",
        "falha_antes_do_resumo",
    )


def test_falha_depois_do_resumo_vai_ao_resumo(destino: ModuleType) -> None:
    ev = evento(dias=10)
    assert _par(decidir(destino, ev, None, falha=True)) == (
        "resumo_semanal",
        "falha_classificacao",
    )


def test_falha_no_horario_do_resumo_vai_ao_resumo(destino: ModuleType) -> None:
    ev = evento(inicio=PROXIMO_RESUMO)
    assert decidir(destino, ev, None, falha=True).destino == "resumo_semanal"


def test_falha_alem_de_30_dias_aguarda(destino: ModuleType) -> None:
    ev = evento(dias=31)
    assert _par(decidir(destino, ev, None, falha=True)) == (
        "aguardar",
        "alem_do_horizonte_do_tipo",
    )
    assert decidir(destino, evento(dias=30), None, falha=True).destino == "resumo_semanal"


def test_falha_com_filtro_indefinido_entra(destino: ModuleType) -> None:
    ev = evento(dias=10, localizacao="indefinida")
    assert decidir(destino, ev, None, falha=True).destino == "resumo_semanal"


def test_proximo_resumo_igual_a_agora(destino: ModuleType) -> None:
    ev = evento(dias=1)
    d = decidir(destino, ev, None, falha=True, proximo=AGORA)
    assert _par(d) == ("resumo_semanal", "falha_classificacao")


def test_comunidade_nao_salva_falha(destino: ModuleType) -> None:
    ev = evento(dias=10, calendarios=["codex-sp"])
    d = decidir(destino, ev, None, falha=True)
    assert _par(d) == ("resumo_semanal", "falha_classificacao")
    assert d.e_ia_por_comunidade is False


# --- 6-7) Classificação e IA ---------------------------------------------------------------


def test_sem_classificacao_aguarda(destino: ModuleType) -> None:
    assert _par(decidir(destino, evento(), None)) == ("aguardar", "sem_classificacao")


def test_nao_ia_descartado(destino: ModuleType) -> None:
    assert _par(decidir(destino, evento(), saida(e_ia=False))) == ("descartar", "nao_ia")


def test_comunidade_transforma_nao_ia_em_aviso(destino: ModuleType) -> None:
    ev = evento(calendarios=["cal-a", "codex-sp"])
    d = decidir(destino, ev, saida(e_ia=False))
    assert _par(d) == ("aviso", "idioma_ok")
    assert d.e_ia_por_comunidade is True


def test_comunidade_com_e_ia_true_nao_marca_flag(destino: ModuleType) -> None:
    ev = evento(calendarios=["codex-sp"])
    d = decidir(destino, ev, saida(e_ia=True))
    assert d.destino == "aviso"
    assert d.e_ia_por_comunidade is False


@pytest.mark.parametrize(
    ("calendarios", "slugs"),
    [(["cal-a"], COMUNIDADES), (["codex-sp"], frozenset())],
)
def test_comunidade_que_nao_conta(
    destino: ModuleType, calendarios: list[str], slugs: frozenset[str]
) -> None:
    ev = evento(calendarios=calendarios)
    assert _par(decidir(destino, ev, saida(e_ia=False), slugs=slugs)) == ("descartar", "nao_ia")


# --- 8) Localização do LLM -----------------------------------------------------------------


def test_indefinida_com_llm_fora_descarta(destino: ModuleType) -> None:
    ev = evento(localizacao="indefinida")
    assert _par(decidir(destino, ev, saida(localizacao="fora"))) == (
        "descartar",
        "localizacao_llm_fora",
    )


@pytest.mark.parametrize("llm", ["desconhecida", "sp", "online"])
def test_indefinida_com_llm_nao_fora_segue(destino: ModuleType, llm: str) -> None:
    ev = evento(localizacao="indefinida")
    assert _par(decidir(destino, ev, saida(localizacao=llm))) == ("aviso", "idioma_ok")


def test_filtro_sp_ignora_llm_fora(destino: ModuleType) -> None:
    ev = evento(localizacao="sp")
    assert _par(decidir(destino, ev, saida(localizacao="fora"))) == ("aviso", "idioma_ok")


def test_comunidade_com_llm_fora_em_indefinida_descarta(destino: ModuleType) -> None:
    ev = evento(localizacao="indefinida", calendarios=["codex-sp"])
    assert decidir(destino, ev, saida(localizacao="fora")).motivo == "localizacao_llm_fora"


# --- 9) Horizonte por tipo -----------------------------------------------------------------


@pytest.mark.parametrize("tipo", ["conferencia", "hackathon"])
def test_tipos_longos_a_60_dias(destino: ModuleType, tipo: str) -> None:
    d = decidir(destino, evento(dias=60), saida(tipo_evento=tipo))
    assert _par(d) == ("aviso", "idioma_ok")


@pytest.mark.parametrize("tipo", ["conferencia", "hackathon"])
def test_tipos_longos_limite_exato_e_alem(destino: ModuleType, tipo: str) -> None:
    assert decidir(destino, evento(dias=90), saida(tipo_evento=tipo)).destino == "aviso"
    alem = evento(inicio=AGORA + timedelta(days=90, seconds=1))
    assert _par(decidir(destino, alem, saida(tipo_evento=tipo))) == (
        "aguardar",
        "alem_do_horizonte_do_tipo",
    )


@pytest.mark.parametrize("tipo", ["meetup", "palestra", "workshop", "outro"])
def test_tipos_curtos(destino: ModuleType, tipo: str) -> None:
    assert _par(decidir(destino, evento(dias=31), saida(tipo_evento=tipo))) == (
        "aguardar",
        "alem_do_horizonte_do_tipo",
    )
    assert decidir(destino, evento(dias=30), saida(tipo_evento=tipo)).destino == "aviso"


@pytest.mark.parametrize(
    "campos",
    [
        {"estado_preco": "pago_com_valor", "trecho_preco": "R$ 500"},
        {"estado_preco": "preco_desconhecido", "trecho_preco": ""},
        {"modalidade": "online"},
        {"modalidade": "hibrido"},
    ],
)
def test_preco_e_modalidade_nao_mudam_horizonte(
    destino: ModuleType, campos: dict[str, Any]
) -> None:
    s = saida(tipo_evento="meetup", **campos)
    assert decidir(destino, evento(dias=31), s).destino == "aguardar"
    assert decidir(destino, evento(dias=10), s).destino == "aviso"


@pytest.mark.parametrize(
    ("estado_preco", "trecho"),
    [
        ("gratuito_confirmado", "Grátis"),
        ("pago_com_valor", "R$ 50"),
        ("pago_sem_valor", "Pago"),
        ("preco_variavel", "Lotes"),
        ("preco_desconhecido", ""),
    ],
)
def test_preco_nunca_muda_o_destino(destino: ModuleType, estado_preco: str, trecho: str) -> None:
    s = saida(estado_preco=estado_preco, trecho_preco=trecho)
    assert _par(decidir(destino, evento(), s)) == ("aviso", "idioma_ok")


# --- 10) Idioma ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("idioma", "traducao", "esperado"),
    [
        ("pt", False, ("aviso", "idioma_ok")),
        ("bilingue", False, ("aviso", "idioma_ok")),
        ("en", True, ("aviso", "idioma_ok")),
        ("outro", True, ("aviso", "idioma_ok")),
        ("desconhecido", True, ("aviso", "idioma_ok")),
        ("desconhecido", False, ("aviso", "idioma_nao_confirmado")),
    ],
)
def test_idiomas(destino: ModuleType, idioma: str, traducao: bool, esperado: tuple) -> None:
    s = saida(idioma=idioma, tem_traducao=traducao)
    assert _par(decidir(destino, evento(), s)) == esperado


@pytest.mark.parametrize("idioma", ["en", "outro"])
@pytest.mark.parametrize(
    ("filtro", "llm_local", "modalidade", "esperado"),
    [
        ("sp", "nao_se_aplica", "presencial", ("resumo_semanal", "estrangeiro_presencial")),
        ("sp", "nao_se_aplica", "hibrido", ("resumo_semanal", "estrangeiro_presencial")),
        ("sp", "nao_se_aplica", "online", ("resumo_semanal", "estrangeiro_presencial")),
        (
            "indefinida",
            "desconhecida",
            "desconhecida",
            ("resumo_semanal", "estrangeiro_presencial"),
        ),
        ("indefinida", "desconhecida", "online", ("descartar", "estrangeiro_online")),
        ("indefinida", "online", "desconhecida", ("descartar", "estrangeiro_online")),
        ("online", "nao_se_aplica", "online", ("descartar", "estrangeiro_online")),
        ("online", "nao_se_aplica", "desconhecida", ("descartar", "estrangeiro_online")),
        ("online", "nao_se_aplica", "presencial", ("resumo_semanal", "estrangeiro_presencial")),
    ],
)
def test_estrangeiro_sem_traducao(
    destino: ModuleType,
    idioma: str,
    filtro: str,
    llm_local: str,
    modalidade: str,
    esperado: tuple,
) -> None:
    ev = evento(localizacao=filtro)
    s = saida(idioma=idioma, tem_traducao=False, localizacao=llm_local, modalidade=modalidade)
    assert _par(decidir(destino, ev, s)) == esperado


def test_estrangeiro_presencial_alem_do_horizonte_aguarda(destino: ModuleType) -> None:
    s = saida(idioma="en", tem_traducao=False, tipo_evento="meetup", modalidade="presencial")
    assert _par(decidir(destino, evento(dias=40), s)) == ("aguardar", "alem_do_horizonte_do_tipo")


def test_decisao_e_sempre_do_tipo_decisao(destino: ModuleType) -> None:
    d = decidir(destino)
    assert isinstance(d, destino.Decisao)
    assert d.destino in DESTINOS
    assert d.motivo in MOTIVOS


# --- F1-10c: estrangeiro antes do próximo resumo -------------------------------------------


@pytest.mark.parametrize("idioma", ["en", "outro"])
@pytest.mark.parametrize("modalidade", ["presencial", "hibrido", "desconhecida"])
def test_estrangeiro_antes_do_resumo_vira_aviso(
    destino: ModuleType, idioma: str, modalidade: str
) -> None:
    s = saida(idioma=idioma, tem_traducao=False, modalidade=modalidade)
    ev = evento(dias=2)  # antes de PROXIMO_RESUMO (agora + 5 dias)
    assert _par(decidir(destino, ev, s)) == ("aviso", "estrangeiro_antes_do_resumo")


def test_estrangeiro_no_horario_do_resumo_vai_ao_resumo(destino: ModuleType) -> None:
    s = saida(idioma="en", tem_traducao=False, modalidade="presencial")
    ev = evento(inicio=PROXIMO_RESUMO)
    assert _par(decidir(destino, ev, s)) == ("resumo_semanal", "estrangeiro_presencial")


def test_estrangeiro_online_antes_do_resumo_continua_descartado(destino: ModuleType) -> None:
    s = saida(idioma="en", tem_traducao=False, modalidade="online")
    ev = evento(dias=2, localizacao="online")
    assert _par(decidir(destino, ev, s)) == ("descartar", "estrangeiro_online")


def test_estrangeiro_antes_do_resumo_com_proximo_igual_a_agora(destino: ModuleType) -> None:
    # resumo saindo agora: nada começa antes de `agora` sem ter terminado → resumo
    s = saida(idioma="en", tem_traducao=False, modalidade="presencial")
    d = decidir(destino, evento(dias=2), s, proximo=AGORA)
    assert _par(d) == ("resumo_semanal", "estrangeiro_presencial")
