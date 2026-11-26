"""Testes do F1-10c — alertas sem evento (radar.notificacao.alertas).

Assinaturas fixadas:
- `TipoAlerta = Literal["falha_extracao", "fonte_vazia", "inatividade", "robots_bloqueado",
  "enriquecimento_sistematico", "classificador_interrompido"]`;
- `Alerta(tipo, alvo: str, codigo: str | None = None, quantidade: int | None = None)` frozen;
- `chave_alerta(alerta, agora) -> str` = f"{tipo}:{alvo}:{data em America/Sao_Paulo ISO}";
  `agora` sem fuso → ValueError;
- `formatar_alerta(alerta) -> str`: texto puro começando por "Alerta do radar", só nomes
  fixos, slug, código e contagem, sem URL; ≤ 4096; inatividade cita "60 dias" e "Reativação";
- `ResumoAlertas`: enviados: list[Alerta], ja_enviados: list[Alerta], falhas:
  list[tuple[Alerta, str]] (alerta, categoria), nao_enviados: list[Alerta],
  interrompido_por: str | None ("autenticacao"/"proibido"/"rate_limit"/"falhas_seguidas");
- `enviar_alertas(sessao, cliente: EnviadorMensagens, alertas, agora) -> ResumoAlertas`:
  transação limpa (como `notificar`), dedup na lista, 'enviado' do dia → ja_enviados,
  'enviando' do dia → reenvia, sem linha → 'enviando' + commit, envia, 'enviado' + commit;
  autenticacao/proibido/rate_limit interrompem; demais após 3 seguidas; o que sobra vai para
  nao_enviados sem reserva. Grava em `envios_sem_evento` (tipo = alerta.tipo).
"""

from __future__ import annotations

import importlib
import typing
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from banco.conftest import linhas
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

SP = ZoneInfo("America/Sao_Paulo")
UTC = ZoneInfo("UTC")
AGORA = datetime(2026, 11, 25, 12, 0, tzinfo=SP)
TIPOS = {
    "falha_extracao",
    "fonte_vazia",
    "inatividade",
    "robots_bloqueado",
    "enriquecimento_sistematico",
    "classificador_interrompido",
}


@pytest.fixture
def al() -> ModuleType:
    return importlib.import_module("radar.notificacao.alertas")


@pytest.fixture
def tg() -> ModuleType:
    return importlib.import_module("radar.notificacao.telegram")


@dataclass
class EnviadorFalso:
    fila: list[Any] = field(default_factory=list)
    textos: list[str] = field(default_factory=list)

    def enviar_texto(self, texto: str) -> None:
        self.textos.append(texto)
        if self.fila:
            item = self.fila.pop(0)
            if isinstance(item, Exception):
                raise item


def _alerta(al: ModuleType, alvo: str = "mug-sp", **campos: Any) -> Any:
    campos.setdefault("tipo", "falha_extracao")
    campos.setdefault("codigo", "status_http")
    return al.Alerta(alvo=alvo, **campos)


def _envios(sessao: Session) -> list[dict[str, Any]]:
    return linhas(sessao, "SELECT * FROM envios_sem_evento ORDER BY id")


def _erro(tg: ModuleType, categoria: str, status: int | None) -> Exception:
    return tg.ErroTelegram(categoria=categoria, status_http=status, retry_after=None)


# --- Tipos, chave e texto ------------------------------------------------------------------


def test_tipos(al: ModuleType) -> None:
    assert set(typing.get_args(al.TipoAlerta)) == TIPOS


def test_alerta_frozen(al: ModuleType) -> None:
    alerta = al.Alerta(tipo="fonte_vazia", alvo="codex-sp")
    assert (alerta.codigo, alerta.quantidade) == (None, None)
    with pytest.raises((ValidationError, AttributeError, TypeError)):
        alerta.alvo = "outro"


@pytest.mark.parametrize(
    ("agora", "data"),
    [
        (datetime(2026, 11, 25, 23, 30, tzinfo=SP), "2026-11-25"),
        (datetime(2026, 11, 26, 0, 30, tzinfo=SP), "2026-11-26"),
        (datetime(2026, 11, 26, 2, 30, tzinfo=UTC), "2026-11-25"),  # 23:30 em SP
    ],
)
def test_chave_alerta_usa_o_dia_de_sp(al: ModuleType, agora: datetime, data: str) -> None:
    alerta = _alerta(al)
    assert al.chave_alerta(alerta, agora) == f"falha_extracao:mug-sp:{data}"


def test_chave_alerta_sem_fuso(al: ModuleType) -> None:
    with pytest.raises(ValueError):
        al.chave_alerta(_alerta(al), datetime(2026, 11, 25, 12))


def test_formatar_falha_de_extracao(al: ModuleType) -> None:
    texto = al.formatar_alerta(
        al.Alerta(tipo="falha_extracao", alvo="mug-sp", codigo="status_http")
    )
    assert texto.startswith("Alerta do radar")
    assert "falha de extração" in texto
    assert "mug-sp" in texto
    assert "status_http" in texto


@pytest.mark.parametrize(
    "alerta",
    [
        {
            "tipo": "falha_extracao",
            "alvo": "mug-sp",
            "codigo": "eventos_invalidos",
            "quantidade": 4,
        },
        {"tipo": "fonte_vazia", "alvo": "codex-sp", "quantidade": 6},
        {"tipo": "inatividade", "alvo": "repositorio", "quantidade": 55},
        {"tipo": "robots_bloqueado", "alvo": "luma.com"},
        {"tipo": "enriquecimento_sistematico", "alvo": "luma.com", "quantidade": 3},
        {"tipo": "classificador_interrompido", "alvo": "anthropic", "codigo": "rate_limit"},
    ],
)
def test_formatar_sem_url_e_cabe_no_telegram(al: ModuleType, alerta: dict[str, Any]) -> None:
    texto = al.formatar_alerta(al.Alerta(**alerta))
    assert texto.startswith("Alerta do radar")
    assert "http://" not in texto
    assert "https://" not in texto
    assert len(texto.encode("utf-16-le")) // 2 <= 4096
    if alerta.get("codigo"):
        assert alerta["codigo"] in texto
    if alerta.get("quantidade") is not None:
        assert str(alerta["quantidade"]) in texto


def test_formatar_inatividade(al: ModuleType) -> None:
    texto = al.formatar_alerta(al.Alerta(tipo="inatividade", alvo="repositorio", quantidade=55))
    assert "60 dias" in texto
    assert "Reativação" in texto


# --- Envio ---------------------------------------------------------------------------------


def test_alerta_novo(al: ModuleType, sessao: Session) -> None:
    alerta = _alerta(al)
    cliente = EnviadorFalso()
    resumo = al.enviar_alertas(sessao, cliente, [alerta], AGORA)
    assert cliente.textos == [al.formatar_alerta(alerta)]
    assert resumo.enviados == [alerta]
    assert resumo.interrompido_por is None
    [linha] = _envios(sessao)
    assert linha["chave"] == al.chave_alerta(alerta, AGORA)
    assert (linha["tipo"], linha["estado"]) == ("falha_extracao", "enviado")
    assert linha["enviado_em"] == AGORA


def test_mesmo_dia_nao_reenvia(al: ModuleType, sessao: Session) -> None:
    alerta = _alerta(al)
    cliente = EnviadorFalso()
    al.enviar_alertas(sessao, cliente, [alerta], AGORA)
    resumo = al.enviar_alertas(sessao, cliente, [alerta], AGORA + timedelta(hours=7))
    assert len(cliente.textos) == 1
    assert resumo.ja_enviados == [alerta]
    assert resumo.enviados == []


def test_dia_seguinte_reenvia(al: ModuleType, sessao: Session) -> None:
    alerta = _alerta(al)
    cliente = EnviadorFalso()
    al.enviar_alertas(sessao, cliente, [alerta], AGORA)
    al.enviar_alertas(sessao, cliente, [alerta], AGORA + timedelta(days=1))
    assert len(cliente.textos) == 2
    assert len(_envios(sessao)) == 2


def test_virada_do_dia_em_sp(al: ModuleType, sessao: Session) -> None:
    alerta = _alerta(al)
    cliente = EnviadorFalso()
    al.enviar_alertas(sessao, cliente, [alerta], datetime(2026, 11, 25, 23, 30, tzinfo=SP))
    al.enviar_alertas(sessao, cliente, [alerta], datetime(2026, 11, 26, 2, 45, tzinfo=UTC))
    assert len(cliente.textos) == 1  # 23:45 em SP, mesmo dia
    al.enviar_alertas(sessao, cliente, [alerta], datetime(2026, 11, 26, 0, 30, tzinfo=SP))
    assert len(cliente.textos) == 2


def test_enviando_do_mesmo_dia_e_reenviado(al: ModuleType, sessao: Session) -> None:
    alerta = _alerta(al)
    sessao.execute(
        text(
            "INSERT INTO envios_sem_evento (chave, tipo, estado, criado_em) "
            "VALUES (:c, 'falha_extracao', 'enviando', :a)"
        ),
        {"c": al.chave_alerta(alerta, AGORA), "a": AGORA},
    )
    sessao.commit()
    cliente = EnviadorFalso()
    resumo = al.enviar_alertas(sessao, cliente, [alerta], AGORA)
    assert len(cliente.textos) == 1
    assert resumo.enviados == [alerta]
    [linha] = _envios(sessao)
    assert linha["estado"] == "enviado"


def test_duplicado_na_lista(al: ModuleType, sessao: Session) -> None:
    alerta = _alerta(al)
    cliente = EnviadorFalso()
    resumo = al.enviar_alertas(sessao, cliente, [alerta, _alerta(al)], AGORA)
    assert len(cliente.textos) == 1
    assert len(resumo.enviados) == 1


@pytest.mark.parametrize(
    ("categoria", "status"), [("autenticacao", 401), ("proibido", 403), ("rate_limit", 429)]
)
def test_interrompe_na_hora(
    al: ModuleType, tg: ModuleType, sessao: Session, categoria: str, status: int
) -> None:
    alertas = [_alerta(al, alvo=s) for s in ("a-1", "b-2", "c-3")]
    cliente = EnviadorFalso(fila=[_erro(tg, categoria, status)])
    resumo = al.enviar_alertas(sessao, cliente, alertas, AGORA)
    assert len(cliente.textos) == 1
    assert resumo.interrompido_por == categoria
    assert [a for a, _ in resumo.falhas] == [alertas[0]]
    assert resumo.nao_enviados == alertas[1:]
    chaves = {linha["chave"] for linha in _envios(sessao)}
    assert chaves == {al.chave_alerta(alertas[0], AGORA)}  # sobra sem reserva
    assert _envios(sessao)[0]["estado"] == "enviando"


def test_tres_falhas_seguidas(al: ModuleType, tg: ModuleType, sessao: Session) -> None:
    alertas = [_alerta(al, alvo=f"cal-{i}") for i in range(5)]
    cliente = EnviadorFalso(fila=[_erro(tg, "servidor", 500) for _ in range(3)])
    resumo = al.enviar_alertas(sessao, cliente, alertas, AGORA)
    assert len(cliente.textos) == 3
    assert resumo.interrompido_por == "falhas_seguidas"
    assert resumo.nao_enviados == alertas[3:]


def test_sucesso_zera_a_sequencia(al: ModuleType, tg: ModuleType, sessao: Session) -> None:
    alertas = [_alerta(al, alvo=f"cal-{i}") for i in range(5)]
    falha = _erro(tg, "servidor", 500)
    cliente = EnviadorFalso(fila=[falha, None, falha, falha, None])
    resumo = al.enviar_alertas(sessao, cliente, alertas, AGORA)
    assert len(cliente.textos) == 5
    assert resumo.interrompido_por is None
    assert len(resumo.enviados) == 2
    assert len(resumo.falhas) == 3


def test_sessao_suja(al: ModuleType, sessao: Session) -> None:
    from radar.banco.modelos import Evento

    sessao.add(Evento())
    cliente = EnviadorFalso()
    with pytest.raises(ValueError):
        al.enviar_alertas(sessao, cliente, [_alerta(al)], AGORA)
    assert cliente.textos == []


def test_agora_sem_fuso(al: ModuleType, sessao: Session) -> None:
    cliente = EnviadorFalso()
    with pytest.raises(ValueError):
        al.enviar_alertas(sessao, cliente, [_alerta(al)], datetime(2026, 11, 25, 12))
    assert cliente.textos == []


@pytest.fixture
def sessao_engine(engine: Engine) -> Iterator[Session]:
    real = Session(bind=engine)
    try:
        yield real
    finally:
        real.rollback()
        real.close()
        with engine.begin() as conexao:
            conexao.execute(
                text("DELETE FROM envios_sem_evento WHERE chave LIKE :padrao"),
                {"padrao": "%:engine-%"},
            )


def test_sessao_ligada_ao_engine(al: ModuleType, engine: Engine, sessao_engine: Session) -> None:
    alerta = _alerta(al, alvo="engine-1")
    cliente = EnviadorFalso()
    resumo = al.enviar_alertas(sessao_engine, cliente, [alerta], AGORA)
    assert resumo.enviados == [alerta]
    with engine.connect() as conexao:
        estado = conexao.execute(
            text("SELECT estado FROM envios_sem_evento WHERE chave = :c"),
            {"c": al.chave_alerta(alerta, AGORA)},
        ).scalar()
    assert estado == "enviado"
