"""Testes do F1-06 — classificador (cliente falso; nenhum teste chama a API).

Assinaturas fixadas (radar.classificacao.classificador):
- `validar_contra_evento(saida: SaidaClassificador, texto_fonte: str,
  localizacao_filtro: str) -> str | None`: None se ok; senão o motivo
  ("saida_evidencia_nao_encontrada:<campo>" ou "saida_localizacao_ausente"). Trecho não
  vazio precisa aparecer no texto_fonte depois de normalizar os dois (NFC, espaços
  colapsados, casefold, reticências "..."/"…" nas pontas do trecho removidas).
- `ResumoClassificacao`: chamadas: int, ok: list[int], erros: list[tuple[int, str]],
  interrompido_por: "autenticacao" | "rate_limit" | "falhas_api_seguidas" | None,
  restantes: bool, custo_total_usd: Decimal.
- `classificar_pendentes(sessao, cliente, config, agora, rodada, relogio=time.monotonic)
  -> ResumoClassificacao`: limite = `config.limite_chamadas_llm_por_execucao`; só `flush`.
- A chave (versão do prompt, modelo, versão do schema) é lida em tempo de chamada de
  `radar.classificacao.prompt.VERSAO_PROMPT`, `radar.classificacao.modelos.MODELO` e
  `radar.classificacao.modelos.VERSAO_SCHEMA` (monkeypatch nesses módulos muda a chave), e o
  custo usa `radar.classificacao.precos.TABELA_PRECOS` em tempo de chamada.
- `ErroAPI(categoria=..., status_http=...)` (construtor por palavra-chave).
- F1-07b: `classificar_pendentes` NÃO aplica a regra de comunidade de IA (aplicada na
  leitura, F1-10): o `resultado` ok gravado é a saída do LLM, validável por
  `SaidaClassificador` e sem chaves extras. O texto bruto do LLM nunca é gravado.
- Toda categoria de ErroAPI fora de autenticacao/rate_limit (servidor, timeout, conexao,
  requisicao, outro) conta para as 3 falhas seguidas; nenhuma delas interrompe na primeira.
- Tokens (inclusive de erros de saída) somam por execução; antes de cada chamada, acumulado
  >= `config.limite_tokens_entrada_por_execucao` ou `..._saida_...` →
  `interrompido_por="limite_tokens"` e `restantes=True` se houver pendentes.
- `max_tokens` enviado ao cliente = 1024.
"""

from __future__ import annotations

import importlib
import json
import unicodedata
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from types import ModuleType
from typing import Any

import pytest
from banco.conftest import linhas
from sqlalchemy.orm import Session

from radar.config import Configuracao

from .fabricas import AGORA, DESCRICAO, INICIO_PADRAO, LOCAL_TEXTO, campos_evento, saida_valida

TITULO_SECRETO = "Segredo Zeta 7731"
PEDACOS_SECRETOS = ("Segredo", "Zeta", "7731", "Paulista", "gratuito", "linguagem")


# --- Módulos -------------------------------------------------------------------------------


@pytest.fixture
def classificador() -> ModuleType:
    return importlib.import_module("radar.classificacao.classificador")


@pytest.fixture
def cliente_mod() -> ModuleType:
    return importlib.import_module("radar.classificacao.cliente")


@pytest.fixture
def modelos() -> ModuleType:
    return importlib.import_module("radar.classificacao.modelos")


@pytest.fixture
def prompt() -> ModuleType:
    return importlib.import_module("radar.classificacao.prompt")


# --- Relógio e cliente falsos --------------------------------------------------------------


@dataclass
class Relogio:
    agora: float = 1_000.0

    def __call__(self) -> float:
        return self.agora


@dataclass
class Chamada:
    system: str
    mensagem: str
    schema: dict[str, Any]
    max_tokens: int


@dataclass
class ClienteFalso:
    """Devolve as respostas na ordem (ErroAPI é levantado); depois repete `padrao`.

    Cada chamada avança o relógio em 0,25 s (latência esperada: 250 ms).
    """

    padrao: Any
    relogio: Relogio
    fila: list[Any] = field(default_factory=list)
    chamadas: list[Chamada] = field(default_factory=list)

    def classificar(
        self, system: str, mensagem: str, schema: dict[str, Any], max_tokens: int
    ) -> Any:
        self.chamadas.append(Chamada(system, mensagem, schema, max_tokens))
        self.relogio.agora += 0.25
        item = self.fila.pop(0) if self.fila else self.padrao
        if isinstance(item, Exception):
            raise item
        return item


def _resposta(
    cliente_mod: ModuleType,
    dados: dict[str, Any] | None = None,
    *,
    texto: str | None = None,
    stop_reason: str | None = "end_turn",
    tokens_entrada: int | None = 1_000,
    tokens_saida: int | None = 200,
) -> Any:
    return cliente_mod.RespostaLLM(
        texto=texto if texto is not None else json.dumps(dados or saida_valida()),
        stop_reason=stop_reason,
        tokens_entrada=tokens_entrada,
        tokens_saida=tokens_saida,
    )


def _erro_api(cliente_mod: ModuleType, categoria: str, status: int | None = None) -> Exception:
    return cliente_mod.ErroAPI(categoria=categoria, status_http=status)


@pytest.fixture
def relogio() -> Relogio:
    return Relogio()


@pytest.fixture
def cliente(cliente_mod: ModuleType, relogio: Relogio) -> ClienteFalso:
    return ClienteFalso(padrao=_resposta(cliente_mod), relogio=relogio)


# --- Banco ---------------------------------------------------------------------------------

_contador = iter(range(1, 1_000_000))


def _inserir(sessao: Session, **campos: Any) -> Any:
    from radar.banco.modelos import Evento

    campos.setdefault("id_fonte", f"evt-Teste{next(_contador)}")
    linha = Evento(**campos_evento(**campos))
    sessao.add(linha)
    sessao.flush()
    return linha


def _rodar(
    classificador: ModuleType,
    sessao: Session,
    cliente: ClienteFalso,
    *,
    limite: int = 50,
    agora: datetime = AGORA,
    rodada: uuid.UUID | None = None,
    **config: Any,
) -> Any:
    return classificador.classificar_pendentes(
        sessao,
        cliente,
        Configuracao(limite_chamadas_llm_por_execucao=limite, **config),
        agora,
        rodada or uuid.uuid4(),
        relogio=cliente.relogio,
    )


def _classificacoes(sessao: Session, evento_id: int | None = None) -> list[dict[str, Any]]:
    if evento_id is None:
        return linhas(sessao, "SELECT * FROM classificacoes ORDER BY id")
    return linhas(
        sessao, "SELECT * FROM classificacoes WHERE evento_id = :e ORDER BY id", e=evento_id
    )


@pytest.fixture
def sem_commit(sessao: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    def proibido() -> None:
        raise AssertionError("classificar_pendentes só pode fazer flush")

    monkeypatch.setattr(sessao, "commit", proibido)
    yield


# --- Caminho feliz -------------------------------------------------------------------------


def test_evento_novo_e_classificado(
    classificador: ModuleType,
    modelos: ModuleType,
    prompt: ModuleType,
    sessao: Session,
    cliente: ClienteFalso,
    sem_commit: None,
) -> None:
    evento = _inserir(sessao)
    rodada = uuid.uuid4()
    resumo = _rodar(classificador, sessao, cliente, rodada=rodada)

    assert resumo.chamadas == 1
    assert resumo.ok == [evento.id]
    assert resumo.erros == []
    assert resumo.interrompido_por is None
    assert resumo.restantes is False
    assert resumo.custo_total_usd == Decimal("0.000200")

    [linha] = _classificacoes(sessao)
    assert linha["evento_id"] == evento.id
    assert linha["situacao"] == "ok"
    assert linha["rodada"] == rodada
    assert linha["criada_em"] == AGORA
    assert linha["hash_conteudo"] == evento.hash_conteudo
    assert linha["versao_prompt"] == prompt.VERSAO_PROMPT
    assert linha["modelo"] == modelos.MODELO
    assert linha["versao_schema"] == modelos.VERSAO_SCHEMA
    assert linha["resultado"] == saida_valida()
    assert linha["motivo_erro"] is None
    assert linha["tokens_entrada"] == 1_000
    assert linha["tokens_saida"] == 200
    assert linha["latencia_ms"] == 250
    assert linha["custo_usd"] == Decimal("0.000200")


def test_chamada_usa_prompt_schema_e_mensagem(
    classificador: ModuleType,
    modelos: ModuleType,
    prompt: ModuleType,
    sessao: Session,
    cliente: ClienteFalso,
) -> None:
    evento = _inserir(sessao, localizacao="online")
    _rodar(classificador, sessao, cliente)
    [chamada] = cliente.chamadas
    assert chamada.system == prompt.carregar_prompt()
    assert chamada.schema == modelos.SCHEMA_SAIDA
    assert chamada.mensagem == prompt.montar_mensagem(evento, "online")[0]
    assert chamada.max_tokens == 1024


def test_segunda_execucao_nao_chama_de_novo(
    classificador: ModuleType, sessao: Session, cliente: ClienteFalso
) -> None:
    _inserir(sessao)
    _rodar(classificador, sessao, cliente)
    resumo = _rodar(classificador, sessao, cliente)
    assert len(cliente.chamadas) == 1
    assert resumo.chamadas == 0
    assert resumo.ok == []
    assert resumo.custo_total_usd == Decimal("0")
    assert len(_classificacoes(sessao)) == 1


def test_evento_alterado_e_reclassificado(
    classificador: ModuleType, sessao: Session, cliente: ClienteFalso
) -> None:
    evento = _inserir(sessao)
    _rodar(classificador, sessao, cliente)
    evento.hash_conteudo = "b" * 64
    evento.estado = "alterado"
    sessao.flush()
    resumo = _rodar(classificador, sessao, cliente)
    assert len(cliente.chamadas) == 2
    assert resumo.ok == [evento.id]
    hashes = [linha["hash_conteudo"] for linha in _classificacoes(sessao, evento.id)]
    assert hashes == ["a" * 64, "b" * 64]


@pytest.mark.parametrize(
    ("modulo", "atributo", "valor", "coluna"),
    [
        ("radar.classificacao.prompt", "VERSAO_PROMPT", "classificador_v2", "versao_prompt"),
        ("radar.classificacao.modelos", "MODELO", "modelo-novo", "modelo"),
        ("radar.classificacao.modelos", "VERSAO_SCHEMA", "2", "versao_schema"),
    ],
)
def test_mudar_a_chave_reclassifica(
    classificador: ModuleType,
    sessao: Session,
    cliente: ClienteFalso,
    monkeypatch: pytest.MonkeyPatch,
    modulo: str,
    atributo: str,
    valor: str,
    coluna: str,
) -> None:
    evento = _inserir(sessao)
    _rodar(classificador, sessao, cliente)
    precos = importlib.import_module("radar.classificacao.precos")
    tabela = dict(precos.TABELA_PRECOS)
    tabela["modelo-novo"] = tabela["claude-haiku-5-5"]
    monkeypatch.setattr(precos, "TABELA_PRECOS", tabela)
    monkeypatch.setattr(importlib.import_module(modulo), atributo, valor)

    resumo = _rodar(classificador, sessao, cliente)
    assert len(cliente.chamadas) == 2
    assert resumo.ok == [evento.id]
    assert [linha[coluna] for linha in _classificacoes(sessao, evento.id)][-1] == valor


@pytest.mark.parametrize("estado", ["cancelado", "encerrado", "nao_encontrado"])
def test_estados_fora_da_fila_nao_sao_chamados(
    classificador: ModuleType, sessao: Session, cliente: ClienteFalso, estado: str
) -> None:
    _inserir(sessao, estado=estado)
    resumo = _rodar(classificador, sessao, cliente)
    assert cliente.chamadas == []
    assert resumo.chamadas == 0


def test_evento_fora_de_sp_nao_e_chamado(
    classificador: ModuleType, sessao: Session, cliente: ClienteFalso
) -> None:
    _inserir(sessao, localizacao="fora", evidencia_localizacao="texto")
    _rodar(classificador, sessao, cliente)
    assert cliente.chamadas == []


def test_agora_sem_fuso_e_erro(
    classificador: ModuleType, sessao: Session, cliente: ClienteFalso
) -> None:
    _inserir(sessao)
    with pytest.raises(ValueError):
        _rodar(classificador, sessao, cliente, agora=datetime(2026, 11, 25, 12, 0))
    assert cliente.chamadas == []


def test_custo_total_soma_as_chamadas(
    classificador: ModuleType, cliente_mod: ModuleType, sessao: Session, cliente: ClienteFalso
) -> None:
    _inserir(sessao, inicio=INICIO_PADRAO)
    _inserir(sessao, inicio=INICIO_PADRAO + timedelta(days=1))
    _inserir(sessao, inicio=INICIO_PADRAO + timedelta(days=2))
    cliente.fila = [
        _resposta(cliente_mod, tokens_entrada=1_000, tokens_saida=200),  # 0,000200
        _resposta(cliente_mod, texto="não é json", tokens_entrada=2_000, tokens_saida=0),  # 0,0002
        _erro_api(cliente_mod, "servidor", 500),  # sem tokens: sem custo
    ]
    resumo = _rodar(classificador, sessao, cliente)
    assert resumo.chamadas == 3
    assert resumo.custo_total_usd == Decimal("0.000400")
    custos = [linha["custo_usd"] for linha in _classificacoes(sessao)]
    assert custos == [Decimal("0.000200"), Decimal("0.000200"), None]


# --- Saída inválida do LLM vira erro registrado --------------------------------------------


def _casos_de_saida_invalida() -> list[Any]:
    tipo_secreto = saida_valida(tipo_evento=TITULO_SECRETO)
    chave_secreta = {**saida_valida(), TITULO_SECRETO: "x"}
    trecho_inventado = saida_valida(trecho_preco="Ingresso R$ 50 Segredo Zeta 7731")
    return [
        pytest.param({"texto": "isto não é json {"}, "saida_json_invalido", id="json"),
        pytest.param({"texto": "[1, 2]"}, "saida_schema_invalido", id="json-nao-objeto"),
        pytest.param(
            {"texto": json.dumps(tipo_secreto)}, "saida_schema_invalido:tipo_evento", id="enum"
        ),
        pytest.param({"texto": json.dumps(chave_secreta)}, "saida_schema_invalido", id="extra"),
        pytest.param(
            {"texto": json.dumps(trecho_inventado)},
            "saida_evidencia_nao_encontrada:trecho_preco",
            id="trecho-inexistente",
        ),
        pytest.param({"stop_reason": "max_tokens"}, "saida_truncada", id="truncada"),
        pytest.param({"stop_reason": "refusal"}, "saida_parada_inesperada", id="refusal"),
        pytest.param(
            {"stop_reason": "stop_sequence"}, "saida_parada_inesperada", id="stop-sequence"
        ),
        pytest.param({"stop_reason": None}, "saida_parada_inesperada", id="sem-stop-reason"),
    ]


@pytest.mark.parametrize(("resposta", "motivo"), _casos_de_saida_invalida())
def test_saida_invalida_vira_erro_registrado(
    classificador: ModuleType,
    cliente_mod: ModuleType,
    sessao: Session,
    cliente: ClienteFalso,
    resposta: dict[str, Any],
    motivo: str,
) -> None:
    evento = _inserir(sessao, titulo=TITULO_SECRETO)
    cliente.fila = [_resposta(cliente_mod, **resposta)]
    resumo = _rodar(classificador, sessao, cliente)

    assert len(cliente.chamadas) == 1
    assert resumo.ok == []
    assert resumo.interrompido_por is None
    [(evento_id, motivo_resumo)] = resumo.erros
    assert evento_id == evento.id

    [linha] = _classificacoes(sessao, evento.id)
    assert linha["situacao"] == "erro"
    assert linha["resultado"] is None
    assert linha["motivo_erro"] == motivo_resumo
    if motivo == "saida_schema_invalido":
        # campos livres, mas só nomes de campos do schema (nunca texto vindo do LLM/evento)
        assert linha["motivo_erro"].startswith(motivo)
    else:
        assert linha["motivo_erro"] == motivo
    for pedaco in PEDACOS_SECRETOS:
        assert pedaco not in linha["motivo_erro"]
    assert linha["tokens_entrada"] == 1_000
    assert linha["tokens_saida"] == 200
    assert linha["latencia_ms"] == 250
    assert linha["custo_usd"] == Decimal("0.000200")
    assert linha["hash_conteudo"] == evento.hash_conteudo


def test_json_invalido_tem_motivo_exato(
    classificador: ModuleType, cliente_mod: ModuleType, sessao: Session, cliente: ClienteFalso
) -> None:
    evento = _inserir(sessao)
    cliente.fila = [_resposta(cliente_mod, texto="{ quebrado")]
    resumo = _rodar(classificador, sessao, cliente)
    assert resumo.erros == [(evento.id, "saida_json_invalido")]


def test_truncada_mesmo_com_json_valido(
    classificador: ModuleType, cliente_mod: ModuleType, sessao: Session, cliente: ClienteFalso
) -> None:
    evento = _inserir(sessao)
    cliente.fila = [_resposta(cliente_mod, stop_reason="max_tokens")]
    resumo = _rodar(classificador, sessao, cliente)
    assert resumo.erros == [(evento.id, "saida_truncada")]
    assert resumo.ok == []


def test_indefinida_com_nao_se_aplica_e_erro(
    classificador: ModuleType, cliente_mod: ModuleType, sessao: Session, cliente: ClienteFalso
) -> None:
    evento = _inserir(sessao, localizacao="indefinida", evidencia_localizacao="nenhuma")
    dados = saida_valida(localizacao="nao_se_aplica", trecho_localizacao="")
    cliente.fila = [_resposta(cliente_mod, dados)]
    resumo = _rodar(classificador, sessao, cliente)
    assert resumo.erros == [(evento.id, "saida_localizacao_ausente")]
    assert [linha["situacao"] for linha in _classificacoes(sessao, evento.id)] == ["erro"]


def test_sp_com_nao_se_aplica_e_ok(
    classificador: ModuleType, cliente_mod: ModuleType, sessao: Session, cliente: ClienteFalso
) -> None:
    evento = _inserir(sessao, localizacao="sp")
    dados = saida_valida(localizacao="nao_se_aplica", trecho_localizacao="")
    cliente.fila = [_resposta(cliente_mod, dados)]
    resumo = _rodar(classificador, sessao, cliente)
    assert resumo.ok == [evento.id]


@pytest.mark.parametrize(
    ("filtro", "resposta"),
    [("sp", "fora"), ("online", "sp")],
)
def test_localizacao_indevida_e_erro(
    classificador: ModuleType,
    cliente_mod: ModuleType,
    sessao: Session,
    cliente: ClienteFalso,
    filtro: str,
    resposta: str,
) -> None:
    evento = _inserir(sessao, localizacao=filtro)
    dados = saida_valida(localizacao=resposta, trecho_localizacao=LOCAL_TEXTO)
    cliente.fila = [_resposta(cliente_mod, dados)]
    resumo = _rodar(classificador, sessao, cliente)
    assert resumo.ok == []
    assert resumo.erros == [(evento.id, "saida_localizacao_indevida")]
    [linha] = _classificacoes(sessao, evento.id)
    assert linha["situacao"] == "erro"
    assert linha["motivo_erro"] == "saida_localizacao_indevida"


def test_indefinida_com_localizacao_e_evidencia_e_ok(
    classificador: ModuleType, cliente_mod: ModuleType, sessao: Session, cliente: ClienteFalso
) -> None:
    evento = _inserir(sessao, localizacao="indefinida", evidencia_localizacao="nenhuma")
    dados = saida_valida(localizacao="sp", trecho_localizacao=LOCAL_TEXTO)
    cliente.fila = [_resposta(cliente_mod, dados)]
    resumo = _rodar(classificador, sessao, cliente)
    assert resumo.ok == [evento.id]


def test_trecho_com_url_completa_nao_e_evidencia(
    classificador: ModuleType, cliente_mod: ModuleType, sessao: Session, cliente: ClienteFalso
) -> None:
    url = "https://luma.com/abc123xyz?utm_source=teste"
    evento = _inserir(sessao, descricoes={"cal-a": f"{DESCRICAO} Inscrições em {url}"})
    dados = saida_valida(trecho_evidencia=f"Inscrições em {url}")
    cliente.fila = [_resposta(cliente_mod, dados)]
    resumo = _rodar(classificador, sessao, cliente)
    assert resumo.erros == [(evento.id, "saida_evidencia_nao_encontrada:trecho_evidencia")]
    assert resumo.ok == []


def test_tres_erros_de_saida_tiram_da_fila(
    classificador: ModuleType, cliente_mod: ModuleType, sessao: Session, cliente: ClienteFalso
) -> None:
    _inserir(sessao)
    cliente.padrao = _resposta(cliente_mod, texto="não é json")
    for _ in range(3):
        _rodar(classificador, sessao, cliente)
    assert len(cliente.chamadas) == 3
    resumo = _rodar(classificador, sessao, cliente)
    assert len(cliente.chamadas) == 3
    assert resumo.chamadas == 0


def test_dois_erros_de_saida_ainda_tentam(
    classificador: ModuleType, cliente_mod: ModuleType, sessao: Session, cliente: ClienteFalso
) -> None:
    evento = _inserir(sessao)
    cliente.fila = [
        _resposta(cliente_mod, texto="não é json"),
        _resposta(cliente_mod, stop_reason="max_tokens"),
    ]
    _rodar(classificador, sessao, cliente)
    _rodar(classificador, sessao, cliente)
    resumo = _rodar(classificador, sessao, cliente)
    assert len(cliente.chamadas) == 3
    assert resumo.ok == [evento.id]


def test_tres_erros_de_api_nao_tiram_da_fila(
    classificador: ModuleType, cliente_mod: ModuleType, sessao: Session, cliente: ClienteFalso
) -> None:
    evento = _inserir(sessao)
    cliente.fila = [_erro_api(cliente_mod, "servidor", 500) for _ in range(3)]
    for _ in range(3):
        _rodar(classificador, sessao, cliente)
    resumo = _rodar(classificador, sessao, cliente)
    assert len(cliente.chamadas) == 4
    assert resumo.ok == [evento.id]


# --- Limite e erros de API -----------------------------------------------------------------


def test_limite_de_chamadas_por_execucao(
    classificador: ModuleType, prompt: ModuleType, sessao: Session, cliente: ClienteFalso
) -> None:
    tarde = _inserir(sessao, titulo="Tarde", inicio=INICIO_PADRAO + timedelta(days=3), fim=None)
    cedo = _inserir(sessao, titulo="Cedo", inicio=INICIO_PADRAO, fim=None)
    meio = _inserir(sessao, titulo="Meio", inicio=INICIO_PADRAO + timedelta(days=1), fim=None)
    resumo = _rodar(classificador, sessao, cliente, limite=2)
    assert resumo.chamadas == 2
    assert resumo.ok == [cedo.id, meio.id]
    assert resumo.restantes is True
    assert [c.mensagem for c in cliente.chamadas] == [
        prompt.montar_mensagem(cedo, "sp")[0],
        prompt.montar_mensagem(meio, "sp")[0],
    ]
    seguinte = _rodar(classificador, sessao, cliente, limite=2)
    assert seguinte.ok == [tarde.id]
    assert seguinte.restantes is False


def test_limite_exato_nao_deixa_restantes(
    classificador: ModuleType, sessao: Session, cliente: ClienteFalso
) -> None:
    _inserir(sessao, inicio=INICIO_PADRAO)
    _inserir(sessao, inicio=INICIO_PADRAO + timedelta(days=1))
    resumo = _rodar(classificador, sessao, cliente, limite=2)
    assert resumo.chamadas == 2
    assert resumo.restantes is False


@pytest.mark.parametrize(("categoria", "status"), [("rate_limit", 429), ("autenticacao", 401)])
def test_rate_limit_e_autenticacao_interrompem_na_hora(
    classificador: ModuleType,
    cliente_mod: ModuleType,
    sessao: Session,
    cliente: ClienteFalso,
    categoria: str,
    status: int,
) -> None:
    primeiro = _inserir(sessao, inicio=INICIO_PADRAO)
    _inserir(sessao, inicio=INICIO_PADRAO + timedelta(days=1))
    cliente.fila = [_erro_api(cliente_mod, categoria, status)]
    resumo = _rodar(classificador, sessao, cliente)
    assert len(cliente.chamadas) == 1
    assert resumo.chamadas == 1
    assert resumo.interrompido_por == categoria
    assert resumo.erros == [(primeiro.id, f"api_{categoria}")]
    [linha] = _classificacoes(sessao)
    assert linha["situacao"] == "erro"
    assert linha["motivo_erro"] == f"api_{categoria}"
    assert linha["tokens_entrada"] is None
    assert linha["custo_usd"] is None


@pytest.mark.parametrize(
    "categorias",
    [
        ("servidor", "servidor", "servidor"),
        ("servidor", "timeout", "conexao"),
        ("requisicao", "requisicao", "requisicao"),
        ("outro", "servidor", "requisicao"),
    ],
)
def test_tres_falhas_de_api_seguidas_interrompem(
    classificador: ModuleType,
    cliente_mod: ModuleType,
    sessao: Session,
    cliente: ClienteFalso,
    categorias: tuple[str, ...],
) -> None:
    for dia in range(5):
        _inserir(sessao, inicio=INICIO_PADRAO + timedelta(days=dia))
    status = {"servidor": 500, "requisicao": 400}
    cliente.fila = [_erro_api(cliente_mod, c, status.get(c)) for c in categorias]
    resumo = _rodar(classificador, sessao, cliente)
    assert len(cliente.chamadas) == 3
    assert resumo.interrompido_por == "falhas_api_seguidas"
    assert [motivo for _, motivo in resumo.erros] == [f"api_{c}" for c in categorias]


@pytest.mark.parametrize("categoria", ["requisicao", "outro"])
def test_requisicao_e_outro_nao_interrompem_na_primeira(
    classificador: ModuleType,
    cliente_mod: ModuleType,
    sessao: Session,
    cliente: ClienteFalso,
    categoria: str,
) -> None:
    for dia in range(3):
        _inserir(sessao, inicio=INICIO_PADRAO + timedelta(days=dia))
    cliente.fila = [_erro_api(cliente_mod, categoria, 400 if categoria == "requisicao" else None)]
    resumo = _rodar(classificador, sessao, cliente)
    assert len(cliente.chamadas) == 3
    assert resumo.interrompido_por is None
    assert [motivo for _, motivo in resumo.erros] == [f"api_{categoria}"]
    assert len(resumo.ok) == 2


def test_falhas_de_api_intercaladas_com_ok_nao_interrompem(
    classificador: ModuleType, cliente_mod: ModuleType, sessao: Session, cliente: ClienteFalso
) -> None:
    for dia in range(5):
        _inserir(sessao, inicio=INICIO_PADRAO + timedelta(days=dia))
    falha = _erro_api(cliente_mod, "servidor", 500)
    ok = _resposta(cliente_mod)
    cliente.fila = [falha, ok, falha, falha, ok]
    resumo = _rodar(classificador, sessao, cliente)
    assert len(cliente.chamadas) == 5
    assert resumo.interrompido_por is None
    assert len(resumo.ok) == 2
    assert len(resumo.erros) == 3


def test_uma_chamada_por_evento_mesmo_com_erro(
    classificador: ModuleType, cliente_mod: ModuleType, sessao: Session, cliente: ClienteFalso
) -> None:
    _inserir(sessao, inicio=INICIO_PADRAO)
    _inserir(sessao, inicio=INICIO_PADRAO + timedelta(days=1))
    cliente.fila = [_erro_api(cliente_mod, "timeout"), _resposta(cliente_mod, texto="x")]
    resumo = _rodar(classificador, sessao, cliente)
    assert len(cliente.chamadas) == 2
    assert len({c.mensagem for c in cliente.chamadas}) == 2
    assert len(resumo.erros) == 2


# --- Limites de tokens por execução ------------------------------------------------------


@pytest.mark.parametrize(
    ("config", "tokens"),
    [
        ({"limite_tokens_entrada_por_execucao": 1_000}, {"tokens_entrada": 600}),
        ({"limite_tokens_saida_por_execucao": 300}, {"tokens_saida": 200}),
    ],
    ids=["entrada", "saida"],
)
def test_limite_de_tokens_interrompe(
    classificador: ModuleType,
    cliente_mod: ModuleType,
    sessao: Session,
    cliente: ClienteFalso,
    config: dict[str, int],
    tokens: dict[str, int],
) -> None:
    for dia in range(3):
        _inserir(sessao, inicio=INICIO_PADRAO + timedelta(days=dia))
    cliente.padrao = _resposta(cliente_mod, **tokens)
    resumo = _rodar(classificador, sessao, cliente, **config)
    assert len(cliente.chamadas) == 2
    assert resumo.chamadas == 2
    assert resumo.interrompido_por == "limite_tokens"
    assert resumo.restantes is True
    assert len(_classificacoes(sessao)) == 2


def test_tokens_de_erros_de_saida_contam_no_limite(
    classificador: ModuleType, cliente_mod: ModuleType, sessao: Session, cliente: ClienteFalso
) -> None:
    for dia in range(3):
        _inserir(sessao, inicio=INICIO_PADRAO + timedelta(days=dia))
    cliente.padrao = _resposta(cliente_mod, texto="não é json", tokens_entrada=600)
    resumo = _rodar(classificador, sessao, cliente, limite_tokens_entrada_por_execucao=1_000)
    assert len(cliente.chamadas) == 2
    assert resumo.interrompido_por == "limite_tokens"
    assert resumo.restantes is True


def test_limite_de_tokens_nao_atingido_nao_interrompe(
    classificador: ModuleType, cliente_mod: ModuleType, sessao: Session, cliente: ClienteFalso
) -> None:
    for dia in range(3):
        _inserir(sessao, inicio=INICIO_PADRAO + timedelta(days=dia))
    cliente.padrao = _resposta(cliente_mod, tokens_entrada=300)
    resumo = _rodar(classificador, sessao, cliente, limite_tokens_entrada_por_execucao=1_000)
    assert len(cliente.chamadas) == 3
    assert resumo.interrompido_por is None
    assert resumo.restantes is False


# --- F1-07b: resultado gravado sem alteração; texto bruto fora do banco ---------------------


def test_resultado_ok_gravado_valida_no_modelo_sem_chaves_extras(
    classificador: ModuleType,
    cliente_mod: ModuleType,
    modelos: ModuleType,
    sessao: Session,
    cliente: ClienteFalso,
) -> None:
    # a regra de comunidade de IA é aplicada na leitura (F1-10), nunca na gravação
    evento = _inserir(sessao, calendarios=["ai-collective"])
    cliente.fila = [_resposta(cliente_mod, saida_valida(e_ia=False))]
    _rodar(classificador, sessao, cliente)
    [linha] = _classificacoes(sessao, evento.id)
    assert linha["situacao"] == "ok"
    saida = modelos.SaidaClassificador.model_validate(linha["resultado"])
    assert set(linha["resultado"]) == set(modelos.SaidaClassificador.model_fields)
    assert "e_ia_por_comunidade" not in linha["resultado"]
    assert saida.e_ia is False
    assert linha["resultado"] == saida_valida(e_ia=False)


def test_classificar_pendentes_sem_parametro_de_comunidades(classificador: ModuleType) -> None:
    import inspect

    assert "comunidades_ia" not in inspect.signature(classificador.classificar_pendentes).parameters


@pytest.mark.parametrize("stop_reason", ["end_turn", "max_tokens"])
def test_texto_bruto_do_llm_nunca_vai_ao_banco(
    classificador: ModuleType,
    cliente_mod: ModuleType,
    sessao: Session,
    cliente: ClienteFalso,
    stop_reason: str,
) -> None:
    evento = _inserir(sessao)
    cliente.fila = [
        _resposta(cliente_mod, texto="SEGREDO-BRUTO-42 {sem json", stop_reason=stop_reason)
    ]
    _rodar(classificador, sessao, cliente)
    [linha] = _classificacoes(sessao, evento.id)
    assert linha["situacao"] == "erro"
    for coluna, valor in linha.items():
        assert "SEGREDO-BRUTO" not in json.dumps(valor, default=str), coluna


# --- validar_contra_evento (sem banco) -----------------------------------------------------

TEXTO_FONTE = f"Meetup de IA\n{LOCAL_TEXTO}\n{DESCRICAO}"


def _saida(modelos: ModuleType, **campos: Any) -> Any:
    return modelos.SaidaClassificador.model_validate(saida_valida(**campos))


def test_validar_saida_coerente(classificador: ModuleType, modelos: ModuleType) -> None:
    assert classificador.validar_contra_evento(_saida(modelos), TEXTO_FONTE, "sp") is None


@pytest.mark.parametrize(
    "trecho",
    [
        "MODELOS   DE\nLinguagem",
        "...modelos de linguagem",
        "modelos de linguagem…",
        "… modelos de linguagem ...",
    ],
)
def test_validar_normaliza_caixa_espacos_e_reticencias(
    classificador: ModuleType, modelos: ModuleType, trecho: str
) -> None:
    saida = _saida(modelos, trecho_evidencia=trecho)
    assert classificador.validar_contra_evento(saida, TEXTO_FONTE, "sp") is None


def test_validar_normaliza_nfc(classificador: ModuleType, modelos: ModuleType) -> None:
    decomposto = unicodedata.normalize("NFD", TEXTO_FONTE)
    assert decomposto != TEXTO_FONTE
    saida = _saida(modelos)  # trechos em NFC
    assert classificador.validar_contra_evento(saida, decomposto, "sp") is None


@pytest.mark.parametrize(
    ("campo", "valor", "extra", "filtro"),
    [
        ("trecho_evidencia", "visão computacional", {}, "sp"),
        ("trecho_preco", "R$ 50,00", {}, "sp"),
        ("trecho_modalidade", "online via Zoom", {}, "sp"),
        ("trecho_idioma", "palestras em inglês", {}, "sp"),
        ("trecho_localizacao", "Rua Augusta", {"localizacao": "sp"}, "indefinida"),
    ],
)
def test_validar_trecho_inexistente(
    classificador: ModuleType,
    modelos: ModuleType,
    campo: str,
    valor: str,
    extra: dict[str, str],
    filtro: str,
) -> None:
    saida = _saida(modelos, **{campo: valor, **extra})
    assert (
        classificador.validar_contra_evento(saida, TEXTO_FONTE, filtro)
        == f"saida_evidencia_nao_encontrada:{campo}"
    )


@pytest.mark.parametrize(
    ("filtro", "resposta"),
    [
        ("sp", "fora"),
        ("sp", "sp"),
        ("sp", "desconhecida"),
        ("online", "sp"),
        ("online", "online"),
    ],
)
def test_validar_localizacao_indevida(
    classificador: ModuleType, modelos: ModuleType, filtro: str, resposta: str
) -> None:
    trecho = "" if resposta == "desconhecida" else LOCAL_TEXTO
    saida = _saida(modelos, localizacao=resposta, trecho_localizacao=trecho)
    assert (
        classificador.validar_contra_evento(saida, TEXTO_FONTE, filtro)
        == "saida_localizacao_indevida"
    )


def test_validar_indefinida_aceita_localizacao_com_evidencia(
    classificador: ModuleType, modelos: ModuleType
) -> None:
    saida = _saida(modelos, localizacao="sp", trecho_localizacao=LOCAL_TEXTO)
    assert classificador.validar_contra_evento(saida, TEXTO_FONTE, "indefinida") is None


def test_validar_nao_confere_trechos_vazios(classificador: ModuleType, modelos: ModuleType) -> None:
    saida = _saida(
        modelos,
        modalidade="desconhecida",
        trecho_modalidade="",
        idioma="desconhecido",
        trecho_idioma="",
        estado_preco="preco_desconhecido",
        trecho_preco="",
        localizacao="desconhecida",
        trecho_localizacao="",
    )
    assert classificador.validar_contra_evento(saida, TEXTO_FONTE, "indefinida") is None


def test_validar_reticencias_no_meio_nao_sao_removidas(
    classificador: ModuleType, modelos: ModuleType
) -> None:
    saida = _saida(modelos, trecho_evidencia="modelos ... linguagem")
    assert (
        classificador.validar_contra_evento(saida, TEXTO_FONTE, "sp")
        == "saida_evidencia_nao_encontrada:trecho_evidencia"
    )


@pytest.mark.parametrize(
    ("filtro", "esperado"),
    [
        ("indefinida", "saida_localizacao_ausente"),
        ("sp", None),
        ("online", None),
    ],
)
def test_validar_nao_se_aplica(
    classificador: ModuleType, modelos: ModuleType, filtro: str, esperado: str | None
) -> None:
    saida = _saida(modelos, localizacao="nao_se_aplica", trecho_localizacao="")
    assert classificador.validar_contra_evento(saida, TEXTO_FONTE, filtro) == esperado
