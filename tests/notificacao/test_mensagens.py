"""Testes do F1-09 — texto das mensagens (puro, sem rede nem banco).

Assinaturas fixadas (radar.notificacao.mensagens), `fuso: ZoneInfo`:
- `formatar_aviso(evento, saida: SaidaClassificador, enriquecimento: Enriquecimento | None,
  fuso) -> str`;
- `formatar_aviso_excepcional(evento, fuso) -> str` (primeira linha diferente do aviso);
- `formatar_resumo_semanal(itens: Sequence[tuple[Evento, SaidaClassificador | None,
  Enriquecimento | None]], fuso) -> list[tuple[list[int], str]]`: (ids dos eventos, texto)
  por parte; cada parte ≤ 4096 unidades UTF-16; bloco de um evento nunca é quebrado; nenhum
  evento perdido ou repetido; ordem dos itens mantida.
Formatos fixados: data "sáb, 05/12/2026, 19:00–21:00" (dia da semana de lista fixa seg…dom);
sem fim "sáb, 05/12/2026, 19:00"; dia inteiro "05/12/2026 (dia inteiro)" com fim exclusivo;
preço "Gratuito", "Pago", "Pago (valor não informado)", "Preço variável", "Preço
desconhecido", e "Pago — R$ 50,00" quando a página trouxe o valor; link inválido → "link
indisponível"; local "Online", local_texto, url do local, "Endereço oculto (veja a página do
evento)" ou "Local não informado"; idioma "idioma não confirmado", "Idioma: inglês",
"Idioma: estrangeiro (não pt/en)"; sem saída → "Não classificado — confira manualmente".
Texto puro: "<b>" e "&" saem literais.
"""

from __future__ import annotations

import importlib
from datetime import datetime
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from .fabricas import SP, enriquecimento, evento, saida

UTC = ZoneInfo("UTC")
ROTULOS_PRECO = [
    "Gratuito",
    "Pago (valor não informado)",
    "Preço variável",
    "Preço desconhecido",
    "Pago — R$",
]


@pytest.fixture
def msg() -> ModuleType:
    return importlib.import_module("radar.notificacao.mensagens")


def _tam(texto: str) -> int:
    return len(texto.encode("utf-16-le")) // 2


def _aviso(msg: ModuleType, ev: Any = None, s: Any = None, enr: Any = None) -> str:
    return msg.formatar_aviso(ev if ev is not None else evento(), s or saida(), enr, SP)


# --- Data e hora ---------------------------------------------------------------------------


def test_data_mesmo_dia(msg: ModuleType) -> None:
    assert "sáb, 05/12/2026, 19:00–21:00" in _aviso(msg)


def test_data_sem_fim(msg: ModuleType) -> None:
    texto = _aviso(msg, evento(fim=None))
    assert "sáb, 05/12/2026, 19:00" in texto
    assert "19:00–" not in texto


def test_data_em_dias_diferentes(msg: ModuleType) -> None:
    ev = evento(fim=datetime(2026, 12, 6, 5, 0, tzinfo=UTC))  # dom 02:00 em SP
    texto = _aviso(msg, ev)
    for parte in ("sáb", "05/12/2026", "19:00", "dom", "06/12/2026", "02:00"):
        assert parte in texto


@pytest.mark.parametrize(
    ("dia", "rotulo"),
    [(7, "seg"), (8, "ter"), (9, "qua"), (10, "qui"), (11, "sex"), (12, "sáb"), (13, "dom")],
)
def test_dia_da_semana_em_portugues(msg: ModuleType, dia: int, rotulo: str) -> None:
    inicio = datetime(2026, 12, dia, 22, 0, tzinfo=UTC)
    ev = evento(inicio=inicio, fim=None)
    assert f"{rotulo}, {dia:02d}/12/2026, 19:00" in _aviso(msg, ev)


def test_dia_inteiro_de_um_dia(msg: ModuleType) -> None:
    ev = evento(
        dia_inteiro=True,
        inicio=datetime(2026, 12, 5, tzinfo=SP),
        fim=datetime(2026, 12, 6, tzinfo=SP),
    )
    texto = _aviso(msg, ev)
    assert "05/12/2026 (dia inteiro)" in texto
    assert "06/12/2026" not in texto
    assert "00:00" not in texto


def test_dia_inteiro_de_varios_dias_com_fim_exclusivo(msg: ModuleType) -> None:
    ev = evento(
        dia_inteiro=True,
        inicio=datetime(2026, 12, 5, tzinfo=SP),
        fim=datetime(2026, 12, 8, tzinfo=SP),
    )
    texto = _aviso(msg, ev)
    assert "05/12/2026" in texto
    assert "07/12/2026" in texto
    assert "08/12/2026" not in texto
    assert "(dia inteiro)" in texto


@pytest.mark.parametrize("fuso", ["America/Manaus", "UTC", "Asia/Tokyo"])
def test_dia_inteiro_sempre_no_dia_de_sao_paulo(msg: ModuleType, fuso: str) -> None:
    ev = evento(
        dia_inteiro=True,
        inicio=datetime(2026, 12, 5, tzinfo=SP),
        fim=datetime(2026, 12, 6, tzinfo=SP),
    )
    texto = msg.formatar_aviso(ev, saida(), None, ZoneInfo(fuso))
    assert "05/12/2026 (dia inteiro)" in texto
    assert "04/12/2026" not in texto
    assert "06/12/2026" not in texto


def test_usa_o_fuso_recebido(msg: ModuleType) -> None:
    texto = msg.formatar_aviso(evento(fim=None), saida(), None, UTC)
    assert "sáb, 05/12/2026, 22:00" in texto


# --- Local ---------------------------------------------------------------------------------


def test_local_online(msg: ModuleType) -> None:
    texto = _aviso(msg, evento(localizacao="online", local_texto="Zoom"))
    assert "Online" in texto


def test_local_texto(msg: ModuleType) -> None:
    assert "Av. Paulista, 1000 - São Paulo" in _aviso(msg)


def test_local_texto_longo_e_cortado(msg: ModuleType) -> None:
    texto = _aviso(msg, evento(local_texto="L" * 300))
    assert "L" * 200 not in texto
    assert "L" * 150 in texto
    assert "…" in texto


def test_local_url_quando_nao_ha_texto(msg: ModuleType) -> None:
    ev = evento(local_texto=None, local_url="https://us02web.zoom.us/j/1")
    assert "https://us02web.zoom.us/j/1" in _aviso(msg, ev)


def test_endereco_oculto(msg: ModuleType) -> None:
    ev = evento(local_texto=None, local_url=None, endereco_oculto=True)
    assert "Endereço oculto (veja a página do evento)" in _aviso(msg, ev)


def test_local_nao_informado(msg: ModuleType) -> None:
    ev = evento(local_texto=None, local_url="https://user:x@evil.com/", endereco_oculto=False)
    texto = _aviso(msg, ev)
    assert "Local não informado" in texto
    assert "evil.com" not in texto


# --- Preço ---------------------------------------------------------------------------------


def _so(texto: str, rotulo: str) -> bool:
    outros = [r for r in ROTULOS_PRECO if r != rotulo and not rotulo.startswith(r)]
    return rotulo in texto and not any(o in texto for o in outros)


@pytest.mark.parametrize(
    ("estado_llm", "pagina", "rotulo"),
    [
        ("gratuito_confirmado", None, "Gratuito"),
        ("pago_sem_valor", None, "Pago (valor não informado)"),
        ("preco_variavel", None, "Preço variável"),
        ("preco_desconhecido", None, "Preço desconhecido"),
        ("preco_desconhecido", enriquecimento("gratuito_confirmado"), "Gratuito"),
        ("preco_desconhecido", enriquecimento("preco_variavel"), "Preço variável"),
        ("preco_desconhecido", enriquecimento("pago_com_valor", 5000), "Pago — R$ 50,00"),
        ("gratuito_confirmado", enriquecimento("pago_com_valor", 4990), "Pago — R$ 49,90"),
        # exceção da D18: página grátis, LLM pago → vale o LLM
        ("pago_sem_valor", enriquecimento("gratuito_confirmado"), "Pago (valor não informado)"),
    ],
)
def test_preco_efetivo(msg: ModuleType, estado_llm: str, pagina: Any, rotulo: str) -> None:
    trecho = "" if estado_llm == "preco_desconhecido" else "Encontro gratuito"
    s = saida(estado_preco=estado_llm, trecho_preco=trecho)
    assert _so(_aviso(msg, s=s, enr=pagina), rotulo)


def test_pago_com_valor_so_do_llm_mostra_pago(msg: ModuleType) -> None:
    s = saida(estado_preco="pago_com_valor", trecho_preco="Ingresso")
    texto = _aviso(msg, s=s)
    assert "Pago" in texto
    assert "R$" not in texto
    assert "valor não informado" not in texto


# --- Link, resumo, idioma ------------------------------------------------------------------


def test_link_de_inscricao(msg: ModuleType) -> None:
    ev = evento(link_inscricao="https://luma.com/meetup-ia")
    assert "https://luma.com/meetup-ia" in _aviso(msg, ev)


@pytest.mark.parametrize("link", [None, "https://user:senha@luma.com/x", "javascript:alert(1)"])
def test_link_invalido(msg: ModuleType, link: str | None) -> None:
    texto = _aviso(msg, evento(link_inscricao=link))
    assert "link indisponível" in texto
    assert "senha" not in texto
    assert "javascript" not in texto


def test_resumo_do_llm(msg: ModuleType) -> None:
    assert "Meetup sobre modelos de linguagem na Paulista." in _aviso(msg)


@pytest.mark.parametrize(
    ("idioma", "traducao", "trecho", "etiqueta"),
    [
        ("desconhecido", False, "", "idioma não confirmado"),
        ("en", False, "in English", "Idioma: inglês"),
        ("outro", False, "en español", "Idioma: estrangeiro (não pt/en)"),
    ],
)
def test_etiquetas_de_idioma(
    msg: ModuleType, idioma: str, traducao: bool, trecho: str, etiqueta: str
) -> None:
    s = saida(idioma=idioma, tem_traducao=traducao, trecho_idioma=trecho)
    assert etiqueta in _aviso(msg, s=s)


@pytest.mark.parametrize(
    ("idioma", "traducao", "trecho"),
    [
        ("pt", False, "em português"),
        ("bilingue", False, "pt e en"),
        ("en", True, "com tradução"),
        ("outro", True, "com tradução"),
        ("desconhecido", True, "com tradução"),
    ],
)
def test_sem_etiqueta_de_idioma(msg: ModuleType, idioma: str, traducao: bool, trecho: str) -> None:
    s = saida(idioma=idioma, tem_traducao=traducao, trecho_idioma=trecho)
    texto = _aviso(msg, s=s)
    for etiqueta in ("idioma não confirmado", "Idioma: inglês", "Idioma: estrangeiro"):
        assert etiqueta not in texto


# --- Título e texto puro -------------------------------------------------------------------


def test_titulo_longo_e_cortado(msg: ModuleType) -> None:
    texto = _aviso(msg, evento(titulo="T" * 300))
    assert "T" * 201 not in texto
    assert "T" * 150 in texto


def test_titulo_com_html_sai_literal(msg: ModuleType) -> None:
    texto = _aviso(msg, evento(titulo="<b>IA</b> & Dados"))
    assert "<b>IA</b> & Dados" in texto
    assert "&amp;" not in texto


def test_titulo_sem_caracteres_invisiveis(msg: ModuleType) -> None:
    texto = _aviso(msg, evento(titulo="Meetup‮ de\x07 IA"))
    assert "‮" not in texto
    assert "\x07" not in texto
    assert "Meetup de IA" in texto


def test_aviso_cabe_no_telegram(msg: ModuleType) -> None:
    ev = evento(titulo="T" * 5000, local_texto="L" * 5000, link_inscricao="https://luma.com/x")
    assert _tam(_aviso(msg, ev)) <= 4096


# --- Aviso excepcional ---------------------------------------------------------------------


def test_aviso_excepcional(msg: ModuleType) -> None:
    ev = evento()
    texto = msg.formatar_aviso_excepcional(ev, SP)
    assert "Meetup de IA em SP" in texto
    assert "sáb, 05/12/2026, 19:00–21:00" in texto
    assert ev.link_inscricao in texto
    assert texto.splitlines()[0] != _aviso(msg, ev).splitlines()[0]
    assert _tam(texto) <= 4096


# --- Resumo semanal ------------------------------------------------------------------------


def _itens(quantidade: int, **campos: Any) -> list[tuple[Any, Any, Any]]:
    itens = []
    for i in range(quantidade):
        ev = evento(titulo=f"Evento {i:03d} " + "T" * 180, local_texto="L" * 190, **campos)
        ev.id = 1000 + i
        itens.append((ev, saida() if i % 3 else None, None))
    return itens


def test_resumo_vazio(msg: ModuleType) -> None:
    assert msg.formatar_resumo_semanal([], SP) == []


def test_resumo_pequeno_em_uma_parte(msg: ModuleType) -> None:
    itens = _itens(3)
    partes = msg.formatar_resumo_semanal(itens, SP)
    assert len(partes) == 1
    [(ids, texto)] = partes
    assert ids == [1000, 1001, 1002]
    for i in range(3):
        assert f"Evento {i:03d}" in texto
    assert "Não classificado — confira manualmente" in texto


def test_resumo_grande_dividido_sem_perder_nem_repetir(msg: ModuleType) -> None:
    itens = _itens(80)
    partes = msg.formatar_resumo_semanal(itens, SP)
    assert len(partes) >= 3
    todos = [i for ids, _ in partes for i in ids]
    assert todos == [ev.id for ev, _, _ in itens]
    for ids, texto in partes:
        assert _tam(texto) <= 4096
        for evento_id in ids:
            numero = evento_id - 1000
            assert f"Evento {numero:03d}" in texto  # bloco inteiro na mesma parte
        outros = {ev.id for ev, _, _ in itens} - set(ids)
        for evento_id in outros:
            assert f"Evento {evento_id - 1000:03d}" not in texto
