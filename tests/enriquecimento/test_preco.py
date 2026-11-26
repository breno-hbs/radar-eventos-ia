"""Testes do F1-08 — busca das páginas de preço (httpx.MockTransport + Postgres de teste).

Assinaturas fixadas (radar.enriquecimento.preco):
- `ORIGEM = "https://luma.com"`; `MAX_BYTES_PAGINA = 2 * 1024 * 1024` (lido em tempo de
  chamada; monkeypatch muda o corte);
- `ResumoEnriquecimento`: paginas_buscadas: int, gravados: list[tuple[int, str]] (evento_id,
  estado), erros: list[tuple[int, str]] (evento_id, motivo), robots_bloqueado: bool,
  restantes: bool;
- o limite (`config.limite_paginas_enriquecimento_por_execucao`) conta EVENTOS processados
  (cada um grava uma linha), inclusive bloqueado_robots e link_invalido sem requisição;
- `enriquecer_precos(sessao, cliente: httpx.Client, config, agora, rodada,
  slugs_comunidade_ia: frozenset[str]) -> ResumoEnriquecimento`. A chave da classificação é a
  atual (prompt.VERSAO_PROMPT, modelos.MODELO, modelos.VERSAO_SCHEMA). Só flush.
- `erros` lista os resultados com motivo (preco_desconhecido); `gravados` lista TODAS as
  linhas gravadas (inclusive as de erro); `paginas_buscadas` conta os GET de página feitos
  (inclusive os que falharam), nunca o robots.txt.
"""

from __future__ import annotations

import importlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from types import ModuleType
from typing import Any

import httpx
import pytest
from banco.conftest import linhas
from classificacao.fabricas import AGORA
from sqlalchemy.orm import Session

from radar.config import Configuracao

from .dados_banco import classificar, evento_ia, inserir_evento
from .fabricas import fixture

MOTIVOS = {
    "bloqueado_robots",
    "link_invalido",
    "rede",
    "redirecionamento",
    "status_http",
    "pagina_grande",
    "sem_json_ld",
    "json_ld_invalido",
    "sem_offers",
    "oferta_invalida",
}
ROBOTS_LIVRE = "User-agent: *\nAllow: /\n"


@pytest.fixture
def preco() -> ModuleType:
    return importlib.import_module("radar.enriquecimento.preco")


Resposta = httpx.Response | Exception | Callable[[httpx.Request], httpx.Response]


@dataclass
class Rede:
    """Transporte falso: robots e páginas por caminho; grava todas as requisições."""

    robots: Resposta = field(default_factory=lambda: httpx.Response(200, text=ROBOTS_LIVRE))
    paginas: dict[str, Resposta] = field(default_factory=dict)
    padrao: Resposta = field(
        default_factory=lambda: httpx.Response(200, text=fixture("gratuito_documento.html"))
    )
    requisicoes: list[httpx.Request] = field(default_factory=list)

    def __call__(self, requisicao: httpx.Request) -> httpx.Response:
        self.requisicoes.append(requisicao)
        if requisicao.url.path == "/robots.txt":
            resposta = self.robots
        else:
            resposta = self.paginas.get(requisicao.url.path, self.padrao)
        if isinstance(resposta, Exception):
            raise resposta
        if callable(resposta) and not isinstance(resposta, httpx.Response):
            return resposta(requisicao)
        # resposta nova a cada requisição (o cliente consome e amarra o stream)
        return httpx.Response(
            resposta.status_code, headers=resposta.headers, content=resposta.content
        )

    @property
    def caminhos_de_pagina(self) -> list[str]:
        return [r.url.path for r in self.requisicoes if r.url.path != "/robots.txt"]

    @property
    def leituras_de_robots(self) -> int:
        return sum(1 for r in self.requisicoes if r.url.path == "/robots.txt")


def _cliente(rede: Rede) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(rede), follow_redirects=False)


def _rodar(
    preco: ModuleType,
    sessao: Session,
    rede: Rede,
    *,
    limite: int = 10,
    agora: datetime = AGORA,
    slugs: frozenset[str] = frozenset(),
) -> Any:
    with _cliente(rede) as cliente:
        return preco.enriquecer_precos(
            sessao,
            cliente,
            Configuracao(limite_paginas_enriquecimento_por_execucao=limite),
            agora,
            uuid.uuid4(),
            slugs,
        )


def _linhas(sessao: Session) -> list[dict[str, Any]]:
    return linhas(sessao, "SELECT * FROM enriquecimentos ORDER BY id")


def _caminho(evento: Any) -> str:
    return httpx.URL(evento.link_inscricao).path


# --- Caminho feliz -------------------------------------------------------------------------


def test_grava_o_preco_da_pagina(preco: ModuleType, sessao: Session) -> None:
    evento = evento_ia(sessao)
    rede = Rede(paginas={_caminho(evento): httpx.Response(200, text=fixture("pago_brl.html"))})
    rodada = uuid.uuid4()
    with _cliente(rede) as cliente:
        resumo = preco.enriquecer_precos(
            sessao,
            cliente,
            Configuracao(limite_paginas_enriquecimento_por_execucao=10),
            AGORA,
            rodada,
            frozenset(),
        )
    assert resumo.paginas_buscadas == 1
    assert resumo.gravados == [(evento.id, "pago_com_valor")]
    assert resumo.erros == []
    assert resumo.robots_bloqueado is False
    assert resumo.restantes is False
    [linha] = _linhas(sessao)
    assert linha["evento_id"] == evento.id
    assert linha["hash_conteudo"] == evento.hash_conteudo
    assert linha["rodada"] == rodada
    assert linha["criado_em"] == AGORA
    assert (linha["estado_preco"], linha["valor_centavos"], linha["moeda"]) == (
        "pago_com_valor",
        5000,
        "BRL",
    )
    assert linha["motivo"] is None
    assert [str(r.url) for r in rede.requisicoes] == [
        "https://luma.com/robots.txt",
        evento.link_inscricao,
    ]


def test_limite_de_paginas_e_restantes(preco: ModuleType, sessao: Session) -> None:
    eventos = [evento_ia(sessao, dias=12 - i) for i in range(12)]  # inseridos fora de ordem
    rede = Rede()
    resumo = _rodar(preco, sessao, rede, limite=10)
    assert resumo.paginas_buscadas == 10
    assert resumo.restantes is True
    assert rede.leituras_de_robots == 1
    esperados = sorted(eventos, key=lambda e: e.inicio)[:10]
    assert rede.caminhos_de_pagina == [_caminho(e) for e in esperados]
    assert len(_linhas(sessao)) == 10
    seguinte = _rodar(preco, sessao, rede, limite=10)
    assert seguinte.paginas_buscadas == 2
    assert seguinte.restantes is False


def test_limite_conta_eventos_processados_inclusive_sem_requisicao(
    preco: ModuleType, sessao: Session
) -> None:
    invalido_1 = evento_ia(sessao, dias=0, link_inscricao="https://evil.com/a")
    invalido_2 = evento_ia(sessao, dias=1, link_inscricao="https://luma.com/event")
    valido_1 = evento_ia(sessao, dias=2)
    evento_ia(sessao, dias=3)
    evento_ia(sessao, dias=4)
    rede = Rede()
    resumo = _rodar(preco, sessao, rede, limite=3)
    assert resumo.gravados == [
        (invalido_1.id, "preco_desconhecido"),
        (invalido_2.id, "preco_desconhecido"),
        (valido_1.id, "gratuito_confirmado"),
    ]
    assert resumo.paginas_buscadas == 1
    assert rede.caminhos_de_pagina == [_caminho(valido_1)]
    assert resumo.restantes is True
    assert len(_linhas(sessao)) == 3


def test_limite_zero_nao_faz_nada(preco: ModuleType, sessao: Session) -> None:
    evento_ia(sessao)
    rede = Rede()
    resumo = _rodar(preco, sessao, rede, limite=0)
    assert rede.requisicoes == []
    assert resumo.paginas_buscadas == 0
    assert _linhas(sessao) == []


def test_sem_candidatos_nao_le_robots(preco: ModuleType, sessao: Session) -> None:
    evento = inserir_evento(sessao)
    classificar(sessao, evento, e_ia=False)
    rede = Rede()
    resumo = _rodar(preco, sessao, rede)
    assert rede.requisicoes == []
    assert resumo.gravados == []


def test_comunidade_de_ia_e_enriquecida(preco: ModuleType, sessao: Session) -> None:
    evento = inserir_evento(sessao, calendarios=["codex-sp"])
    classificar(sessao, evento, e_ia=False)
    rede = Rede()
    resumo = _rodar(preco, sessao, rede, slugs=frozenset({"codex-sp"}))
    assert resumo.gravados == [(evento.id, "gratuito_confirmado")]


def test_nada_se_repete_ate_o_hash_mudar(preco: ModuleType, sessao: Session) -> None:
    evento = evento_ia(sessao)
    rede = Rede()
    _rodar(preco, sessao, rede)
    antes = len(rede.requisicoes)
    _rodar(preco, sessao, rede)
    assert len(rede.requisicoes) == antes
    evento.hash_conteudo = "b" * 64
    sessao.flush()
    classificar(sessao, evento)
    _rodar(preco, sessao, rede)
    assert rede.caminhos_de_pagina == [_caminho(evento), _caminho(evento)]
    assert [linha["hash_conteudo"] for linha in _linhas(sessao)] == ["a" * 64, "b" * 64]


def test_agora_sem_fuso_e_erro(preco: ModuleType, sessao: Session) -> None:
    evento_ia(sessao)
    rede = Rede()
    with pytest.raises(ValueError):
        _rodar(preco, sessao, rede, agora=datetime(2026, 11, 25, 12, 0))
    assert rede.requisicoes == []


def test_so_flush_rollback_nao_deixa_linha(preco: ModuleType, sessao: Session) -> None:
    evento_ia(sessao)
    sessao.commit()  # savepoint do teste: o evento fica
    _rodar(preco, sessao, Rede())
    assert len(_linhas(sessao)) == 1
    sessao.rollback()
    assert _linhas(sessao) == []


# --- Erros de página -----------------------------------------------------------------------


@pytest.mark.parametrize("status", [403, 429, 500])
def test_status_http_grava_e_nao_repete(preco: ModuleType, sessao: Session, status: int) -> None:
    evento = evento_ia(sessao)
    rede = Rede(padrao=httpx.Response(status, text="bloqueado"))
    resumo = _rodar(preco, sessao, rede)
    assert resumo.erros == [(evento.id, "status_http")]
    [linha] = _linhas(sessao)
    assert (linha["estado_preco"], linha["motivo"], linha["status_http"]) == (
        "preco_desconhecido",
        "status_http",
        status,
    )
    rede.requisicoes.clear()
    _rodar(preco, sessao, rede)
    assert rede.requisicoes == []


def test_redirecionamento_nao_e_seguido(preco: ModuleType, sessao: Session) -> None:
    evento = evento_ia(sessao)
    rede = Rede(padrao=httpx.Response(301, headers={"Location": "https://luma.com/outro-lugar"}))
    resumo = _rodar(preco, sessao, rede)
    assert resumo.erros == [(evento.id, "redirecionamento")]
    assert rede.caminhos_de_pagina == [_caminho(evento)]
    assert _linhas(sessao)[0]["status_http"] == 301


def test_erros_de_rede_seguem_para_os_proximos(preco: ModuleType, sessao: Session) -> None:
    um = evento_ia(sessao, dias=0)
    dois = evento_ia(sessao, dias=1)
    tres = evento_ia(sessao, dias=2)
    rede = Rede(
        paginas={
            _caminho(um): httpx.ConnectError("sem rede"),
            _caminho(dois): httpx.ReadTimeout("demorou"),
        }
    )
    resumo = _rodar(preco, sessao, rede)
    assert resumo.erros == [(um.id, "rede"), (dois.id, "rede")]
    assert resumo.gravados == [
        (um.id, "preco_desconhecido"),
        (dois.id, "preco_desconhecido"),
        (tres.id, "gratuito_confirmado"),
    ]
    assert resumo.paginas_buscadas == 3


def test_pagina_grande(preco: ModuleType, sessao: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    assert preco.MAX_BYTES_PAGINA == 2 * 1024 * 1024
    monkeypatch.setattr(preco, "MAX_BYTES_PAGINA", 200)
    evento = evento_ia(sessao)
    rede = Rede(padrao=httpx.Response(200, text=fixture("pago_brl.html")))
    resumo = _rodar(preco, sessao, rede)
    assert resumo.erros == [(evento.id, "pagina_grande")]


@pytest.mark.parametrize(
    ("arquivo", "motivo"),
    [
        ("sem_offers.html", "sem_offers"),
        ("json_ld_quebrado.html", "json_ld_invalido"),
        ("sem_json_ld.html", "sem_json_ld"),
    ],
)
def test_pagina_sem_preco(preco: ModuleType, sessao: Session, arquivo: str, motivo: str) -> None:
    evento = evento_ia(sessao)
    resumo = _rodar(preco, sessao, Rede(padrao=httpx.Response(200, text=fixture(arquivo))))
    assert resumo.erros == [(evento.id, motivo)]
    assert _linhas(sessao)[0]["estado_preco"] == "preco_desconhecido"


@pytest.mark.parametrize(
    "link",
    [
        "https://luma.com/event",
        "https://luma.com/user",
        "https://luma.com/a/b",
        "https://luma.com/x?y=1",
        "http://luma.com/x",
        "https://evil.com/x",
        "https://luma.com.evil.com/x",
        "https://user:senha@luma.com/x",
        "https://luma.com/",
        " https://luma.com/x",
        "https://luma.com/x ",
        "https://luma.com/x\n",
        "https://luma.com/x\t",
        "\thttps://luma.com/x",
        "https://luma.com/x\r\n",
        "https://luma.com/x\u00a0",
        "https://luma.com/x\u200b",
        "https://luma.com/x\x1b",  # controle (NUL não cabe no Postgres)
        "https://luma.com/abc?",
        "https://luma.com/abc#",
    ],
)
def test_link_invalido_nao_faz_requisicao(preco: ModuleType, sessao: Session, link: str) -> None:
    evento = evento_ia(sessao, link_inscricao=link)
    rede = Rede()
    resumo = _rodar(preco, sessao, rede)
    assert resumo.erros == [(evento.id, "link_invalido")]
    assert rede.caminhos_de_pagina == []
    assert _linhas(sessao)[0]["motivo"] == "link_invalido"


def test_preco_absurdo_grava_oferta_invalida(preco: ModuleType, sessao: Session) -> None:
    from .fabricas import oferta, pagina_com

    evento = evento_ia(sessao)
    rede = Rede(padrao=httpx.Response(200, text=pagina_com([oferta(1e20, "BRL")])))
    resumo = _rodar(preco, sessao, rede)
    sessao.flush()
    assert resumo.erros == [(evento.id, "oferta_invalida")]
    [linha] = _linhas(sessao)
    assert (linha["estado_preco"], linha["motivo"], linha["valor_centavos"]) == (
        "preco_desconhecido",
        "oferta_invalida",
        None,
    )


def test_corte_de_tamanho_durante_a_leitura(
    preco: ModuleType, sessao: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(preco, "MAX_BYTES_PAGINA", 1_000)
    evento = evento_ia(sessao)
    entregues: list[int] = []

    def pedacos() -> Any:
        for indice in range(1_000):
            entregues.append(indice)
            yield b"x" * 100

    def responder(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=pedacos())

    rede = Rede(padrao=responder)
    resumo = _rodar(preco, sessao, rede)
    assert resumo.erros == [(evento.id, "pagina_grande")]
    assert len(entregues) < 100  # parou cedo, não leu os 100 KB


def test_get_da_pagina_pede_sem_compressao(preco: ModuleType, sessao: Session) -> None:
    evento_ia(sessao)
    rede = Rede()
    _rodar(preco, sessao, rede)
    [pagina] = [r for r in rede.requisicoes if r.url.path != "/robots.txt"]
    assert pagina.headers["Accept-Encoding"] == "identity"


# --- robots.txt ----------------------------------------------------------------------------


def test_robots_proibe_caminho(preco: ModuleType, sessao: Session) -> None:
    proibido = evento_ia(sessao, dias=0, link_inscricao="https://luma.com/proibido")
    livre = evento_ia(sessao, dias=1)
    rede = Rede(robots=httpx.Response(200, text="User-agent: *\nDisallow: /proibido\n"))
    resumo = _rodar(preco, sessao, rede)
    assert resumo.erros == [(proibido.id, "bloqueado_robots")]
    assert rede.caminhos_de_pagina == [_caminho(livre)]
    assert resumo.robots_bloqueado is False


@pytest.mark.parametrize(
    "robots",
    [httpx.Response(500), httpx.Response(403), httpx.ConnectError("sem rede")],
    ids=["500", "403", "erro"],
)
def test_robots_indisponivel_nao_grava_nada(
    preco: ModuleType, sessao: Session, robots: Resposta
) -> None:
    evento_ia(sessao)
    rede = Rede(robots=robots)
    resumo = _rodar(preco, sessao, rede)
    assert resumo.robots_bloqueado is True
    assert resumo.gravados == []
    assert _linhas(sessao) == []
    assert rede.caminhos_de_pagina == []


def test_robots_404_libera(preco: ModuleType, sessao: Session) -> None:
    evento = evento_ia(sessao)
    rede = Rede(robots=httpx.Response(404))
    resumo = _rodar(preco, sessao, rede)
    assert resumo.gravados == [(evento.id, "gratuito_confirmado")]


def test_robots_lido_uma_vez(preco: ModuleType, sessao: Session) -> None:
    for dia in range(3):
        evento_ia(sessao, dias=dia)
    rede = Rede()
    _rodar(preco, sessao, rede)
    assert rede.leituras_de_robots == 1
    assert len(rede.caminhos_de_pagina) == 3


# --- Invariantes ---------------------------------------------------------------------------


def test_so_luma_com_e_motivos_do_conjunto(preco: ModuleType, sessao: Session) -> None:
    respostas = [
        httpx.Response(200, text=fixture("pago_brl.html")),
        httpx.Response(200, text=fixture("sem_offers.html")),
        httpx.Response(500),
        httpx.ConnectError("x"),
        httpx.Response(302, headers={"Location": "https://evil.com/"}),
    ]
    eventos = [evento_ia(sessao, dias=i) for i in range(len(respostas))]
    evento_ia(sessao, dias=9, link_inscricao="https://evil.com/x")
    rede = Rede(paginas={_caminho(e): r for e, r in zip(eventos, respostas, strict=True)})
    _rodar(preco, sessao, rede)
    assert {r.url.host for r in rede.requisicoes} == {"luma.com"}
    assert all(r.url.scheme == "https" for r in rede.requisicoes)
    gravadas = _linhas(sessao)
    assert len(gravadas) == 6
    for linha in gravadas:
        assert linha["motivo"] is None or linha["motivo"] in MOTIVOS
        if linha["estado_preco"] == "pago_com_valor":
            assert linha["moeda"] == "BRL"
            assert linha["valor_centavos"] == 5000
        else:
            assert linha["moeda"] is None
            assert linha["valor_centavos"] is None
