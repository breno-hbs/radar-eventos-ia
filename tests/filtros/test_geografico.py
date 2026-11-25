"""Testes do ticket F1-04 — filtro geográfico (D6; spec §4.3).

Ordem da D6: coordenadas (GEO) na Grande SP → texto do local → online. Sem evidência
conclusiva, o evento segue para o classificador como `indefinida`; só `fora` é descartado.

Usa as fixtures reais de `tests/fixtures/luma/` (lidas com `radar.fontes.luma.ler_ical`) e
eventos sintéticos montados direto em `EventoNormalizado`. Nenhum teste acessa a rede.

O módulo é importado dentro de uma fixture para que a falta da implementação apareça teste
a teste, e não como erro de coleta do arquivo inteiro.
"""

from __future__ import annotations

import importlib
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from radar.fontes.luma import ler_ical
from radar.fontes.modelos import EventoNormalizado

RAIZ = Path(__file__).resolve().parents[2]
FIXTURES = RAIZ / "tests" / "fixtures" / "luma"
SP = ZoneInfo("America/Sao_Paulo")
FEEDS = ("ai-collective", "codex-sp", "vibe-space", "womakerscode", "mug-sp", "cidade-sp")

LAT_MIN, LAT_MAX = -24.10, -23.15
LON_MIN, LON_MAX = -47.25, -45.65


# --- Módulo (import tardio) e utilitários ---------------------------------------------


@pytest.fixture
def geo() -> ModuleType:
    return importlib.import_module("radar.filtros.geografico")


def _feed(slug: str) -> list[EventoNormalizado]:
    resultado = ler_ical((FIXTURES / f"{slug}.ics").read_text(encoding="utf-8"), slug)
    assert resultado.situacao == "ok"
    return resultado.eventos


def _evento_real(slug: str, id_fonte: str) -> EventoNormalizado:
    for evento in _feed(slug):
        if evento.id_fonte == id_fonte:
            return evento
    raise AssertionError(f"{id_fonte} não está na fixture {slug}")


def _evento(**campos: Any) -> EventoNormalizado:
    """Evento sintético sem nenhuma evidência de local; `campos` sobrescreve o padrão."""
    inicio = datetime(2026, 12, 10, 19, 0, tzinfo=SP)
    base: dict[str, Any] = {
        "fonte": "luma",
        "calendario": "sintetico",
        "id_fonte": "evt-Sintetico1",
        "titulo": "Meetup de IA",
        "inicio": inicio,
        "fim": inicio + timedelta(hours=2),
        "dia_inteiro": False,
        "local_texto": None,
        "local_url": None,
        "endereco_oculto": False,
        "latitude": None,
        "longitude": None,
        "status_fonte": "desconhecido",
        "link_inscricao": None,
        "descricao": None,
        "organizador": None,
    }
    base.update(campos)
    return EventoNormalizado(**base)


def _par(localizacao: Any) -> tuple[str, str]:
    return localizacao.situacao, localizacao.evidencia


# --- Modelo Localizacao -----------------------------------------------------------------


def test_constante_da_caixa_da_grande_sp(geo: ModuleType) -> None:
    """A caixa usada no GEO é exatamente a decidida para a Grande SP."""
    caixa = geo.CAIXA_GRANDE_SP
    valores = (
        (caixa.lat_min, caixa.lat_max, caixa.lon_min, caixa.lon_max)
        if hasattr(caixa, "lat_min")
        else tuple(caixa)
    )
    assert valores == (LAT_MIN, LAT_MAX, LON_MIN, LON_MAX)


def test_localizacao_e_imutavel_e_valida_os_valores(geo: ModuleType) -> None:
    loc = geo.Localizacao(situacao="sp", evidencia="geo")
    with pytest.raises(ValidationError):
        loc.situacao = "fora"
    with pytest.raises(ValidationError):
        geo.Localizacao(situacao="brasil", evidencia="geo")
    with pytest.raises(ValidationError):
        geo.Localizacao(situacao="sp", evidencia="palpite")


# --- Fixtures reais ------------------------------------------------------------------


def test_real_evento_de_sp_com_geo_e_sp_por_geo(geo: ModuleType) -> None:
    """MUG São Paulo (mug-sp): GEO na Av. Paulista."""
    evento = _evento_real("mug-sp", "evt-AXkUVWCxURYLxoT")
    assert _par(geo.classificar_localizacao(evento)) == ("sp", "geo")


@pytest.mark.parametrize("id_fonte", ["evt-ZgHu3XgbZVYqyNI", "evt-cApGwNzoQ8GPWBv"])
def test_real_evento_de_helsinque_e_fora(geo: ModuleType, id_fonte: str) -> None:
    evento = _evento_real("ai-collective", id_fonte)
    loc = geo.classificar_localizacao(evento)
    assert loc.situacao == "fora"
    assert loc.evidencia in {"geo", "texto"}


def test_real_evento_do_vietna_e_fora(geo: ModuleType) -> None:
    evento = _evento_real("ai-collective", "evt-3YhAM7t5lwNKDen")
    loc = geo.classificar_localizacao(evento)
    assert loc.situacao == "fora"
    assert loc.evidencia in {"geo", "texto"}


def test_real_agentcon_rio_e_fora(geo: ModuleType) -> None:
    """AgentCon Rio (womakerscode): GEO na Barra da Tijuca, fora da caixa."""
    evento = _evento_real("womakerscode", "evt-5Q4i3nTsHPUfZaR")
    loc = geo.classificar_localizacao(evento)
    assert loc.situacao == "fora"
    assert loc.evidencia in {"geo", "texto"}


def test_real_endereco_oculto_com_geo_em_sp_e_sp_por_geo(geo: ModuleType) -> None:
    """Devin Meetup (cidade-sp): endereço oculto, mas a Luma manda GEO aproximado."""
    evento = _evento_real("cidade-sp", "evt-YEkDcmJq3cWyjK5")
    assert evento.endereco_oculto
    assert _par(geo.classificar_localizacao(evento)) == ("sp", "geo")


def test_real_endereco_oculto_sem_geo_e_indefinida_nunca_online(geo: ModuleType) -> None:
    """Community Town Hall (ai-collective): endereço oculto, sem GEO e sem texto."""
    evento = _evento_real("ai-collective", "evt-lL0WgU68uQsc3nv")
    assert evento.endereco_oculto and evento.latitude is None
    assert _par(geo.classificar_localizacao(evento)) == ("indefinida", "nenhuma")


@pytest.mark.parametrize(
    "id_fonte", ["calev-5WU1y0W8U0qrCmC", "calev-y3dCFGg2S2pwz8k", "calev-iDQWyt8UzBSme2e"]
)
def test_real_calev_com_url_externa_sem_geo_e_indefinida(geo: ModuleType, id_fonte: str) -> None:
    """Link do partiful no LOCATION não é evidência de evento online."""
    evento = _evento_real("ai-collective", id_fonte)
    assert evento.local_url is not None and evento.latitude is None
    assert _par(geo.classificar_localizacao(evento)) == ("indefinida", "nenhuma")


def test_real_texto_ambiguo_fiap_paulista_sem_geo_e_indefinida(geo: ModuleType) -> None:
    """ "FIAP - Paulista - 1° andar": "Paulista" sozinho não basta para concluir SP."""
    evento = _evento_real("womakerscode", "evt-LYJDk4jOEadCy2q")
    assert evento.latitude is None
    assert _par(geo.classificar_localizacao(evento)) == ("indefinida", "nenhuma")


def test_real_florianopolis_sc_sem_geo_e_fora_por_texto(geo: ModuleType) -> None:
    evento = _evento_real("vibe-space", "evt-0DMyzR6x4RRBRvd")
    assert evento.latitude is None
    assert _par(geo.classificar_localizacao(evento)) == ("fora", "texto")


@pytest.mark.parametrize("id_fonte", ["evt-x4ncK2Uctb50x0j", "evt-mszicJ4dJZzwLdm"])
def test_real_local_online_sem_geo_e_online_por_texto(geo: ModuleType, id_fonte: str) -> None:
    """vibe-space: LOCATION "Online" e "Online - Asynchronous Judging"."""
    evento = _evento_real("vibe-space", id_fonte)
    assert _par(geo.classificar_localizacao(evento)) == ("online", "online_texto")


@pytest.mark.parametrize(
    ("slug", "id_fonte"),
    [
        ("vibe-space", "evt-ukQn4ZyZl75XtRT"),  # "..., San Francisco" (sem país nem UF)
        ("ai-collective", "evt-gOzAOVTeqlx2kcj"),  # "..., Stamford (Harbor Point)"
    ],
)
def test_real_texto_sem_pais_nem_uf_e_indefinida(geo: ModuleType, slug: str, id_fonte: str) -> None:
    """Texto que não cita país, UF, CEP nem São Paulo passa adiante para o classificador."""
    evento = _evento_real(slug, id_fonte)
    assert evento.latitude is None
    assert _par(geo.classificar_localizacao(evento)) == ("indefinida", "nenhuma")


# --- GEO sintético: limites da caixa e prioridade ----------------------------------------


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [
        (LAT_MIN, LON_MIN),
        (LAT_MIN, LON_MAX),
        (LAT_MAX, LON_MIN),
        (LAT_MAX, LON_MAX),
        (-23.55, -46.63),  # centro de SP
    ],
)
def test_geo_dentro_ou_na_borda_da_caixa_e_sp(
    geo: ModuleType, latitude: float, longitude: float
) -> None:
    evento = _evento(latitude=latitude, longitude=longitude)
    assert _par(geo.classificar_localizacao(evento)) == ("sp", "geo")


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [
        (LAT_MIN - 0.0001, -46.63),
        (LAT_MAX + 0.0001, -46.63),
        (-23.55, LON_MIN - 0.0001),
        (-23.55, LON_MAX + 0.0001),
        (-22.9068, -43.1729),  # Rio de Janeiro
        (-22.9056, -47.0608),  # Campinas
        (60.1699, 24.9384),  # Helsinque
        (23.55, 46.63),  # sinais trocados
    ],
)
def test_geo_fora_da_caixa_e_fora(geo: ModuleType, latitude: float, longitude: float) -> None:
    evento = _evento(latitude=latitude, longitude=longitude)
    assert _par(geo.classificar_localizacao(evento)) == ("fora", "geo")


def test_geo_tem_prioridade_sobre_texto(geo: ModuleType) -> None:
    evento = _evento(
        latitude=-23.55, longitude=-46.63, local_texto="Unidade X, Rio de Janeiro - RJ, 22640-000"
    )
    assert _par(geo.classificar_localizacao(evento)) == ("sp", "geo")


def test_geo_fora_tem_prioridade_sobre_texto_online(geo: ModuleType) -> None:
    evento = _evento(latitude=60.1699, longitude=24.9384, local_texto="Online")
    assert _par(geo.classificar_localizacao(evento)) == ("fora", "geo")


def test_geo_tem_prioridade_sobre_url_online(geo: ModuleType) -> None:
    evento = _evento(latitude=-23.55, longitude=-46.63, local_url="https://zoom.us/j/123")
    assert _par(geo.classificar_localizacao(evento)) == ("sp", "geo")


def test_texto_conclusivo_tem_prioridade_sobre_url_online(geo: ModuleType) -> None:
    evento = _evento(
        local_texto="Unidade X, Rio de Janeiro - RJ, 22640-000", local_url="https://zoom.us/j/1"
    )
    assert _par(geo.classificar_localizacao(evento)) == ("fora", "texto")


# --- Texto sintético -------------------------------------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        "Cubo Itaú, Alameda Vicente Pinzon, 54 - Vila Olímpia, São Paulo - SP, 04547-130, Brazil",
        "Auditório, São Paulo",
        "SÃO PAULO",
        "Rua Augusta, 100, Sao Paulo",
        "Espaço Y, sao paulo, Brasil",
        "Rua X, 100 - 01311-000",  # CEP com hífen
        "Rua X, 100 - CEP 01311000",  # CEP sem hífen
        "Rua X, 100 - 01000-000",  # limite inferior
        "Rua X, 100 - 09999-999",  # limite superior
        "Rua Y, 50 - Centro, Santo André - SP",
        "Rua Y, 50, Osasco, SP, 06000-000",
    ],
)
def test_texto_de_sp_e_sp_por_texto(geo: ModuleType, texto: str) -> None:
    evento = _evento(local_texto=texto)
    assert _par(geo.classificar_localizacao(evento)) == ("sp", "texto")


@pytest.mark.parametrize(
    "texto",
    [
        "Unidade Ibmec, Av. Armando Lombardi, 940 - Rio de Janeiro - RJ, 22640-000",
        "Centro de Convenções, Belo Horizonte, MG, Brasil",
        "Rua das Flores, 10 - Curitiba - PR 80000-000",
        "Founder Haus, Florianópolis - SC, 88053-370",
        "Maria 01, Lapinlahdenkatu 16, 00180 Helsinki, Finland",
        "Nina Next Space, Hồ Chí Minh, Vietnam",
        "Pebblebed, 1417 15th St, San Francisco, CA 94103, USA",
        "1 Main St, Springfield, IL 62701, United States",
    ],
)
def test_texto_de_outra_uf_ou_pais_e_fora_por_texto(geo: ModuleType, texto: str) -> None:
    evento = _evento(local_texto=texto)
    assert _par(geo.classificar_localizacao(evento)) == ("fora", "texto")


@pytest.mark.parametrize(
    "texto",
    [
        "FIAP - Paulista - 1° andar",
        "Av. Paulista, 1106",
        "Rua X, 100 - 10000-000",  # CEP fora da faixa de SP
        "Rua X, 100 - 00999-999",  # abaixo da faixa
        "Algum Lugar, Brazil",
        "Algum Lugar, Brasil",
        "Coworking - Spaces Paulista",  # "- SP" só conta como UF isolada
        "Sala 3",
    ],
)
def test_texto_ambiguo_e_indefinida(geo: ModuleType, texto: str) -> None:
    evento = _evento(local_texto=texto)
    assert _par(geo.classificar_localizacao(evento)) == ("indefinida", "nenhuma")


# --- Online ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://zoom.us/j/123456",
        "https://us02web.zoom.us/j/123456?pwd=abc",
        "https://meet.google.com/abc-defg-hij",
        "https://teams.microsoft.com/l/meetup-join/x",
        "https://teams.live.com/meet/123",
        "https://www.youtube.com/watch?v=abc",
        "https://youtube.com/live/abc",
        "https://youtu.be/abc",
        "https://www.twitch.tv/canal",
        "https://streamyard.com/abc",
        "https://empresa.webex.com/meet/x",
        "https://discord.gg/abc",
        "https://discord.com/invite/abc",
        "https://ZOOM.US/j/123",
    ],
)
def test_url_de_plataforma_online_e_online(geo: ModuleType, url: str) -> None:
    evento = _evento(local_url=url)
    assert _par(geo.classificar_localizacao(evento)) == ("online", "online_url")


@pytest.mark.parametrize(
    "url",
    [
        "https://zoom.us.evil.example/j/1",
        "https://notzoom.us/j/1",
        "https://evil.example/zoom.us",
        "https://evil.example/?u=meet.google.com",
        "https://zoom.us@evil.example/j/1",
        "https://meet.google.com.evil.example/x",
        "https://fakeyoutube.com/watch",
        "https://partiful.com/e/abc",
        "https://www.eventbrite.com/e/abc",
        "https://lu.ma/abc",
        "https://luma.com/abc",
    ],
)
def test_url_que_nao_e_de_plataforma_online_nao_e_online(geo: ModuleType, url: str) -> None:
    evento = _evento(local_url=url)
    assert _par(geo.classificar_localizacao(evento)) == ("indefinida", "nenhuma")


@pytest.mark.parametrize(
    "texto",
    [
        "Online",
        "online",
        "ONLINE",
        "Virtual",
        "Remoto",
        "Remote",
        "Livestream",
        "Transmissão online",
        "Transmissao Online",
        "Evento online via Zoom",
        "Online - Asynchronous Judging",
    ],
)
def test_texto_online_e_online_por_texto(geo: ModuleType, texto: str) -> None:
    evento = _evento(local_texto=texto)
    assert _par(geo.classificar_localizacao(evento)) == ("online", "online_texto")


@pytest.mark.parametrize("texto", ["Onlinetech Hub", "Virtualize Coworking"])
def test_online_so_conta_como_palavra_inteira(geo: ModuleType, texto: str) -> None:
    evento = _evento(local_texto=texto)
    assert _par(geo.classificar_localizacao(evento)) == ("indefinida", "nenhuma")


def test_titulo_e_descricao_nao_sao_evidencia_de_local(geo: ModuleType) -> None:
    """Só os campos de local contam; título e descrição ficam para o classificador."""
    evento = _evento(
        titulo="ONLINE! Meetup São Paulo", descricao="Evento online em São Paulo - SP, 01311-000"
    )
    assert _par(geo.classificar_localizacao(evento)) == ("indefinida", "nenhuma")


def test_endereco_oculto_sem_geo_e_indefinida(geo: ModuleType) -> None:
    evento = _evento(endereco_oculto=True)
    assert _par(geo.classificar_localizacao(evento)) == ("indefinida", "nenhuma")


def test_sem_nenhuma_evidencia_e_indefinida(geo: ModuleType) -> None:
    assert _par(geo.classificar_localizacao(_evento())) == ("indefinida", "nenhuma")


# --- filtrar_por_localizacao -----------------------------------------------------------


def test_filtro_descarta_so_fora_e_mantem_a_ordem(geo: ModuleType) -> None:
    eventos = [
        _evento(id_fonte="evt-Sp1", latitude=-23.55, longitude=-46.63),
        _evento(id_fonte="evt-Fora1", latitude=60.1699, longitude=24.9384),
        _evento(id_fonte="evt-Online1", local_url="https://zoom.us/j/1"),
        _evento(id_fonte="evt-Indef1"),
        _evento(id_fonte="evt-Fora2", local_texto="Lapinlahdenkatu 16, Helsinki, Finland"),
        _evento(id_fonte="calev-Indef2", local_url="https://partiful.com/e/x"),
    ]
    resultado = geo.filtrar_por_localizacao(eventos)

    assert [a.evento.id_fonte for a in resultado.aprovados] == [
        "evt-Sp1",
        "evt-Online1",
        "evt-Indef1",
        "calev-Indef2",
    ]
    assert [a.localizacao.situacao for a in resultado.aprovados] == [
        "sp",
        "online",
        "indefinida",
        "indefinida",
    ]
    assert resultado.aprovados[0].evento == eventos[0]
    assert [(d.id_fonte, d.calendario, d.motivo) for d in resultado.descartados] == [
        ("evt-Fora1", "sintetico", "fora_de_sp"),
        ("evt-Fora2", "sintetico", "fora_de_sp"),
    ]


def test_filtro_lista_vazia(geo: ModuleType) -> None:
    resultado = geo.filtrar_por_localizacao([])
    assert resultado.aprovados == []
    assert resultado.descartados == []


def test_filtro_nao_descarta_ninguem_so_por_falta_de_geo(geo: ModuleType) -> None:
    """Critério de aceite: nenhum evento sem GEO e sem texto conclusivo é descartado."""
    eventos = [
        _evento(id_fonte="evt-Nada1"),
        _evento(id_fonte="evt-Oculto1", endereco_oculto=True),
        _evento(id_fonte="calev-Externo1", local_url="https://www.eventbrite.com/e/x"),
        _evento(id_fonte="evt-Ambiguo1", local_texto="FIAP - Paulista - 1° andar"),
    ]
    resultado = geo.filtrar_por_localizacao(eventos)
    assert resultado.descartados == []
    assert len(resultado.aprovados) == len(eventos)


@pytest.mark.parametrize("slug", FEEDS)
def test_fixtures_aprovados_mais_descartados_e_o_total(geo: ModuleType, slug: str) -> None:
    eventos = _feed(slug)
    resultado = geo.filtrar_por_localizacao(eventos)
    assert len(resultado.aprovados) + len(resultado.descartados) == len(eventos)
    ids = {a.evento.id_fonte for a in resultado.aprovados} | {
        d.id_fonte for d in resultado.descartados
    }
    assert ids == {e.id_fonte for e in eventos}
    assert all(d.calendario == slug and d.motivo == "fora_de_sp" for d in resultado.descartados)


@pytest.mark.parametrize("slug", FEEDS)
def test_fixtures_descarte_sem_geo_so_com_texto_conclusivo(geo: ModuleType, slug: str) -> None:
    """Nas fixtures reais, evento sem GEO só sai do fluxo se o texto diz que é fora de SP."""
    eventos = _feed(slug)
    descartados = {d.id_fonte for d in geo.filtrar_por_localizacao(eventos).descartados}
    for evento in eventos:
        if evento.latitude is None and evento.id_fonte in descartados:
            assert _par(geo.classificar_localizacao(evento)) == ("fora", "texto")
        if evento.latitude is None and evento.local_texto is None:
            assert evento.id_fonte not in descartados


def test_fixture_cidade_sp_nao_tem_nenhum_fora(geo: ModuleType) -> None:
    eventos = _feed("cidade-sp")
    resultado = geo.filtrar_por_localizacao(eventos)
    assert resultado.descartados == []
    assert len(resultado.aprovados) == len(eventos) == 12
    assert {a.localizacao.situacao for a in resultado.aprovados} == {"sp"}


@pytest.mark.parametrize("slug", ["mug-sp", "codex-sp"])
def test_fixtures_de_organizadores_de_sp_sao_aprovadas(geo: ModuleType, slug: str) -> None:
    resultado = geo.filtrar_por_localizacao(_feed(slug))
    assert resultado.descartados == []
    assert [_par(a.localizacao) for a in resultado.aprovados] == [("sp", "geo")]


def test_fixture_ai_collective_descarta_helsinque_e_vietna(geo: ModuleType) -> None:
    resultado = geo.filtrar_por_localizacao(_feed("ai-collective"))
    descartados = {d.id_fonte for d in resultado.descartados}
    assert {"evt-ZgHu3XgbZVYqyNI", "evt-cApGwNzoQ8GPWBv", "evt-3YhAM7t5lwNKDen"} <= descartados
    aprovados = {a.evento.id_fonte for a in resultado.aprovados}
    assert "evt-lL0WgU68uQsc3nv" in aprovados  # oculto, sem GEO
    assert "calev-5WU1y0W8U0qrCmC" in aprovados  # partiful, sem GEO


def test_fixture_womakerscode_descarta_so_o_rio(geo: ModuleType) -> None:
    resultado = geo.filtrar_por_localizacao(_feed("womakerscode"))
    assert [d.id_fonte for d in resultado.descartados] == ["evt-5Q4i3nTsHPUfZaR"]


# --- Precedência dentro da regra de texto (decisão do coordenador no F1-04) -------------
# Ordem: UF explícita (", SP"/"- SP" → sp; outra UF → fora) → país estrangeiro no fim →
# CEP da Grande SP → "sao paulo" no texto.


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Rua São Paulo, 100 - Belo Horizonte - MG, 30000-000, Brazil", ("fora", "texto")),
        ("Av. Paulista, 1000 - São Paulo - SP, 01310-100, Brazil", ("sp", "texto")),
        ("Rua São Paulo, 10, Lisboa, Portugal", ("fora", "texto")),
        ("Rua Belo Horizonte, 10 - 01310-100, Finland", ("fora", "texto")),
        ("Rua do Rio, 10 - Centro - SP, 22640-000", ("sp", "texto")),
    ],
)
def test_precedencia_dentro_da_regra_de_texto(
    geo: ModuleType, texto: str, esperado: tuple[str, str]
) -> None:
    evento = _evento(local_texto=texto)
    assert _par(geo.classificar_localizacao(evento)) == esperado


@pytest.mark.parametrize("texto", ["Unidade Ibmec, Rio de Janeiro - RJ", "Centro, Recife, PE"])
def test_outra_uf_no_fim_do_texto_e_fora(geo: ModuleType, texto: str) -> None:
    evento = _evento(local_texto=texto)
    assert _par(geo.classificar_localizacao(evento)) == ("fora", "texto")


def test_sp_no_fim_do_texto_e_sp(geo: ModuleType) -> None:
    evento = _evento(local_texto="Rua Y, 50 - Centro, Santo André - SP")
    assert _par(geo.classificar_localizacao(evento)) == ("sp", "texto")


def test_campinas_sp_e_sp_por_texto_limitacao_aceita(geo: ModuleType) -> None:
    # Limitação aceita (F1-04): a regra de texto olha só a UF e não diferencia municípios
    # paulistas fora da Grande SP. Campinas, sem GEO, passa como "sp"; quem tem GEO é
    # julgado pela caixa (ver o caso de Campinas em test_geo_fora_da_caixa_e_fora).
    evento = _evento(local_texto="Campinas, SP")
    assert _par(geo.classificar_localizacao(evento)) == ("sp", "texto")


# --- Revisão do F1-04: falsos negativos (evento de SP descartado como "fora") -----------
# Descartar um evento de SP é o erro mais grave do produto; na dúvida, o texto fica
# inconclusivo e o evento segue para o classificador como "indefinida".


@pytest.mark.parametrize(
    "texto",
    [
        "Bloco - AM 12",
        "Centro de Convenções - AL 3",
        "Rua dos Pinheiros, 870 - Pinheiros - CE 1",
        "Escritório XP - AL, 2º andar",
        "Auditório - PA, Prédio A",
        "Sala - TO, 3o andar",
        "Vivo - RS 2",
        "Unidade Ibmec, Rio de Janeiro - rj",  # sigla só conta em maiúsculas
    ],
)
def test_sigla_de_uf_seguida_de_outra_coisa_nao_conta(geo: ModuleType, texto: str) -> None:
    """A sigla só é UF se vier seguida de fim do texto, CEP ou ", Brasil"/", Brazil"."""
    evento = _evento(local_texto=texto)
    assert _par(geo.classificar_localizacao(evento)) == ("indefinida", "nenhuma")


@pytest.mark.parametrize(
    "texto",
    [
        "Unidade Ibmec, Rio de Janeiro - RJ",
        "Founder Haus, Florianópolis - SC, 88053-370",
        "Rua das Flores, 10 - Curitiba - PR 80000-000",
        "Rua das Flores, 10 - Curitiba - PR, 80000000",
        "Centro, Belo Horizonte - MG, 30000-000, Brazil",
        "Centro, Belo Horizonte - MG, Brasil",
        "Centro, Recife, PE",
    ],
)
def test_sigla_de_uf_bem_delimitada_continua_fora(geo: ModuleType, texto: str) -> None:
    evento = _evento(local_texto=texto)
    assert _par(geo.classificar_localizacao(evento)) == ("fora", "texto")


@pytest.mark.parametrize(
    "texto",
    ["Hub - ES, 05422-001", "Rua X, 10 - RJ, 01310-100", "Espaço Y - MG 04547130"],
)
def test_uf_de_outro_estado_com_cep_da_grande_sp_e_indefinida(geo: ModuleType, texto: str) -> None:
    """Conflito entre UF e CEP nunca descarta."""
    evento = _evento(local_texto=texto)
    assert _par(geo.classificar_localizacao(evento)) == ("indefinida", "nenhuma")


def test_uf_sp_com_cep_de_fora_continua_sp(geo: ModuleType) -> None:
    evento = _evento(local_texto="Rua do Rio, 10 - Centro - SP, 22640-000")
    assert _par(geo.classificar_localizacao(evento)) == ("sp", "texto")


@pytest.mark.parametrize(
    "texto",
    [
        "Bar - Canada",
        "Restaurante - India",
        "Casa Japão - India",
        "Barra Funda - Chile",
        "Av. Faria Lima - Uk",
        "WeWork - Paulista, US",
        "Rua X - Moema - Australia",
        "Rua X, 10, uk",
        "Rua X, 10, finland",  # país só conta com inicial maiúscula ou tudo em maiúsculas
    ],
)
def test_pais_fora_do_padrao_nao_conta(geo: ModuleType, texto: str) -> None:
    """País só conta como último componente depois de vírgula, por nome completo."""
    evento = _evento(local_texto=texto)
    assert _par(geo.classificar_localizacao(evento)) == ("indefinida", "nenhuma")


@pytest.mark.parametrize(
    "texto",
    [
        "Maria 01, Lapinlahdenkatu 16, 00180 Helsinki, Finland",
        "Maria 01, Lapinlahdenkatu 16, 00180 Helsinki, FINLAND",
        "Nina Next Space, Hồ Chí Minh, Vietnam",
        "Pebblebed, 1417 15th St, San Francisco, CA 94103, USA",
        "1 Main St, Springfield, IL 62701, United States",
        "Rua São Paulo, 10, Lisboa, Portugal",
    ],
)
def test_pais_como_ultimo_componente_depois_de_virgula_continua_fora(
    geo: ModuleType, texto: str
) -> None:
    evento = _evento(local_texto=texto)
    assert _par(geo.classificar_localizacao(evento)) == ("fora", "texto")


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.com\\@zoom.us/j/1",
        "https://zoom.us\\j\\1",
        "https://user:senha@zoom.us/j/1",
        "https://user@us02web.zoom.us/j/1",
        "https://zoom.us/j/1\x01x",
        "https://zoom.us/j/1\x7fx",
    ],
)
def test_url_com_barra_invertida_controle_ou_userinfo_nao_e_online(
    geo: ModuleType, url: str
) -> None:
    evento = _evento(local_url=url)
    assert _par(geo.classificar_localizacao(evento)) == ("indefinida", "nenhuma")


@pytest.mark.parametrize("url", ["https://zoom.us/j/1 x", "https://zoom.us/j/1\tx"])
def test_url_com_espaco_nao_e_online(geo: ModuleType, url: str) -> None:
    """O modelo já recusa espaço em `local_url`; o filtro também não confia nisso."""
    base = _evento()
    evento = EventoNormalizado.model_construct(**{**base.model_dump(), "local_url": url})
    assert _par(geo.classificar_localizacao(evento)) == ("indefinida", "nenhuma")


def _na_caixa(evento: EventoNormalizado) -> bool:
    return (
        evento.latitude is not None
        and evento.longitude is not None
        and LAT_MIN <= evento.latitude <= LAT_MAX
        and LON_MIN <= evento.longitude <= LON_MAX
    )


@pytest.mark.parametrize("slug", FEEDS)
def test_fixtures_texto_de_evento_em_sp_nunca_diz_fora(geo: ModuleType, slug: str) -> None:
    """Tirando o GEO de cada evento real dentro da caixa, o texto sozinho nunca diz "fora"."""
    for evento in _feed(slug):
        if _na_caixa(evento) and evento.local_texto is not None:
            sem_geo = evento.model_copy(update={"latitude": None, "longitude": None})
            loc = geo.classificar_localizacao(sem_geo)
            assert loc.situacao != "fora", (evento.id_fonte, evento.local_texto)


def test_fixtures_classificacao_sem_geo_conclusiva_e_estavel(geo: ModuleType) -> None:
    """Retrato das fixtures: só estes eventos sem GEO têm local conclusivo."""
    conclusivos = {
        (e.id_fonte, *_par(geo.classificar_localizacao(e)))
        for slug in FEEDS
        for e in _feed(slug)
        if e.latitude is None and geo.classificar_localizacao(e).situacao != "indefinida"
    }
    assert conclusivos == {
        ("evt-x4ncK2Uctb50x0j", "online", "online_texto"),
        ("evt-mszicJ4dJZzwLdm", "online", "online_texto"),
        ("evt-meUHxu9No6SaHj2", "online", "online_texto"),
        ("evt-XyNGTqlrZATRMci", "online", "online_texto"),
        ("evt-XkEF8yugtt9ByVn", "online", "online_texto"),
        ("evt-0DMyzR6x4RRBRvd", "fora", "texto"),
    }


@pytest.mark.parametrize("slug", FEEDS)
def test_fixtures_com_geo_seguem_so_a_caixa(geo: ModuleType, slug: str) -> None:
    for evento in _feed(slug):
        if evento.latitude is not None:
            esperado = "sp" if _na_caixa(evento) else "fora"
            assert _par(geo.classificar_localizacao(evento)) == (esperado, "geo")
