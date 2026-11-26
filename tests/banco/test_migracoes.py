"""Testes do F1-05 — migrations do Alembic e restrições do esquema (aceite: "migrations sobem
do zero").

Assinaturas fixadas:
- `radar.banco.migrar.migrar_para_head(url: SecretStr) -> None`
- `radar.banco.migrar.reverter_para_base(url: SecretStr) -> None`
- `radar.banco.modelos.Base` (DeclarativeBase) com as 5 tabelas no `metadata`.

As restrições são testadas com SQL cru, pelos nomes das colunas do plano, para não depender
dos nomes das classes ORM. Enums são `text` + CHECK; horários são `timestamptz`.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .conftest import linhas
from .fabricas import AGORA
from .seguranca import recriar_schema

TABELAS = {
    "eventos",
    "execucoes",
    "classificacoes",
    "enriquecimentos",
    "notificacoes",
    "envios_sem_evento",  # F1-10c, migração 0002
}


@pytest.fixture
def banco_migravel(engine: Engine, url_teste: SecretStr) -> Iterator[Engine]:
    """Para testes que sobem/descem migrations; no fim deixa o banco em head de novo."""
    from radar.banco.migrar import migrar_para_head

    recriar_schema(engine)
    try:
        yield engine
    finally:
        recriar_schema(engine)
        migrar_para_head(url_teste)


def _tabelas(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names())


# --- Subir e descer -------------------------------------------------------------------------


def test_migrations_sobem_do_zero(banco_migravel: Engine, url_teste: SecretStr) -> None:
    from radar.banco.migrar import migrar_para_head

    assert _tabelas(banco_migravel) == set()
    migrar_para_head(url_teste)
    assert _tabelas(banco_migravel) >= TABELAS | {"alembic_version"}


def test_migrar_para_head_e_idempotente(banco_migravel: Engine, url_teste: SecretStr) -> None:
    from radar.banco.migrar import migrar_para_head

    migrar_para_head(url_teste)
    migrar_para_head(url_teste)
    assert _tabelas(banco_migravel) >= TABELAS


def test_migrations_descem_para_base_e_sobem_de_novo(
    banco_migravel: Engine, url_teste: SecretStr
) -> None:
    from radar.banco.migrar import migrar_para_head, reverter_para_base

    migrar_para_head(url_teste)
    reverter_para_base(url_teste)
    assert _tabelas(banco_migravel) & TABELAS == set()
    migrar_para_head(url_teste)
    assert _tabelas(banco_migravel) >= TABELAS


def test_existe_uma_unica_head(engine: Engine) -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from radar.banco.migrar import PASTA_MIGRACOES

    configuracao = Config()
    configuracao.set_main_option("script_location", str(PASTA_MIGRACOES))
    heads = ScriptDirectory.from_config(configuracao).get_heads()
    assert len(heads) == 1
    with engine.connect() as conexao:
        versoes = conexao.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    assert versoes == heads


def test_modelos_batem_com_as_migrations(engine: Engine) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from radar.banco.modelos import Base

    with engine.connect() as conexao:
        diferencas = compare_metadata(MigrationContext.configure(conexao), Base.metadata)
    assert diferencas == []


def test_metadata_tem_as_tabelas() -> None:
    from radar.banco.modelos import Base

    assert set(Base.metadata.tables) == TABELAS


# --- Tipos das colunas ----------------------------------------------------------------------


def _colunas(engine: Engine, tabela: str) -> dict[str, Any]:
    return {c["name"]: c for c in inspect(engine).get_columns(tabela)}


@pytest.mark.parametrize(
    ("tabela", "coluna"),
    [
        ("eventos", "inicio"),
        ("eventos", "fim"),
        ("eventos", "primeira_vez_visto"),
        ("eventos", "ultima_vez_visto"),
        ("eventos", "atualizado_em"),
        ("execucoes", "executada_em"),
        ("classificacoes", "criada_em"),
        ("enriquecimentos", "criado_em"),
        ("notificacoes", "criada_em"),
        ("notificacoes", "enviada_em"),
    ],
)
def test_horarios_sao_timestamptz(engine: Engine, tabela: str, coluna: str) -> None:
    tipo = _colunas(engine, tabela)[coluna]["type"]
    assert isinstance(tipo, postgresql.TIMESTAMP)
    assert tipo.timezone is True


def test_coluna_rodadas_escalares_mantidos(engine: Engine, sessao: Session) -> None:
    coluna = _colunas(engine, "eventos")["rodadas_escalares_mantidos"]
    assert isinstance(coluna["type"], postgresql.INTEGER)
    assert coluna["nullable"] is False
    assert "0" in str(coluna["default"])
    evento_id = _inserir_evento(sessao)  # sem informar a coluna: usa o padrão 0
    [linha] = linhas(
        sessao, "SELECT rodadas_escalares_mantidos FROM eventos WHERE id = :id", id=evento_id
    )
    assert linha["rodadas_escalares_mantidos"] == 0


def test_tipos_especiais_de_eventos(engine: Engine) -> None:
    colunas = _colunas(engine, "eventos")
    assert isinstance(colunas["id"]["type"], postgresql.BIGINT)
    assert isinstance(colunas["calendarios"]["type"], postgresql.ARRAY)
    assert isinstance(colunas["descricoes"]["type"], postgresql.JSONB)
    assert isinstance(colunas["hash_conteudo"]["type"], postgresql.CHAR)
    assert colunas["hash_conteudo"]["type"].length == 64
    assert colunas["estado"]["nullable"] is False
    assert colunas["hash_conteudo"]["nullable"] is False


def test_tipos_especiais_de_classificacoes(engine: Engine) -> None:
    colunas = _colunas(engine, "classificacoes")
    assert isinstance(colunas["resultado"]["type"], postgresql.JSONB)
    custo = colunas["custo_usd"]["type"]
    assert isinstance(custo, postgresql.NUMERIC)
    assert (custo.precision, custo.scale) == (12, 6)
    assert isinstance(colunas["rodada"]["type"], postgresql.UUID)


def test_rodada_de_execucoes_e_uuid(engine: Engine) -> None:
    assert isinstance(_colunas(engine, "execucoes")["rodada"]["type"], postgresql.UUID)


def test_indices_de_eventos(engine: Engine) -> None:
    indices = {tuple(i["column_names"]) for i in inspect(engine).get_indexes("eventos")}
    assert ("estado",) in indices
    assert ("inicio",) in indices


# --- Inserções cruas ------------------------------------------------------------------------


def _inserir_evento(sessao: Session, id_fonte: str = "evt-Sql1", **campos: Any) -> int:
    valores: dict[str, Any] = {
        "fonte": "luma",
        "id_fonte": id_fonte,
        "calendarios": ["cal-a"],
        "titulo": "Evento",
        "inicio": AGORA + timedelta(days=5),
        "fim": AGORA + timedelta(days=5, hours=2),
        "dia_inteiro": False,
        "local_texto": None,
        "local_url": None,
        "link_inscricao": None,
        "organizador": None,
        "endereco_oculto": False,
        "latitude": None,
        "longitude": None,
        "descricoes": '{"cal-a": null}',
        "status_fonte": "desconhecido",
        "localizacao": "indefinida",
        "evidencia_localizacao": "nenhuma",
        "hash_conteudo": "a" * 64,
        "estado": "ativo",
        "ausencias_consecutivas": 0,
        "primeira_vez_visto": AGORA,
        "ultima_vez_visto": AGORA,
        "atualizado_em": AGORA,
    }
    valores.update(campos)
    colunas = ", ".join(valores)
    marcadores = ", ".join(
        f"CAST(:{c} AS jsonb)" if c == "descricoes" else f":{c}" for c in valores
    )
    with sessao.begin_nested():
        return sessao.execute(
            text(f"INSERT INTO eventos ({colunas}) VALUES ({marcadores}) RETURNING id"), valores
        ).scalar_one()


def _inserir_execucao(sessao: Session, **campos: Any) -> None:
    valores: dict[str, Any] = {
        "rodada": uuid.uuid4(),
        "fonte": "luma",
        "calendario": "cal-a",
        "executada_em": AGORA,
        "situacao": "ok",
        "quantidade_eventos": 1,
        "quantidade_invalidos": 0,
        "motivo_falha": None,
        "status_http": None,
        "detalhe": None,
    }
    valores.update(campos)
    colunas = ", ".join(valores)
    marcadores = ", ".join(f":{c}" for c in valores)
    with sessao.begin_nested():
        sessao.execute(text(f"INSERT INTO execucoes ({colunas}) VALUES ({marcadores})"), valores)


def _inserir_classificacao(sessao: Session, evento_id: int, **campos: Any) -> None:
    valores: dict[str, Any] = {
        "evento_id": evento_id,
        "rodada": uuid.uuid4(),
        "criada_em": AGORA,
        "hash_conteudo": "a" * 64,
        "versao_prompt": "v1",
        "modelo": "modelo-falso",
        "versao_schema": "1",
        "situacao": "ok",
        "resultado": "{}",
        "motivo_erro": None,
        "tokens_entrada": 10,
        "tokens_saida": 5,
        "latencia_ms": 100,
        "custo_usd": "0.000123",
    }
    valores.update(campos)
    colunas = ", ".join(valores)
    marcadores = ", ".join(f"CAST(:{c} AS jsonb)" if c == "resultado" else f":{c}" for c in valores)
    with sessao.begin_nested():
        sessao.execute(
            text(f"INSERT INTO classificacoes ({colunas}) VALUES ({marcadores})"), valores
        )


def _inserir_enriquecimento(sessao: Session, evento_id: int, **campos: Any) -> None:
    valores: dict[str, Any] = {
        "evento_id": evento_id,
        "hash_conteudo": "a" * 64,
        "rodada": uuid.uuid4(),
        "criado_em": AGORA,
        "estado_preco": "gratuito_confirmado",
        "valor_centavos": None,
        "moeda": None,
        "motivo": None,
        "status_http": 200,
    }
    valores.update(campos)
    colunas = ", ".join(valores)
    marcadores = ", ".join(f":{c}" for c in valores)
    with sessao.begin_nested():
        sessao.execute(
            text(f"INSERT INTO enriquecimentos ({colunas}) VALUES ({marcadores})"), valores
        )


def _inserir_notificacao(sessao: Session, evento_id: int, **campos: Any) -> None:
    valores: dict[str, Any] = {
        "evento_id": evento_id,
        "tipo": "aviso",
        "estado": "enviando",
        "criada_em": AGORA,
        "enviada_em": None,
    }
    valores.update(campos)
    colunas = ", ".join(valores)
    marcadores = ", ".join(f":{c}" for c in valores)
    with sessao.begin_nested():
        sessao.execute(text(f"INSERT INTO notificacoes ({colunas}) VALUES ({marcadores})"), valores)


# --- eventos ------------------------------------------------------------------------------


def test_evento_valido_e_aceito(sessao: Session) -> None:
    evento_id = _inserir_evento(sessao)
    [linha] = linhas(sessao, "SELECT * FROM eventos WHERE id = :id", id=evento_id)
    assert linha["calendarios"] == ["cal-a"]
    assert linha["descricoes"] == {"cal-a": None}
    assert linha["inicio"].utcoffset() is not None


def test_eventos_unico_por_fonte_e_id_fonte(sessao: Session) -> None:
    _inserir_evento(sessao, "evt-Dup1")
    with pytest.raises(IntegrityError):
        _inserir_evento(sessao, "evt-Dup1")


@pytest.mark.parametrize(
    "campos",
    [
        {"fonte": "meetup"},
        {"estado": "provisorio"},
        {"status_fonte": "tentative"},
        {"localizacao": "exterior"},
        {"ausencias_consecutivas": -1},
        {"rodadas_escalares_mantidos": -1},
    ],
)
def test_check_de_eventos(sessao: Session, campos: dict[str, Any]) -> None:
    with pytest.raises(IntegrityError):
        _inserir_evento(sessao, **campos)


@pytest.mark.parametrize(
    "estado", ["ativo", "alterado", "cancelado", "encerrado", "nao_encontrado"]
)
def test_estados_validos_de_eventos(sessao: Session, estado: str) -> None:
    _inserir_evento(sessao, estado=estado)


@pytest.mark.parametrize("localizacao", ["sp", "online", "indefinida", "fora"])
def test_localizacoes_validas(sessao: Session, localizacao: str) -> None:
    _inserir_evento(sessao, localizacao=localizacao)


# --- execucoes ----------------------------------------------------------------------------


def test_execucoes_unica_por_rodada_fonte_calendario(sessao: Session) -> None:
    rodada = uuid.uuid4()
    _inserir_execucao(sessao, rodada=rodada, calendario="cal-a")
    _inserir_execucao(sessao, rodada=rodada, calendario="cal-b")
    _inserir_execucao(sessao, rodada=uuid.uuid4(), calendario="cal-a")
    with pytest.raises(IntegrityError):
        _inserir_execucao(sessao, rodada=rodada, calendario="cal-a")


@pytest.mark.parametrize(
    "motivo", ["bloqueado_robots", "status_http", "redirecionamento", "rede", "formato_invalido"]
)
def test_motivos_de_falha_validos(sessao: Session, motivo: str) -> None:
    _inserir_execucao(sessao, situacao="falha", quantidade_eventos=0, motivo_falha=motivo)


@pytest.mark.parametrize(
    "campos", [{"situacao": "parcial"}, {"situacao": "falha", "motivo_falha": "timeout"}]
)
def test_check_de_execucoes(sessao: Session, campos: dict[str, Any]) -> None:
    with pytest.raises(IntegrityError):
        _inserir_execucao(sessao, **campos)


# --- classificacoes -----------------------------------------------------------------------


def test_classificacao_ok_unica_por_chave(sessao: Session) -> None:
    evento_id = _inserir_evento(sessao)
    _inserir_classificacao(sessao, evento_id)
    with pytest.raises(IntegrityError):
        _inserir_classificacao(sessao, evento_id)


def test_duas_classificacoes_erro_com_a_mesma_chave_sao_aceitas(sessao: Session) -> None:
    evento_id = _inserir_evento(sessao)
    erro = {"situacao": "erro", "resultado": None, "motivo_erro": "saida_invalida"}
    _inserir_classificacao(sessao, evento_id, **erro)
    _inserir_classificacao(sessao, evento_id, **erro)
    _inserir_classificacao(sessao, evento_id)  # e ainda cabe uma ok
    [contagem] = linhas(
        sessao, "SELECT count(*) AS n FROM classificacoes WHERE evento_id = :id", id=evento_id
    )
    assert contagem["n"] == 3


@pytest.mark.parametrize("campo", ["hash_conteudo", "versao_prompt", "modelo", "versao_schema"])
def test_classificacao_ok_com_outra_chave_e_aceita(sessao: Session, campo: str) -> None:
    evento_id = _inserir_evento(sessao)
    _inserir_classificacao(sessao, evento_id)
    outro = "b" * 64 if campo == "hash_conteudo" else "outro"
    _inserir_classificacao(sessao, evento_id, **{campo: outro})


def test_check_de_situacao_da_classificacao(sessao: Session) -> None:
    evento_id = _inserir_evento(sessao)
    with pytest.raises(IntegrityError):
        _inserir_classificacao(sessao, evento_id, situacao="pendente")


def test_classificacao_exige_evento_existente(sessao: Session) -> None:
    with pytest.raises(IntegrityError):
        _inserir_classificacao(sessao, 999_999_999)


def test_apagar_evento_apaga_classificacoes(sessao: Session) -> None:
    evento_id = _inserir_evento(sessao)
    _inserir_classificacao(sessao, evento_id)
    sessao.execute(text("DELETE FROM eventos WHERE id = :id"), {"id": evento_id})
    restantes = linhas(sessao, "SELECT id FROM classificacoes WHERE evento_id = :id", id=evento_id)
    assert restantes == []


# --- enriquecimentos ----------------------------------------------------------------------


def test_enriquecimento_unico_por_evento_e_hash(sessao: Session) -> None:
    evento_id = _inserir_evento(sessao)
    _inserir_enriquecimento(sessao, evento_id)
    _inserir_enriquecimento(sessao, evento_id, hash_conteudo="b" * 64)
    with pytest.raises(IntegrityError):
        _inserir_enriquecimento(sessao, evento_id)


@pytest.mark.parametrize(
    "estado",
    [
        "gratuito_confirmado",
        "pago_com_valor",
        "pago_sem_valor",
        "preco_variavel",
        "preco_desconhecido",
    ],
)
def test_estados_de_preco_validos(sessao: Session, estado: str) -> None:
    evento_id = _inserir_evento(sessao)
    extra = {"valor_centavos": 5000, "moeda": "BRL"} if estado == "pago_com_valor" else {}
    _inserir_enriquecimento(sessao, evento_id, estado_preco=estado, **extra)


def test_check_de_estado_de_preco(sessao: Session) -> None:
    evento_id = _inserir_evento(sessao)
    with pytest.raises(IntegrityError):
        _inserir_enriquecimento(sessao, evento_id, estado_preco="gratis")


def test_enriquecimento_exige_evento_existente(sessao: Session) -> None:
    with pytest.raises(IntegrityError):
        _inserir_enriquecimento(sessao, 999_999_999)


# --- notificacoes -------------------------------------------------------------------------


def test_notificacao_unica_por_evento_e_tipo(sessao: Session) -> None:
    evento_id = _inserir_evento(sessao)
    _inserir_notificacao(sessao, evento_id)
    _inserir_notificacao(sessao, evento_id, tipo="lembrete")
    with pytest.raises(IntegrityError):
        _inserir_notificacao(sessao, evento_id, estado="enviado", enviada_em=AGORA)


def test_check_de_estado_da_notificacao(sessao: Session) -> None:
    evento_id = _inserir_evento(sessao)
    with pytest.raises(IntegrityError):
        _inserir_notificacao(sessao, evento_id, estado="falhou")


def test_notificacao_exige_evento_existente(sessao: Session) -> None:
    with pytest.raises(IntegrityError):
        _inserir_notificacao(sessao, 999_999_999)


# --- F1-10c: envios_sem_evento (migração 0002) ---------------------------------------------

TIPOS_SEM_EVENTO = (
    "falha_extracao",
    "fonte_vazia",
    "inatividade",
    "robots_bloqueado",
    "enriquecimento_sistematico",
    "classificador_interrompido",
    "resumo_semanal",
)


def _alembic(url: SecretStr, alvo: str, subir: bool) -> None:
    from alembic import command
    from alembic.config import Config

    from radar.banco.migrar import PASTA_MIGRACOES
    from radar.banco.sessao import criar_engine

    configuracao = Config()
    configuracao.set_main_option("script_location", str(PASTA_MIGRACOES))
    motor = criar_engine(url)
    try:
        with motor.begin() as conexao:
            configuracao.attributes["connection"] = conexao
            if subir:
                command.upgrade(configuracao, alvo)
            else:
                command.downgrade(configuracao, alvo)
    finally:
        motor.dispose()


def test_0002_sobe_e_desce_para_0001(banco_migravel: Engine, url_teste: SecretStr) -> None:
    from radar.banco.migrar import migrar_para_head

    _alembic(url_teste, "0001_esquema_inicial", subir=True)
    assert "envios_sem_evento" not in _tabelas(banco_migravel)
    _alembic(url_teste, "0002_envios_sem_evento", subir=True)
    assert "envios_sem_evento" in _tabelas(banco_migravel)
    with banco_migravel.connect() as conexao:
        versao = conexao.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert versao == "0002_envios_sem_evento"
    _alembic(url_teste, "0001_esquema_inicial", subir=False)
    assert "envios_sem_evento" not in _tabelas(banco_migravel)
    assert _tabelas(banco_migravel) >= TABELAS - {"envios_sem_evento"}
    migrar_para_head(url_teste)
    assert "envios_sem_evento" in _tabelas(banco_migravel)


def test_colunas_de_envios_sem_evento(engine: Engine) -> None:
    colunas = _colunas(engine, "envios_sem_evento")
    assert set(colunas) == {"id", "chave", "tipo", "estado", "criado_em", "enviado_em"}
    for nome in ("criado_em", "enviado_em"):
        assert isinstance(colunas[nome]["type"], postgresql.TIMESTAMP)
        assert colunas[nome]["type"].timezone is True
    assert colunas["enviado_em"]["nullable"] is True
    for nome in ("chave", "tipo", "estado", "criado_em"):
        assert colunas[nome]["nullable"] is False


def _inserir_envio(sessao: Session, **campos: Any) -> None:
    valores: dict[str, Any] = {
        "chave": "falha_extracao:mug-sp:2026-11-25",
        "tipo": "falha_extracao",
        "estado": "enviado",
        "criado_em": AGORA,
        "enviado_em": AGORA,
    }
    valores.update(campos)
    colunas = ", ".join(valores)
    marcadores = ", ".join(f":{c}" for c in valores)
    sessao.execute(
        text(f"INSERT INTO envios_sem_evento ({colunas}) VALUES ({marcadores})"), valores
    )
    sessao.flush()


@pytest.mark.parametrize("tipo", TIPOS_SEM_EVENTO)
def test_envio_sem_evento_tipos_validos(sessao: Session, tipo: str) -> None:
    _inserir_envio(sessao, tipo=tipo, chave=f"{tipo}:x:2026-11-25")
    assert linhas(sessao, "SELECT tipo FROM envios_sem_evento") == [{"tipo": tipo}]


@pytest.mark.parametrize(
    "campos",
    [
        {"tipo": "aviso"},
        {"estado": "pendente"},
        {"estado": "enviado", "enviado_em": None},
        {"estado": "enviando", "enviado_em": AGORA},
    ],
)
def test_envio_sem_evento_checks(sessao: Session, campos: dict[str, Any]) -> None:
    with pytest.raises(IntegrityError):
        _inserir_envio(sessao, **campos)


def test_envio_sem_evento_enviando_sem_horario(sessao: Session) -> None:
    _inserir_envio(sessao, estado="enviando", enviado_em=None)
    assert linhas(sessao, "SELECT estado FROM envios_sem_evento") == [{"estado": "enviando"}]


def test_envio_sem_evento_chave_unica(sessao: Session) -> None:
    _inserir_envio(sessao)
    with pytest.raises(IntegrityError):
        _inserir_envio(sessao, tipo="fonte_vazia")


def test_modelo_envio_sem_evento() -> None:
    from radar.banco.modelos import EnvioSemEvento

    assert EnvioSemEvento.__tablename__ == "envios_sem_evento"
