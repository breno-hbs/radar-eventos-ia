"""Testes do F1-05 — consolidação de observações e hash de conteúdo (D10, nota do hash).

Assinaturas fixadas (radar.banco.conteudo):
- `VERSAO_HASH = 1`
- `consolidar_observacoes(observacoes: Sequence[EventoNormalizado]) -> EventoConsolidado`
  - todas as observações são do mesmo (fonte, id_fonte), cada uma de um calendário; lista
    vazia ou ids diferentes → ValueError;
  - escalares (titulo, inicio, fim, dia_inteiro, local_*, endereco_oculto, lat/lon,
    link_inscricao, organizador) vêm do calendário de menor slug;
  - `status_fonte` = "cancelado" se qualquer observação for cancelada; senão o do menor slug;
  - `calendarios`: lista ordenada e sem repetição; `descricoes`: dict {slug: descricao|None}.
- `hash_conteudo(evento: EventoConsolidado, descricoes: Mapping[str, str | None]) -> str`
  - SHA-256 em hex minúsculo (64 caracteres) de um JSON canônico;
  - usa os escalares de `evento` e o mapa `descricoes` recebido (o mapa ACUMULADO do banco
    mesclado com o da rodada); `evento.descricoes` não é usado;
  - do mapa, entra só o CONJUNTO de descrições normalizadas distintas, sem as chaves de
    calendário e sem None/vazio, em ordem determinística: calendário novo com descrição
    equivalente (igual após normalização) não muda o hash;
  - fora do hash: calendarios, status_fonte, organizador, fonte, id_fonte.
"""

from __future__ import annotations

import importlib
import re
from datetime import UTC, timedelta
from types import ModuleType

import pytest

from radar.fontes.modelos import EventoNormalizado

from .fabricas import INICIO_PADRAO, evento


@pytest.fixture
def conteudo() -> ModuleType:
    return importlib.import_module("radar.banco.conteudo")


def _hash(conteudo: ModuleType, *observacoes: EventoNormalizado) -> str:
    """Hash usando o próprio mapa de descrições da rodada (caso de uma rodada só)."""
    consolidado = conteudo.consolidar_observacoes(list(observacoes))
    return conteudo.hash_conteudo(consolidado, dict(consolidado.descricoes))


# --- Consolidação --------------------------------------------------------------------


def test_consolidar_uma_observacao(conteudo: ModuleType) -> None:
    obs = evento(calendario="cal-a", descricao="Texto A")
    consolidado = conteudo.consolidar_observacoes([obs])
    assert consolidado.fonte == "luma"
    assert consolidado.id_fonte == "evt-Alfa1"
    assert consolidado.titulo == obs.titulo
    assert consolidado.inicio == obs.inicio
    assert consolidado.fim == obs.fim
    assert consolidado.dia_inteiro is False
    assert consolidado.local_texto == obs.local_texto
    assert consolidado.local_url is None
    assert consolidado.endereco_oculto is False
    assert consolidado.latitude == obs.latitude
    assert consolidado.longitude == obs.longitude
    assert consolidado.link_inscricao == obs.link_inscricao
    assert consolidado.organizador == obs.organizador
    assert consolidado.status_fonte == "desconhecido"
    assert list(consolidado.calendarios) == ["cal-a"]
    assert dict(consolidado.descricoes) == {"cal-a": "Texto A"}


@pytest.mark.parametrize("ordem", [("cal-b", "cal-a"), ("cal-a", "cal-b")])
def test_escalares_vem_do_calendario_de_menor_slug(
    conteudo: ModuleType, ordem: tuple[str, str]
) -> None:
    obs = {
        "cal-a": evento(calendario="cal-a", titulo="Título A", organizador="Org A"),
        "cal-b": evento(calendario="cal-b", titulo="Título B", organizador="Org B"),
    }
    consolidado = conteudo.consolidar_observacoes([obs[slug] for slug in ordem])
    assert consolidado.titulo == "Título A"
    assert consolidado.organizador == "Org A"
    assert list(consolidado.calendarios) == ["cal-a", "cal-b"]


def test_mapa_de_descricoes_tem_uma_entrada_por_calendario(conteudo: ModuleType) -> None:
    consolidado = conteudo.consolidar_observacoes(
        [
            evento(calendario="cal-b", descricao="Texto B"),
            evento(calendario="cal-a", descricao=None),
        ]
    )
    assert dict(consolidado.descricoes) == {"cal-a": None, "cal-b": "Texto B"}


@pytest.mark.parametrize("cancelado_em", ["cal-a", "cal-b"])
def test_status_cancelado_se_qualquer_calendario_diz(
    conteudo: ModuleType, cancelado_em: str
) -> None:
    observacoes = [
        evento(
            calendario=slug,
            status_fonte="cancelado" if slug == cancelado_em else "confirmado",
        )
        for slug in ("cal-a", "cal-b")
    ]
    assert conteudo.consolidar_observacoes(observacoes).status_fonte == "cancelado"


def test_status_sem_cancelamento_vem_do_menor_slug(conteudo: ModuleType) -> None:
    consolidado = conteudo.consolidar_observacoes(
        [
            evento(calendario="cal-b", status_fonte="desconhecido"),
            evento(calendario="cal-a", status_fonte="confirmado"),
        ]
    )
    assert consolidado.status_fonte == "confirmado"


def test_consolidar_lista_vazia_e_erro(conteudo: ModuleType) -> None:
    with pytest.raises(ValueError):
        conteudo.consolidar_observacoes([])


def test_consolidar_ids_diferentes_e_erro(conteudo: ModuleType) -> None:
    with pytest.raises(ValueError):
        conteudo.consolidar_observacoes(
            [evento(id_fonte="evt-Alfa1"), evento(id_fonte="evt-Beta2", calendario="cal-b")]
        )


# --- Hash: formato e estabilidade ----------------------------------------------------------


def test_versao_do_hash_e_1(conteudo: ModuleType) -> None:
    assert conteudo.VERSAO_HASH == 1


def test_hash_e_sha256_hex_minusculo(conteudo: ModuleType) -> None:
    assert re.fullmatch(r"[0-9a-f]{64}", _hash(conteudo, evento()))


def test_hash_e_deterministico(conteudo: ModuleType) -> None:
    assert _hash(conteudo, evento()) == _hash(conteudo, evento())


def test_hash_independe_do_fuso_do_horario(conteudo: ModuleType) -> None:
    em_sp = evento()
    em_utc = evento(inicio=INICIO_PADRAO.astimezone(UTC), fim=em_sp.fim.astimezone(UTC))
    assert _hash(conteudo, em_sp) == _hash(conteudo, em_utc)


def test_hash_independe_da_ordem_do_mapa_de_descricoes(conteudo: ModuleType) -> None:
    consolidado = conteudo.consolidar_observacoes([evento()])
    um = conteudo.hash_conteudo(consolidado, {"cal-a": "A", "cal-b": "B"})
    outro = conteudo.hash_conteudo(consolidado, {"cal-b": "B", "cal-a": "A"})
    assert um == outro


def test_hash_independe_da_ordem_das_observacoes(conteudo: ModuleType) -> None:
    a = evento(calendario="cal-a", descricao="A")
    b = evento(calendario="cal-b", descricao="B")
    assert _hash(conteudo, a, b) == _hash(conteudo, b, a)


def test_hash_usa_o_mapa_recebido_e_nao_o_da_rodada(conteudo: ModuleType) -> None:
    """O repositório passa o mapa acumulado: um calendário que falhou mantém sua descrição."""
    so_a = conteudo.consolidar_observacoes([evento(calendario="cal-a", descricao="A")])
    a_e_b = conteudo.consolidar_observacoes(
        [evento(calendario="cal-a", descricao="A"), evento(calendario="cal-b", descricao="B")]
    )
    acumulado = {"cal-a": "A", "cal-b": "B"}
    assert conteudo.hash_conteudo(so_a, acumulado) == conteudo.hash_conteudo(a_e_b, acumulado)


# --- Hash: normalização ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("campo", "valor_a", "valor_b"),
    [
        ("titulo", "Meetup de IA", "  Meetup   de\tIA  "),
        ("titulo", "São Paulo AI", "São Paulo AI"),  # NFC x NFD
        ("local_texto", "Av. Paulista, 1000", " Av.  Paulista,  1000 "),
        ("local_texto", None, ""),
        ("local_texto", None, "   "),
        ("descricao", "linha 1\nlinha 2", "linha 1\r\nlinha 2"),
        ("descricao", "texto", "  texto  "),
        ("descricao", "um texto", "um   texto"),
        ("descricao", None, ""),
        ("descricao", None, " \n "),
        ("descricao", "Inscrição", "Inscrição"),
    ],
)
def test_hash_normaliza_texto(
    conteudo: ModuleType, campo: str, valor_a: str | None, valor_b: str | None
) -> None:
    assert _hash(conteudo, evento(**{campo: valor_a})) == _hash(
        conteudo, evento(**{campo: valor_b})
    )


def test_hash_arredonda_coordenadas_em_6_casas(conteudo: ModuleType) -> None:
    base = _hash(conteudo, evento(latitude=-23.5612341, longitude=-46.6567891))
    ruido = _hash(conteudo, evento(latitude=-23.5612344, longitude=-46.6567886))
    mudou = _hash(conteudo, evento(latitude=-23.561236, longitude=-46.6567891))
    assert base == ruido
    assert base != mudou


# --- Hash: o que conta e o que não conta ---------------------------------------------------


@pytest.mark.parametrize(
    "mudanca",
    [
        {"titulo": "Outro título"},
        {"inicio": INICIO_PADRAO + timedelta(minutes=1)},
        {"fim": INICIO_PADRAO + timedelta(hours=3)},
        {"fim": None},
        {"local_texto": "Rua Augusta, 10 - São Paulo"},
        {"local_texto": None, "local_url": "https://zoom.us/j/123"},
        {"local_texto": None, "endereco_oculto": True},
        {"latitude": -23.6, "longitude": -46.7},
        {"latitude": None, "longitude": None},
        {"link_inscricao": "https://luma.com/outro"},
        {"descricao": "Descrição nova"},
    ],
)
def test_hash_muda_com_campo_relevante(conteudo: ModuleType, mudanca: dict) -> None:
    assert _hash(conteudo, evento()) != _hash(conteudo, evento(**mudanca))


def test_hash_muda_com_dia_inteiro(conteudo: ModuleType) -> None:
    meia_noite = INICIO_PADRAO.replace(hour=0, minute=0)
    com_hora = evento(inicio=meia_noite, fim=meia_noite + timedelta(days=1), dia_inteiro=False)
    dia_inteiro = evento(inicio=meia_noite, fim=meia_noite + timedelta(days=1), dia_inteiro=True)
    assert _hash(conteudo, com_hora) != _hash(conteudo, dia_inteiro)


@pytest.mark.parametrize(
    "mudanca",
    [
        {"status_fonte": "cancelado"},
        {"status_fonte": "confirmado"},
        {"organizador": "Outra organização"},
        {"organizador": None},
        {"id_fonte": "evt-Outro9", "link_inscricao": "https://luma.com/evt-alfa1"},
    ],
)
def test_hash_ignora_campos_fora_do_conteudo(conteudo: ModuleType, mudanca: dict) -> None:
    assert _hash(conteudo, evento()) == _hash(conteudo, evento(**mudanca))


def test_hash_ignora_lista_de_calendarios(conteudo: ModuleType) -> None:
    """Mesmo mapa de descrições: entrar em mais um calendário não muda o hash."""
    mapa = {"cal-a": "Descrição do evento"}
    so_a = conteudo.consolidar_observacoes([evento(calendario="cal-a")])
    a_e_z = conteudo.consolidar_observacoes([evento(calendario="cal-a"), evento(calendario="z")])
    assert list(a_e_z.calendarios) == ["cal-a", "z"]
    assert conteudo.hash_conteudo(so_a, mapa) == conteudo.hash_conteudo(a_e_z, mapa)


def test_hash_muda_com_descricao_de_outro_calendario(conteudo: ModuleType) -> None:
    consolidado = conteudo.consolidar_observacoes([evento()])
    antes = conteudo.hash_conteudo(consolidado, {"cal-a": "A", "cal-b": "B"})
    depois = conteudo.hash_conteudo(consolidado, {"cal-a": "A", "cal-b": "B novo"})
    assert antes != depois


def test_hash_muda_quando_mapa_ganha_descricao_diferente(conteudo: ModuleType) -> None:
    consolidado = conteudo.consolidar_observacoes([evento()])
    antes = conteudo.hash_conteudo(consolidado, {"cal-a": "A"})
    depois = conteudo.hash_conteudo(consolidado, {"cal-a": "A", "cal-b": "B"})
    assert antes != depois


@pytest.mark.parametrize(
    "descricao_b",
    [
        "Texto do evento",
        "  Texto   do evento \n",
        "Texto do\tevento",
        "Texto do evento\r\n",
        None,
        "",
        "   ",
    ],
)
def test_calendario_novo_com_descricao_equivalente_nao_muda_hash(
    conteudo: ModuleType, descricao_b: str | None
) -> None:
    consolidado = conteudo.consolidar_observacoes([evento()])
    antes = conteudo.hash_conteudo(consolidado, {"cal-a": "Texto do evento"})
    depois = conteudo.hash_conteudo(consolidado, {"cal-a": "Texto do evento", "cal-b": descricao_b})
    assert antes == depois


def test_hash_ignora_as_chaves_de_calendario_do_mapa(conteudo: ModuleType) -> None:
    consolidado = conteudo.consolidar_observacoes([evento()])
    um = conteudo.hash_conteudo(consolidado, {"cal-a": "X", "cal-b": "Y"})
    outro = conteudo.hash_conteudo(consolidado, {"cal-a": "Y", "cal-b": "X"})
    renomeado = conteudo.hash_conteudo(consolidado, {"outro": "X", "mais-um": "Y"})
    assert um == outro == renomeado


def test_hash_usa_conjunto_de_descricoes_distintas(conteudo: ModuleType) -> None:
    consolidado = conteudo.consolidar_observacoes([evento()])
    uma = conteudo.hash_conteudo(consolidado, {"cal-a": "A"})
    repetida = conteudo.hash_conteudo(consolidado, {"cal-a": "A", "cal-b": "A", "cal-c": " A "})
    assert uma == repetida


def test_mapa_sem_descricoes_equivale_a_mapa_vazio(conteudo: ModuleType) -> None:
    consolidado = conteudo.consolidar_observacoes([evento(descricao=None)])
    assert conteudo.hash_conteudo(consolidado, {}) == conteudo.hash_conteudo(
        consolidado, {"cal-a": None, "cal-b": ""}
    )


def test_hash_independe_da_ordem_de_insercao_no_mapa(conteudo: ModuleType) -> None:
    consolidado = conteudo.consolidar_observacoes([evento()])
    itens = [("cal-a", "Zeta"), ("cal-b", "alfa"), ("cal-c", "Beta"), ("cal-d", None)]
    hashes = {
        conteudo.hash_conteudo(consolidado, dict(ordem))
        for ordem in (itens, list(reversed(itens)), [itens[2], itens[0], itens[3], itens[1]])
    }
    assert len(hashes) == 1


def test_descricoes_que_diferem_so_na_caixa_contam_como_diferentes(
    conteudo: ModuleType,
) -> None:
    consolidado = conteudo.consolidar_observacoes([evento()])
    antes = conteudo.hash_conteudo(consolidado, {"cal-a": "Texto"})
    depois = conteudo.hash_conteudo(consolidado, {"cal-a": "Texto", "cal-b": "texto"})
    assert antes != depois
