"""Testes do F1-05 — repositório: registro da rodada (upsert + ciclo de vida) e seleção de
eventos para classificar.

Assinaturas fixadas (radar.banco.repositorio; funções recebem Session e só fazem flush):
- `registrar_rodada(sessao: Session, rodada: UUID, resultados: Sequence[ResultadoCalendario],
  aprovados: Sequence[EventoLocalizado], agora: datetime, limite_ausencias: int)
  -> ResumoRodada`
- `ResumoRodada`: ja_registrada: bool; novos, alterados, cancelados, encerrados,
  nao_encontrados, reativados: list[int] (ids de `eventos` que ENTRARAM nesse estado na
  rodada; `novos` = inseridos; `reativados` = saíram de nao_encontrado).
- `eventos_para_classificar(sessao: Session, versao_prompt: str, modelo: str,
  versao_schema: str, limite: int) -> list[Evento]` (Evento = classe ORM de
  radar.banco.modelos, atributos com os nomes das colunas).

Regras cobertas: novos só se aprovados; existentes atualizados mesmo se filtrados;
idempotência por rodada; calendário com falha não conta ausência (o evento só tem ausência
contável se TODOS os seus calendários foram lidos com sucesso — ok ou vazio — na rodada);
ids em `eventos_invalidos` de calendário ok contam como vistos; descrições acumuladas por
calendário (um calendário que falha não faz o hash oscilar); encerramento por
`calcular_termino`.
"""

from __future__ import annotations

import importlib
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from radar.filtros.geografico import EventoLocalizado
from radar.fontes.modelos import ResultadoCalendario

from .conftest import linhas
from .fabricas import AGORA, INICIO_PADRAO, SP, aprovar, evento, falha, localizado, ok, vazio

LIMITE = 3


@pytest.fixture
def repo() -> ModuleType:
    return importlib.import_module("radar.banco.repositorio")


def _rodar(
    repo: ModuleType,
    sessao: Session,
    resultados: Sequence[ResultadoCalendario],
    aprovados: Sequence[EventoLocalizado] | None = None,
    *,
    agora: datetime = AGORA,
    limite: int = LIMITE,
    rodada: uuid.UUID | None = None,
) -> Any:
    return repo.registrar_rodada(
        sessao,
        rodada or uuid.uuid4(),
        list(resultados),
        aprovar(resultados) if aprovados is None else list(aprovados),
        agora=agora,
        limite_ausencias=limite,
    )


def _ev(sessao: Session, id_fonte: str = "evt-Alfa1") -> dict[str, Any]:
    encontrados = linhas(sessao, "SELECT * FROM eventos WHERE id_fonte = :i", i=id_fonte)
    assert len(encontrados) == 1, f"esperado 1 evento {id_fonte}, achou {len(encontrados)}"
    return encontrados[0]


def _contar_eventos(sessao: Session) -> int:
    return linhas(sessao, "SELECT count(*) AS n FROM eventos")[0]["n"]


def _resumo_vazio(resumo: Any) -> bool:
    return not any(
        (
            resumo.novos,
            resumo.alterados,
            resumo.cancelados,
            resumo.encerrados,
            resumo.nao_encontrados,
            resumo.reativados,
        )
    )


# --- Primeira rodada --------------------------------------------------------------------------


def test_primeira_rodada_insere_evento_aprovado(repo: ModuleType, sessao: Session) -> None:
    obs = evento()
    resumo = _rodar(repo, sessao, [ok("cal-a", [obs])])

    linha = _ev(sessao)
    assert resumo.ja_registrada is False
    assert resumo.novos == [linha["id"]]
    assert resumo.alterados == []
    assert linha["fonte"] == "luma"
    assert linha["calendarios"] == ["cal-a"]
    assert linha["titulo"] == obs.titulo
    assert linha["inicio"] == obs.inicio
    assert linha["fim"] == obs.fim
    assert linha["dia_inteiro"] is False
    assert linha["local_texto"] == obs.local_texto
    assert linha["local_url"] is None
    assert linha["link_inscricao"] == obs.link_inscricao
    assert linha["organizador"] == obs.organizador
    assert linha["endereco_oculto"] is False
    assert linha["latitude"] == pytest.approx(obs.latitude)
    assert linha["longitude"] == pytest.approx(obs.longitude)
    assert linha["descricoes"] == {"cal-a": obs.descricao}
    assert linha["status_fonte"] == "desconhecido"
    assert linha["localizacao"] == "sp"
    assert linha["evidencia_localizacao"] == "geo"
    assert linha["estado"] == "ativo"
    assert linha["ausencias_consecutivas"] == 0
    assert linha["primeira_vez_visto"] == AGORA
    assert linha["ultima_vez_visto"] == AGORA
    assert linha["atualizado_em"] == AGORA


def test_hash_gravado_e_o_do_modulo_conteudo(repo: ModuleType, sessao: Session) -> None:
    conteudo = importlib.import_module("radar.banco.conteudo")
    obs = evento()
    _rodar(repo, sessao, [ok("cal-a", [obs])])
    esperado = conteudo.hash_conteudo(
        conteudo.consolidar_observacoes([obs]), {"cal-a": obs.descricao}
    )
    assert _ev(sessao)["hash_conteudo"] == esperado


def test_localizacao_online_e_gravada(repo: ModuleType, sessao: Session) -> None:
    obs = evento(local_texto="Online", latitude=None, longitude=None)
    _rodar(repo, sessao, [ok("cal-a", [obs])], [localizado(obs, "online", "online_texto")])
    linha = _ev(sessao)
    assert (linha["localizacao"], linha["evidencia_localizacao"]) == ("online", "online_texto")


def test_evento_nao_aprovado_nao_e_gravado(repo: ModuleType, sessao: Session) -> None:
    aprovado = evento("evt-Sim1")
    filtrado = evento("evt-Nao1")
    resumo = _rodar(repo, sessao, [ok("cal-a", [aprovado, filtrado])], [localizado(aprovado)])
    assert _contar_eventos(sessao) == 1
    assert _ev(sessao, "evt-Sim1")["id"] in resumo.novos
    assert linhas(sessao, "SELECT id FROM eventos WHERE id_fonte = 'evt-Nao1'") == []


def test_evento_novo_cancelado_entra_como_cancelado(repo: ModuleType, sessao: Session) -> None:
    resumo = _rodar(repo, sessao, [ok("cal-a", [evento(status_fonte="cancelado")])])
    linha = _ev(sessao)
    assert linha["estado"] == "cancelado"
    assert linha["id"] in resumo.novos


def test_execucoes_registradas_por_calendario(repo: ModuleType, sessao: Session) -> None:
    rodada = uuid.uuid4()
    resultados = [
        ok("cal-a", [evento("evt-A1", "cal-a"), evento("evt-A2", "cal-a")], invalidos=["evt-X1"]),
        vazio("cal-b"),
        falha("cal-c", motivo="status_http", status_http=429),
    ]
    _rodar(repo, sessao, resultados, rodada=rodada)
    execucoes = {
        e["calendario"]: e
        for e in linhas(sessao, "SELECT * FROM execucoes WHERE rodada = :r", r=rodada)
    }
    assert set(execucoes) == {"cal-a", "cal-b", "cal-c"}
    a, b, c = execucoes["cal-a"], execucoes["cal-b"], execucoes["cal-c"]
    assert (a["situacao"], a["quantidade_eventos"], a["quantidade_invalidos"]) == ("ok", 2, 1)
    assert a["motivo_falha"] is None
    assert (b["situacao"], b["quantidade_eventos"], b["quantidade_invalidos"]) == ("vazio", 0, 0)
    assert (c["situacao"], c["motivo_falha"], c["status_http"]) == ("falha", "status_http", 429)
    assert all(e["fonte"] == "luma" and e["executada_em"] == AGORA for e in execucoes.values())


# --- Idempotência -----------------------------------------------------------------------------


def test_mesma_rodada_duas_vezes_nao_muda_nada(repo: ModuleType, sessao: Session) -> None:
    _rodar(repo, sessao, [ok("cal-a", [evento()])])
    rodada = uuid.uuid4()
    depois = AGORA + timedelta(hours=5)
    primeira = _rodar(repo, sessao, [vazio("cal-a")], rodada=rodada, agora=depois)
    antes = _ev(sessao)
    execucoes_antes = linhas(sessao, "SELECT count(*) AS n FROM execucoes")[0]["n"]

    segunda = _rodar(
        repo, sessao, [vazio("cal-a")], rodada=rodada, agora=depois + timedelta(hours=1)
    )

    assert primeira.ja_registrada is False
    assert segunda.ja_registrada is True
    assert _resumo_vazio(segunda)
    assert _ev(sessao) == antes
    assert antes["ausencias_consecutivas"] == 1
    assert linhas(sessao, "SELECT count(*) AS n FROM execucoes")[0]["n"] == execucoes_antes


def test_mesma_rodada_repetida_nao_duplica_evento_novo(repo: ModuleType, sessao: Session) -> None:
    rodada = uuid.uuid4()
    _rodar(repo, sessao, [ok("cal-a", [evento()])], rodada=rodada)
    segunda = _rodar(repo, sessao, [ok("cal-a", [evento()])], rodada=rodada)
    assert segunda.ja_registrada is True
    assert segunda.novos == []
    assert _contar_eventos(sessao) == 1


def test_rodadas_repetidas_com_o_mesmo_conteudo_nao_alteram(
    repo: ModuleType, sessao: Session
) -> None:
    _rodar(repo, sessao, [ok("cal-a", [evento()])])
    hash_inicial = _ev(sessao)["hash_conteudo"]
    for horas in (5, 10, 15):
        resumo = _rodar(
            repo, sessao, [ok("cal-a", [evento()])], agora=AGORA + timedelta(hours=horas)
        )
        assert _resumo_vazio(resumo)
    linha = _ev(sessao)
    assert linha["estado"] == "ativo"
    assert linha["hash_conteudo"] == hash_inicial
    assert linha["primeira_vez_visto"] == AGORA
    assert linha["ultima_vez_visto"] == AGORA + timedelta(hours=15)
    assert _contar_eventos(sessao) == 1


# --- Vários calendários -----------------------------------------------------------------------


def _em_a_e_b(desc_a: str = "Texto A", desc_b: str = "Texto B") -> list[ResultadoCalendario]:
    return [
        ok("cal-a", [evento(calendario="cal-a", descricao=desc_a)]),
        ok("cal-b", [evento(calendario="cal-b", descricao=desc_b)]),
    ]


def test_dois_calendarios_com_descricoes_diferentes_nao_oscilam(
    repo: ModuleType, sessao: Session
) -> None:
    resumo = _rodar(repo, sessao, _em_a_e_b())
    assert len(resumo.novos) == 1
    linha = _ev(sessao)
    hash_inicial = linha["hash_conteudo"]
    assert linha["calendarios"] == ["cal-a", "cal-b"]
    assert linha["descricoes"] == {"cal-a": "Texto A", "cal-b": "Texto B"}

    for horas in (5, 10, 15):
        resumo = _rodar(repo, sessao, _em_a_e_b(), agora=AGORA + timedelta(hours=horas))
        assert resumo.alterados == []
        assert _ev(sessao)["estado"] == "ativo"
        assert _ev(sessao)["hash_conteudo"] == hash_inicial


def test_calendario_que_falha_nao_faz_o_hash_oscilar(repo: ModuleType, sessao: Session) -> None:
    _rodar(repo, sessao, _em_a_e_b())
    hash_inicial = _ev(sessao)["hash_conteudo"]

    so_a = [ok("cal-a", [evento(calendario="cal-a", descricao="Texto A")]), falha("cal-b")]
    resumo = _rodar(repo, sessao, so_a, agora=AGORA + timedelta(hours=5))
    linha = _ev(sessao)
    assert resumo.alterados == []
    assert linha["estado"] == "ativo"
    assert linha["hash_conteudo"] == hash_inicial
    assert linha["descricoes"] == {"cal-a": "Texto A", "cal-b": "Texto B"}
    assert linha["calendarios"] == ["cal-a", "cal-b"]

    resumo = _rodar(repo, sessao, _em_a_e_b(), agora=AGORA + timedelta(hours=10))
    assert resumo.alterados == []
    assert _ev(sessao)["estado"] == "ativo"
    assert _ev(sessao)["hash_conteudo"] == hash_inicial


def test_descricao_de_outro_calendario_mudou_e_alterado(repo: ModuleType, sessao: Session) -> None:
    _rodar(repo, sessao, _em_a_e_b())
    resumo = _rodar(
        repo, sessao, _em_a_e_b(desc_b="Texto B novo"), agora=AGORA + timedelta(hours=5)
    )
    linha = _ev(sessao)
    assert linha["estado"] == "alterado"
    assert resumo.alterados == [linha["id"]]
    assert linha["descricoes"] == {"cal-a": "Texto A", "cal-b": "Texto B novo"}


def test_calendario_novo_com_descricao_equivalente_nao_altera(
    repo: ModuleType, sessao: Session
) -> None:
    _rodar(repo, sessao, [ok("cal-a", [evento(calendario="cal-a", descricao="Texto A")])])
    linha = _ev(sessao)
    hash_inicial = linha["hash_conteudo"]
    _classificar(sessao, linha["id"])
    assert _para_classificar(repo, sessao) == []

    equivalente = "  Texto   A \r\n"
    for horas in (1, 2):
        resumo = _rodar(
            repo,
            sessao,
            _em_a_e_b(desc_b=equivalente),
            agora=AGORA + timedelta(hours=horas),
        )
        linha = _ev(sessao)
        assert resumo.alterados == []
        assert linha["estado"] == "ativo"
        assert linha["hash_conteudo"] == hash_inicial
        assert linha["calendarios"] == ["cal-a", "cal-b"]
        assert set(linha["descricoes"]) == {"cal-a", "cal-b"}
        assert _para_classificar(repo, sessao) == []


def test_calendario_novo_com_descricao_diferente_altera_uma_vez(
    repo: ModuleType, sessao: Session
) -> None:
    _rodar(repo, sessao, [ok("cal-a", [evento(calendario="cal-a", descricao="Texto A")])])
    linha = _ev(sessao)
    hash_inicial = linha["hash_conteudo"]
    _classificar(sessao, linha["id"])

    resumo = _rodar(repo, sessao, _em_a_e_b(desc_b="Outro texto"), agora=AGORA + timedelta(hours=1))
    linha = _ev(sessao)
    assert resumo.alterados == [linha["id"]]
    assert linha["estado"] == "alterado"
    assert linha["hash_conteudo"] != hash_inicial
    assert _ids(_para_classificar(repo, sessao)) == ["evt-Alfa1"]

    resumo = _rodar(repo, sessao, _em_a_e_b(desc_b="Outro texto"), agora=AGORA + timedelta(hours=2))
    assert resumo.alterados == []
    assert _ev(sessao)["estado"] == "ativo"


def test_calendario_com_descricao_igual_que_falha_mantem_mapa(
    repo: ModuleType, sessao: Session
) -> None:
    _rodar(repo, sessao, _em_a_e_b(desc_b="Texto A"))
    hash_inicial = _ev(sessao)["hash_conteudo"]
    resultados = [ok("cal-a", [evento(calendario="cal-a", descricao="Texto A")]), falha("cal-b")]
    _rodar(repo, sessao, resultados, agora=AGORA + timedelta(hours=1))
    linha = _ev(sessao)
    assert linha["descricoes"] == {"cal-a": "Texto A", "cal-b": "Texto A"}
    assert linha["hash_conteudo"] == hash_inicial
    assert linha["estado"] == "ativo"


def _a_e_b_titulos(com_a: str = "ok") -> list[ResultadoCalendario]:
    """Evento em cal-a ("Título A") e cal-b ("Título B"), mesma descrição.

    `com_a`: "ok" (traz o evento), "falha", "ausente" (fora da lista) ou "sem_evento".
    """
    b = ok("cal-b", [evento(calendario="cal-b", titulo="Título B")])
    a = {
        "ok": [ok("cal-a", [evento(calendario="cal-a", titulo="Título A")])],
        "falha": [falha("cal-a")],
        "ausente": [],
        "sem_evento": [ok("cal-a", [evento("evt-Outro1", "cal-a")])],
    }[com_a]
    return [*a, b]


def test_escalares_nao_oscilam_quando_o_menor_slug_falha(repo: ModuleType, sessao: Session) -> None:
    _rodar(repo, sessao, _a_e_b_titulos())
    linha = _ev(sessao)
    assert linha["titulo"] == "Título A"
    hash_inicial = linha["hash_conteudo"]

    sequencia = ("falha", "ok", "falha", "falha", "ok", "falha", "ok", "falha")
    for horas, com_a in enumerate(sequencia, start=1):
        resumo = _rodar(repo, sessao, _a_e_b_titulos(com_a), agora=AGORA + timedelta(hours=horas))
        linha = _ev(sessao)
        assert linha["titulo"] == "Título A", (horas, com_a)
        assert linha["estado"] == "ativo", (horas, com_a)
        assert linha["hash_conteudo"] == hash_inicial, (horas, com_a)
        assert resumo.alterados == [], (horas, com_a)
        # Contador de rodadas com escalares mantidos: sobe na falha, zera no ok.
        esperado = (
            0 if com_a == "ok" else (2 if sequencia[horas - 2 : horas] == ("falha",) * 2 else 1)
        )
        assert linha["rodadas_escalares_mantidos"] == esperado, (horas, com_a)


def test_calendario_removido_da_lista_nao_congela_escalares(
    repo: ModuleType, sessao: Session
) -> None:
    """Cenário do revisor: cal-a sai do fontes.yaml e cal-b traz o início 20 dias à frente."""
    _rodar(repo, sessao, _em_a_e_b())
    novo_inicio = INICIO_PADRAO + timedelta(days=20)
    so_b = [
        ok("cal-b", [evento(calendario="cal-b", descricao="Texto B", inicio=novo_inicio, fim=None)])
    ]

    resumo = _rodar(repo, sessao, so_b, agora=AGORA + timedelta(hours=1))
    linha = _ev(sessao)
    assert linha["inicio"] == novo_inicio
    assert linha["estado"] == "alterado"
    assert resumo.alterados == [linha["id"]]
    assert linha["rodadas_escalares_mantidos"] == 0

    resumo = _rodar(repo, sessao, so_b, agora=AGORA + timedelta(hours=2))
    assert _ev(sessao)["estado"] == "ativo"
    assert resumo.alterados == []


def test_congelamento_dura_no_maximo_o_limite_de_rodadas(repo: ModuleType, sessao: Session) -> None:
    """cal-a falha sempre: 3 rodadas mantendo o título, na 4ª aceita o de cal-b, depois estável."""
    _rodar(repo, sessao, _a_e_b_titulos())
    evento_id = _ev(sessao)["id"]

    historico = []
    for horas in range(1, 9):
        resumo = _rodar(repo, sessao, _a_e_b_titulos("falha"), agora=AGORA + timedelta(hours=horas))
        linha = _ev(sessao)
        historico.append((linha["titulo"], linha["estado"], linha["rodadas_escalares_mantidos"]))
        assert resumo.alterados == ([evento_id] if horas == 4 else []), horas

    assert historico[:4] == [
        ("Título A", "ativo", 1),
        ("Título A", "ativo", 2),
        ("Título A", "ativo", 3),
        ("Título B", "alterado", 0),
    ]
    assert all(titulo == "Título B" for titulo, _, _ in historico[4:])
    assert all(estado == "ativo" for _, estado, _ in historico[4:])


def test_terminou_usa_o_inicio_atualizado_depois_do_descongelamento(
    repo: ModuleType, sessao: Session
) -> None:
    original = INICIO_PADRAO
    adiado = INICIO_PADRAO + timedelta(days=20)
    a = evento(calendario="cal-a", inicio=original, fim=None)
    _rodar(
        repo,
        sessao,
        [ok("cal-a", [a]), ok("cal-b", [evento(calendario="cal-b", inicio=original, fim=None)])],
    )

    b_adiado = ok("cal-b", [evento(calendario="cal-b", inicio=adiado, fim=None)])
    for horas in range(1, 5):
        _rodar(repo, sessao, [falha("cal-a"), b_adiado], agora=AGORA + timedelta(hours=horas))
    assert _ev(sessao)["inicio"] == adiado

    # Passou a data original, mas não a nova: continua ativo (com cal-a ainda falhando).
    depois_do_original = original + timedelta(days=1)
    _rodar(repo, sessao, [falha("cal-a"), b_adiado], [], agora=depois_do_original)
    linha = _ev(sessao)
    assert linha["estado"] == "ativo"
    assert linha["inicio"] == adiado

    _rodar(repo, sessao, [falha("cal-a"), b_adiado], [], agora=adiado + timedelta(minutes=1))
    assert _ev(sessao)["estado"] == "encerrado"


def test_evento_que_sai_do_menor_slug_passa_a_usar_o_proximo(
    repo: ModuleType, sessao: Session
) -> None:
    _rodar(repo, sessao, _a_e_b_titulos())
    resumo = _rodar(repo, sessao, _a_e_b_titulos("sem_evento"), agora=AGORA + timedelta(hours=1))
    linha = _ev(sessao)
    assert linha["titulo"] == "Título B"
    assert linha["estado"] == "alterado"
    assert resumo.alterados == [linha["id"]]

    resumo = _rodar(repo, sessao, _a_e_b_titulos("sem_evento"), agora=AGORA + timedelta(hours=2))
    linha = _ev(sessao)
    assert linha["titulo"] == "Título B"
    assert linha["estado"] == "ativo"
    assert resumo.alterados == []


def test_evento_em_a_e_b_que_some_de_a_continua_visto(repo: ModuleType, sessao: Session) -> None:
    _rodar(repo, sessao, _em_a_e_b())
    hash_inicial = _ev(sessao)["hash_conteudo"]
    resultados = [
        ok("cal-a", [evento("evt-Outro1", "cal-a")]),
        ok("cal-b", [evento(calendario="cal-b", descricao="Texto B")]),
    ]
    resumo = _rodar(repo, sessao, resultados, agora=AGORA + timedelta(hours=5))
    linha = _ev(sessao)
    assert linha["estado"] == "ativo"
    assert linha["ausencias_consecutivas"] == 0
    assert linha["calendarios"] == ["cal-a", "cal-b"]
    assert linha["hash_conteudo"] == hash_inicial
    assert linha["id"] not in resumo.alterados


def test_calendarios_so_crescem_e_escalares_vem_do_menor_slug(
    repo: ModuleType, sessao: Session
) -> None:
    _rodar(repo, sessao, [ok("cal-b", [evento(calendario="cal-b", titulo="Título B")])])
    assert _ev(sessao)["titulo"] == "Título B"

    resultados = [
        ok("cal-a", [evento(calendario="cal-a", titulo="Título A")]),
        ok("cal-b", [evento(calendario="cal-b", titulo="Título B")]),
    ]
    _rodar(repo, sessao, resultados, agora=AGORA + timedelta(hours=5))
    linha = _ev(sessao)
    assert linha["calendarios"] == ["cal-a", "cal-b"]
    assert linha["titulo"] == "Título A"

    resultados = [ok("cal-b", [evento(calendario="cal-b", titulo="Título B")]), vazio("cal-a")]
    _rodar(repo, sessao, resultados, agora=AGORA + timedelta(hours=10))
    assert _ev(sessao)["calendarios"] == ["cal-a", "cal-b"]


# --- Ausências --------------------------------------------------------------------------------


def test_calendario_com_falha_nao_conta_ausencia(repo: ModuleType, sessao: Session) -> None:
    _rodar(repo, sessao, [ok("cal-a", [evento()])])
    for horas in range(1, 6):
        _rodar(repo, sessao, [falha("cal-a")], agora=AGORA + timedelta(hours=horas))
    linha = _ev(sessao)
    assert linha["estado"] == "ativo"
    assert linha["ausencias_consecutivas"] == 0


def test_falha_em_um_dos_calendarios_do_evento_nao_conta_ausencia(
    repo: ModuleType, sessao: Session
) -> None:
    _rodar(repo, sessao, _em_a_e_b())
    for horas in range(1, 6):
        resultados = [ok("cal-a", [evento("evt-Outro1", "cal-a")]), falha("cal-b")]
        _rodar(repo, sessao, resultados, agora=AGORA + timedelta(hours=horas))
    linha = _ev(sessao)
    assert linha["estado"] == "ativo"
    assert linha["ausencias_consecutivas"] == 0


def test_rodada_sem_o_calendario_do_evento_nao_conta_ausencia(
    repo: ModuleType, sessao: Session
) -> None:
    """Sem leitura bem-sucedida de todos os calendários do evento, não há evidência de sumiço."""
    _rodar(repo, sessao, [ok("cal-a", [evento()])])
    for horas in range(1, 6):
        _rodar(repo, sessao, [vazio("cal-z")], agora=AGORA + timedelta(hours=horas))
    assert _ev(sessao)["ausencias_consecutivas"] == 0


def test_ausencia_contavel_leva_a_nao_encontrado_e_reaparecimento(
    repo: ModuleType, sessao: Session
) -> None:
    _rodar(repo, sessao, [ok("cal-a", [evento()])])
    evento_id = _ev(sessao)["id"]

    historico = []
    for horas in (1, 2, 3):
        resumo = _rodar(repo, sessao, [vazio("cal-a")], agora=AGORA + timedelta(hours=horas))
        linha = _ev(sessao)
        historico.append((linha["estado"], linha["ausencias_consecutivas"]))
    assert historico == [("ativo", 1), ("ativo", 2), ("nao_encontrado", 3)]
    assert resumo.nao_encontrados == [evento_id]

    resumo = _rodar(repo, sessao, [vazio("cal-a")], agora=AGORA + timedelta(hours=4))
    assert resumo.nao_encontrados == []
    assert _ev(sessao)["ausencias_consecutivas"] == 4

    resumo = _rodar(repo, sessao, [ok("cal-a", [evento()])], agora=AGORA + timedelta(hours=5))
    linha = _ev(sessao)
    assert (linha["estado"], linha["ausencias_consecutivas"]) == ("ativo", 0)
    assert resumo.reativados == [evento_id]
    assert resumo.novos == []
    assert _contar_eventos(sessao) == 1


def test_reaparecimento_nao_aprovado_tambem_reativa(repo: ModuleType, sessao: Session) -> None:
    """Existentes são atualizados mesmo se filtrados nesta rodada."""
    _rodar(repo, sessao, [ok("cal-a", [evento()])])
    for horas in (1, 2, 3):
        _rodar(repo, sessao, [vazio("cal-a")], agora=AGORA + timedelta(hours=horas))
    assert _ev(sessao)["estado"] == "nao_encontrado"
    _rodar(repo, sessao, [ok("cal-a", [evento()])], [], agora=AGORA + timedelta(hours=4))
    assert (_ev(sessao)["estado"], _ev(sessao)["ausencias_consecutivas"]) == ("ativo", 0)


def test_ok_sem_o_evento_conta_ausencia(repo: ModuleType, sessao: Session) -> None:
    _rodar(repo, sessao, [ok("cal-a", [evento()])])
    resultados = [ok("cal-a", [evento("evt-Outro1")])]
    _rodar(repo, sessao, resultados, agora=AGORA + timedelta(hours=1))
    assert _ev(sessao)["ausencias_consecutivas"] == 1


def test_vazio_conta_como_coleta_ok(repo: ModuleType, sessao: Session) -> None:
    _rodar(repo, sessao, [ok("cal-a", [evento()])])
    _rodar(repo, sessao, [vazio("cal-a")], agora=AGORA + timedelta(hours=1), limite=1)
    assert _ev(sessao)["estado"] == "nao_encontrado"


def test_evento_invalido_de_calendario_ok_conta_como_visto(
    repo: ModuleType, sessao: Session
) -> None:
    _rodar(repo, sessao, [ok("cal-a", [evento()])])
    for horas in (1, 2):
        _rodar(repo, sessao, [vazio("cal-a")], agora=AGORA + timedelta(hours=horas))
    assert _ev(sessao)["ausencias_consecutivas"] == 2
    titulo = _ev(sessao)["titulo"]

    resultados = [ok("cal-a", [evento("evt-Outro1")], invalidos=["evt-Alfa1"])]
    resumo = _rodar(repo, sessao, resultados, agora=AGORA + timedelta(hours=3))
    linha = _ev(sessao)
    assert (linha["estado"], linha["ausencias_consecutivas"]) == ("ativo", 0)
    assert linha["titulo"] == titulo
    assert linha["id"] not in resumo.alterados


def test_evento_invalido_nao_conhecido_nao_e_inserido(repo: ModuleType, sessao: Session) -> None:
    _rodar(repo, sessao, [ok("cal-a", [evento()], invalidos=["evt-Novo9"])])
    assert linhas(sessao, "SELECT id FROM eventos WHERE id_fonte = 'evt-Novo9'") == []


# --- Encerramento -----------------------------------------------------------------------------


def test_evento_filtrado_que_terminou_e_encerrado(repo: ModuleType, sessao: Session) -> None:
    obs = evento()
    _rodar(repo, sessao, [ok("cal-a", [obs])])
    depois_do_fim = obs.fim + timedelta(minutes=1)
    resumo = _rodar(repo, sessao, [ok("cal-a", [obs])], [], agora=depois_do_fim)
    linha = _ev(sessao)
    assert linha["estado"] == "encerrado"
    assert resumo.encerrados == [linha["id"]]


def test_evento_nao_visto_que_terminou_e_encerrado(repo: ModuleType, sessao: Session) -> None:
    obs = evento()
    _rodar(repo, sessao, [ok("cal-a", [obs])])
    resumo = _rodar(repo, sessao, [vazio("cal-a")], agora=obs.fim, limite=1)
    linha = _ev(sessao)
    assert linha["estado"] == "encerrado"
    assert resumo.encerrados == [linha["id"]]
    assert resumo.nao_encontrados == []


def test_evento_terminado_com_calendario_em_falha_e_encerrado(
    repo: ModuleType, sessao: Session
) -> None:
    obs = evento()
    _rodar(repo, sessao, [ok("cal-a", [obs])])
    _rodar(repo, sessao, [falha("cal-a")], agora=obs.fim + timedelta(days=1))
    assert _ev(sessao)["estado"] == "encerrado"


def test_encerrado_e_terminal(repo: ModuleType, sessao: Session) -> None:
    obs = evento()
    _rodar(repo, sessao, [ok("cal-a", [obs])])
    _rodar(repo, sessao, [vazio("cal-a")], agora=obs.fim + timedelta(hours=1))
    resumo = _rodar(
        repo,
        sessao,
        [ok("cal-a", [evento(status_fonte="cancelado", titulo="Outro")])],
        [],
        agora=obs.fim + timedelta(hours=2),
    )
    assert _ev(sessao)["estado"] == "encerrado"
    assert _resumo_vazio(resumo)


def test_dia_inteiro_sem_fim_termina_na_meia_noite_seguinte(
    repo: ModuleType, sessao: Session
) -> None:
    dia = datetime(2026, 12, 5, 0, 0, tzinfo=SP)
    obs = evento(inicio=dia, fim=None, dia_inteiro=True)
    _rodar(repo, sessao, [ok("cal-a", [obs])])

    _rodar(repo, sessao, [vazio("cal-a")], agora=dia + timedelta(hours=23, minutes=59))
    assert _ev(sessao)["estado"] == "ativo"
    _rodar(repo, sessao, [vazio("cal-a")], agora=dia + timedelta(days=1))
    assert _ev(sessao)["estado"] == "encerrado"


def test_evento_com_hora_sem_fim_termina_no_inicio(repo: ModuleType, sessao: Session) -> None:
    obs = evento(fim=None)
    _rodar(repo, sessao, [ok("cal-a", [obs])])
    _rodar(repo, sessao, [vazio("cal-a")], agora=obs.inicio - timedelta(minutes=1))
    assert _ev(sessao)["estado"] == "ativo"
    _rodar(repo, sessao, [vazio("cal-a")], agora=obs.inicio)
    assert _ev(sessao)["estado"] == "encerrado"


# --- Cancelado e alterado ---------------------------------------------------------------------


def test_cancelado_quando_a_fonte_diz(repo: ModuleType, sessao: Session) -> None:
    _rodar(repo, sessao, [ok("cal-a", [evento()])])
    resumo = _rodar(
        repo,
        sessao,
        [ok("cal-a", [evento(status_fonte="cancelado")])],
        agora=AGORA + timedelta(hours=1),
    )
    linha = _ev(sessao)
    assert linha["estado"] == "cancelado"
    assert linha["status_fonte"] == "cancelado"
    assert resumo.cancelados == [linha["id"]]


def test_cancelado_que_termina_vira_encerrado(repo: ModuleType, sessao: Session) -> None:
    """Data passada vence tudo, inclusive o cancelamento."""
    obs = evento(status_fonte="cancelado")
    _rodar(repo, sessao, [ok("cal-a", [obs])])
    resumo = _rodar(repo, sessao, [vazio("cal-a")], agora=obs.fim + timedelta(days=1), limite=1)
    linha = _ev(sessao)
    assert linha["estado"] == "encerrado"
    assert resumo.encerrados == [linha["id"]]


def test_cancelado_visto_ainda_cancelado_depois_da_data_vira_encerrado(
    repo: ModuleType, sessao: Session
) -> None:
    obs = evento(status_fonte="cancelado")
    _rodar(repo, sessao, [ok("cal-a", [obs])])
    _rodar(repo, sessao, [ok("cal-a", [obs])], [], agora=obs.fim + timedelta(hours=1))
    assert _ev(sessao)["estado"] == "encerrado"


def test_cancelado_que_deixa_de_ser_depois_da_data_vira_encerrado(
    repo: ModuleType, sessao: Session
) -> None:
    _rodar(repo, sessao, [ok("cal-a", [evento(status_fonte="cancelado")])])
    assert _ev(sessao)["estado"] == "cancelado"
    onze_dias_depois = AGORA + timedelta(days=11)
    resumo = _rodar(repo, sessao, [ok("cal-a", [evento()])], [], agora=onze_dias_depois)
    linha = _ev(sessao)
    assert linha["estado"] == "encerrado"
    assert linha["id"] not in resumo.reativados
    assert _para_classificar(repo, sessao) == []


def test_cancelado_so_volta_a_ativo_quando_visto_sem_cancelamento(
    repo: ModuleType, sessao: Session
) -> None:
    _rodar(repo, sessao, [ok("cal-a", [evento(status_fonte="cancelado")])])
    assert _ev(sessao)["estado"] == "cancelado"

    # Ausência (contável ou não) nunca tira do cancelado.
    for horas, resultado in ((1, vazio("cal-a")), (2, falha("cal-a")), (3, vazio("cal-a"))):
        resumo = _rodar(repo, sessao, [resultado], agora=AGORA + timedelta(hours=horas), limite=1)
        assert _ev(sessao)["estado"] == "cancelado"
        assert resumo.reativados == []
    # Visto ainda cancelado: continua cancelado.
    _rodar(
        repo,
        sessao,
        [ok("cal-a", [evento(status_fonte="cancelado")])],
        agora=AGORA + timedelta(hours=4),
    )
    assert _ev(sessao)["estado"] == "cancelado"
    # Visto numa coleta válida sem cancelamento: volta a ativo.
    _rodar(repo, sessao, [ok("cal-a", [evento()])], agora=AGORA + timedelta(hours=5))
    linha = _ev(sessao)
    assert (linha["estado"], linha["ausencias_consecutivas"]) == ("ativo", 0)
    assert linha["status_fonte"] == "desconhecido"


def test_cancelado_em_um_calendario_basta(repo: ModuleType, sessao: Session) -> None:
    _rodar(repo, sessao, _em_a_e_b())
    resultados = [
        ok("cal-a", [evento(calendario="cal-a", descricao="Texto A")]),
        ok("cal-b", [evento(calendario="cal-b", descricao="Texto B", status_fonte="cancelado")]),
    ]
    _rodar(repo, sessao, resultados, agora=AGORA + timedelta(hours=1))
    assert _ev(sessao)["estado"] == "cancelado"


def test_alterado_e_depois_ativo(repo: ModuleType, sessao: Session) -> None:
    _rodar(repo, sessao, [ok("cal-a", [evento()])])
    hash_inicial = _ev(sessao)["hash_conteudo"]

    resumo = _rodar(
        repo,
        sessao,
        [ok("cal-a", [evento(titulo="Novo título")])],
        agora=AGORA + timedelta(hours=1),
    )
    linha = _ev(sessao)
    assert linha["estado"] == "alterado"
    assert linha["titulo"] == "Novo título"
    assert linha["hash_conteudo"] != hash_inicial
    assert linha["atualizado_em"] == AGORA + timedelta(hours=1)
    assert resumo.alterados == [linha["id"]]

    resumo = _rodar(
        repo,
        sessao,
        [ok("cal-a", [evento(titulo="Novo título")])],
        agora=AGORA + timedelta(hours=2),
    )
    assert _ev(sessao)["estado"] == "ativo"
    assert resumo.alterados == []


def test_mudanca_fora_do_hash_nao_altera_mas_e_gravada(repo: ModuleType, sessao: Session) -> None:
    _rodar(repo, sessao, [ok("cal-a", [evento()])])
    _rodar(
        repo,
        sessao,
        [ok("cal-a", [evento(organizador="Novo organizador")])],
        agora=AGORA + timedelta(hours=1),
    )
    linha = _ev(sessao)
    assert linha["estado"] == "ativo"
    assert linha["organizador"] == "Novo organizador"


def test_existente_filtrado_e_atualizado(repo: ModuleType, sessao: Session) -> None:
    _rodar(repo, sessao, [ok("cal-a", [evento()])])
    mudado = evento(titulo="Agora em outro lugar", local_texto="Rua X, Rio de Janeiro - RJ")
    resumo = _rodar(repo, sessao, [ok("cal-a", [mudado])], [], agora=AGORA + timedelta(hours=1))
    linha = _ev(sessao)
    assert linha["titulo"] == "Agora em outro lugar"
    assert linha["estado"] == "alterado"
    assert resumo.novos == []


# --- Localização ------------------------------------------------------------------------------

HELSINQUE = {
    "local_texto": "Kamppi, Helsinki, Finland",
    "latitude": 60.1699,
    "longitude": 24.9384,
}


def test_localizacao_recalculada_vai_para_fora_e_volta(repo: ModuleType, sessao: Session) -> None:
    _rodar(repo, sessao, [ok("cal-a", [evento()])])
    assert _ids(_para_classificar(repo, sessao)) == ["evt-Alfa1"]

    _rodar(repo, sessao, [ok("cal-a", [evento(**HELSINQUE)])], [], agora=AGORA + timedelta(hours=1))
    linha = _ev(sessao)
    assert (linha["localizacao"], linha["evidencia_localizacao"]) == ("fora", "geo")
    assert linha["local_texto"] == HELSINQUE["local_texto"]
    assert _para_classificar(repo, sessao) == []

    _rodar(repo, sessao, [ok("cal-a", [evento()])], agora=AGORA + timedelta(hours=2))
    linha = _ev(sessao)
    assert (linha["localizacao"], linha["evidencia_localizacao"]) == ("sp", "geo")
    assert _ids(_para_classificar(repo, sessao)) == ["evt-Alfa1"]


def test_localizacao_recalculada_mesmo_sem_mudanca_de_conteudo(
    repo: ModuleType, sessao: Session
) -> None:
    _rodar(repo, sessao, [ok("cal-a", [evento()])])
    sessao.execute(
        text("UPDATE eventos SET localizacao = 'indefinida', evidencia_localizacao = 'nenhuma'")
    )
    _rodar(repo, sessao, [ok("cal-a", [evento()])], [], agora=AGORA + timedelta(hours=1))
    linha = _ev(sessao)
    assert (linha["localizacao"], linha["evidencia_localizacao"]) == ("sp", "geo")
    assert linha["estado"] == "ativo"


def test_localizacao_por_texto_fora_de_sp(repo: ModuleType, sessao: Session) -> None:
    _rodar(repo, sessao, [ok("cal-a", [evento()])])
    sem_geo_no_rio = evento(local_texto="Rua X, 10 - RJ", latitude=None, longitude=None)
    _rodar(repo, sessao, [ok("cal-a", [sem_geo_no_rio])], [], agora=AGORA + timedelta(hours=1))
    linha = _ev(sessao)
    assert (linha["localizacao"], linha["evidencia_localizacao"]) == ("fora", "texto")


def test_evento_fora_nao_entra_na_fila(repo: ModuleType, sessao: Session) -> None:
    _rodar(repo, sessao, [ok("cal-a", [evento()])])
    sessao.execute(text("UPDATE eventos SET localizacao = 'fora', evidencia_localizacao = 'geo'"))
    assert _para_classificar(repo, sessao) == []


def test_novo_evento_fora_de_sp_nao_aprovado_nao_e_gravado(
    repo: ModuleType, sessao: Session
) -> None:
    _rodar(repo, sessao, [ok("cal-a", [evento(**HELSINQUE)])], [])
    assert _contar_eventos(sessao) == 0


# --- Horários e fusos -------------------------------------------------------------------------


def test_horarios_com_fuso(repo: ModuleType, sessao: Session) -> None:
    obs = evento()
    agora_utc = AGORA.astimezone(UTC)
    _rodar(repo, sessao, [ok("cal-a", [obs])], agora=agora_utc)
    linha = _ev(sessao)
    for coluna in ("inicio", "fim", "primeira_vez_visto", "ultima_vez_visto", "atualizado_em"):
        assert linha[coluna].utcoffset() is not None, coluna
    assert linha["inicio"] == obs.inicio
    assert linha["primeira_vez_visto"] == AGORA


# --- Erros de entrada -------------------------------------------------------------------------


def test_agora_sem_fuso_e_erro(repo: ModuleType, sessao: Session) -> None:
    with pytest.raises(ValueError):
        _rodar(repo, sessao, [ok("cal-a", [evento()])], agora=datetime(2026, 11, 25, 12, 0))


def test_calendario_repetido_e_erro(repo: ModuleType, sessao: Session) -> None:
    with pytest.raises(ValueError):
        _rodar(repo, sessao, [ok("cal-a", [evento()]), vazio("cal-a")])


def test_aprovado_de_calendario_com_falha_e_erro(repo: ModuleType, sessao: Session) -> None:
    with pytest.raises(ValueError):
        _rodar(repo, sessao, [falha("cal-a")], [localizado(evento())])


def test_aprovado_de_calendario_ausente_e_erro(repo: ModuleType, sessao: Session) -> None:
    with pytest.raises(ValueError):
        _rodar(repo, sessao, [ok("cal-a", [evento()])], [localizado(evento(calendario="cal-z"))])


def test_aprovado_que_nao_esta_no_resultado_e_erro(repo: ModuleType, sessao: Session) -> None:
    with pytest.raises(ValueError):
        _rodar(repo, sessao, [ok("cal-a", [evento()])], [localizado(evento("evt-Fora1"))])


def test_erro_de_entrada_nao_grava_nada(repo: ModuleType, sessao: Session) -> None:
    with pytest.raises(ValueError):
        _rodar(repo, sessao, [ok("cal-a", [evento()]), vazio("cal-a")])
    assert _contar_eventos(sessao) == 0
    assert linhas(sessao, "SELECT count(*) AS n FROM execucoes")[0]["n"] == 0


def test_registrar_rodada_nao_faz_commit(repo: ModuleType, sessao: Session) -> None:
    commits: list[object] = []
    ouvinte = commits.append
    event.listen(sessao, "after_commit", ouvinte)
    try:
        _rodar(repo, sessao, [ok("cal-a", [evento()])])
    finally:
        event.remove(sessao, "after_commit", ouvinte)
    assert commits == []
    assert _contar_eventos(sessao) == 1  # flush feito: visível na mesma transação


# --- eventos_para_classificar -----------------------------------------------------------------

CHAVE = {"versao_prompt": "v1", "modelo": "modelo-falso", "versao_schema": "1"}


def _para_classificar(repo: ModuleType, sessao: Session, limite: int = 50, **chave: str) -> list:
    return repo.eventos_para_classificar(sessao, **{**CHAVE, **chave}, limite=limite)


def _classificar(sessao: Session, evento_id: int, situacao: str = "ok", **campos: str) -> None:
    linha = _ev_por_id(sessao, evento_id)
    valores: dict[str, Any] = {
        "evento_id": evento_id,
        "rodada": uuid.uuid4(),
        "criada_em": AGORA,
        "hash_conteudo": linha["hash_conteudo"],
        **CHAVE,
        "situacao": situacao,
        "resultado": "{}" if situacao == "ok" else None,
        "motivo_erro": None if situacao == "ok" else "saida_invalida",
        "tokens_entrada": 10,
        "tokens_saida": 5,
        "latencia_ms": 100,
        "custo_usd": "0.000100",
    }
    valores.update(campos)
    colunas = ", ".join(valores)
    marcadores = ", ".join(f"CAST(:{c} AS jsonb)" if c == "resultado" else f":{c}" for c in valores)
    sessao.execute(text(f"INSERT INTO classificacoes ({colunas}) VALUES ({marcadores})"), valores)


def _ev_por_id(sessao: Session, evento_id: int) -> dict[str, Any]:
    return linhas(sessao, "SELECT * FROM eventos WHERE id = :id", id=evento_id)[0]


def _ids(eventos: list) -> list[str]:
    return [e.id_fonte for e in eventos]


def test_novo_evento_precisa_de_classificacao(repo: ModuleType, sessao: Session) -> None:
    _rodar(repo, sessao, [ok("cal-a", [evento()])])
    resultado = _para_classificar(repo, sessao)
    assert _ids(resultado) == ["evt-Alfa1"]
    from radar.banco.modelos import Evento

    assert isinstance(resultado[0], Evento)


def test_classificacao_ok_com_a_chave_atual_tira_da_fila(repo: ModuleType, sessao: Session) -> None:
    _rodar(repo, sessao, [ok("cal-a", [evento()])])
    _classificar(sessao, _ev(sessao)["id"])
    assert _para_classificar(repo, sessao) == []


def test_classificacao_com_erro_nao_tira_da_fila(repo: ModuleType, sessao: Session) -> None:
    _rodar(repo, sessao, [ok("cal-a", [evento()])])
    _classificar(sessao, _ev(sessao)["id"], situacao="erro")
    _classificar(sessao, _ev(sessao)["id"], situacao="erro")
    assert _ids(_para_classificar(repo, sessao)) == ["evt-Alfa1"]


@pytest.mark.parametrize(
    "chave_antiga",
    [
        {"versao_prompt": "v0"},
        {"modelo": "modelo-antigo"},
        {"versao_schema": "0"},
        {"hash_conteudo": "f" * 64},
    ],
)
def test_classificacao_com_outra_chave_nao_vale(
    repo: ModuleType, sessao: Session, chave_antiga: dict[str, str]
) -> None:
    _rodar(repo, sessao, [ok("cal-a", [evento()])])
    _classificar(sessao, _ev(sessao)["id"], **chave_antiga)
    assert _ids(_para_classificar(repo, sessao)) == ["evt-Alfa1"]


def test_evento_alterado_volta_para_a_fila(repo: ModuleType, sessao: Session) -> None:
    _rodar(repo, sessao, [ok("cal-a", [evento()])])
    _classificar(sessao, _ev(sessao)["id"])
    assert _para_classificar(repo, sessao) == []
    _rodar(repo, sessao, [ok("cal-a", [evento(titulo="Mudou")])], agora=AGORA + timedelta(hours=1))
    assert _ev(sessao)["estado"] == "alterado"
    assert _ids(_para_classificar(repo, sessao)) == ["evt-Alfa1"]


def test_alterado_sem_classificacao_entra(repo: ModuleType, sessao: Session) -> None:
    _rodar(repo, sessao, [ok("cal-a", [evento()])])
    sessao.execute(text("UPDATE eventos SET estado = 'alterado'"))
    assert _ids(_para_classificar(repo, sessao)) == ["evt-Alfa1"]


@pytest.mark.parametrize("estado", ["cancelado", "encerrado", "nao_encontrado"])
def test_estados_fora_da_fila(repo: ModuleType, sessao: Session, estado: str) -> None:
    _rodar(repo, sessao, [ok("cal-a", [evento()])])
    sessao.execute(text("UPDATE eventos SET estado = :e"), {"e": estado})
    assert _para_classificar(repo, sessao) == []


def test_fila_ordenada_por_inicio_e_limitada(repo: ModuleType, sessao: Session) -> None:
    eventos = [
        evento("evt-Tarde1", inicio=INICIO_PADRAO + timedelta(days=3), fim=None),
        evento("evt-Cedo1", inicio=INICIO_PADRAO, fim=None),
        evento("evt-Meio1", inicio=INICIO_PADRAO + timedelta(days=1), fim=None),
    ]
    _rodar(repo, sessao, [ok("cal-a", eventos)])
    assert _ids(_para_classificar(repo, sessao)) == ["evt-Cedo1", "evt-Meio1", "evt-Tarde1"]
    assert _ids(_para_classificar(repo, sessao, limite=2)) == ["evt-Cedo1", "evt-Meio1"]


def test_fila_mistura_classificados_e_pendentes(repo: ModuleType, sessao: Session) -> None:
    eventos = [
        evento("evt-Um1"),
        evento("evt-Dois2", inicio=INICIO_PADRAO + timedelta(days=1), fim=None),
    ]
    _rodar(repo, sessao, [ok("cal-a", eventos)])
    _classificar(sessao, _ev(sessao, "evt-Um1")["id"])
    assert _ids(_para_classificar(repo, sessao)) == ["evt-Dois2"]
