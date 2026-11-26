"""Repositório: registro de uma rodada (upsert + ciclo de vida) e fila de classificação (D12).

As funções recebem uma `Session` e só fazem `flush`; o commit é de quem chama.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, exists, false, func, or_, select
from sqlalchemy.orm import Session

from radar.banco.ciclo_vida import EstadoEvento, Observacao, transicionar
from radar.banco.conteudo import EventoConsolidado, consolidar_observacoes, hash_conteudo
from radar.banco.modelos import Classificacao, Enriquecimento, Evento, Execucao
from radar.filtros.geografico import EventoLocalizado, classificar_localizacao
from radar.filtros.horizonte import calcular_termino
from radar.fontes.modelos import EventoNormalizado, ResultadoCalendario

_SUCESSO = frozenset({"ok", "vazio"})
_CAMPOS_CONTEUDO = (
    "titulo",
    "inicio",
    "fim",
    "dia_inteiro",
    "local_texto",
    "local_url",
    "endereco_oculto",
    "latitude",
    "longitude",
    "link_inscricao",
    "organizador",
    "status_fonte",
)
_E = EstadoEvento


class ResumoRodada(BaseModel):
    """Ids de `eventos` que entraram em cada estado nesta rodada."""

    model_config = ConfigDict(extra="forbid")

    ja_registrada: bool
    novos: list[int] = []
    alterados: list[int] = []
    cancelados: list[int] = []
    encerrados: list[int] = []
    nao_encontrados: list[int] = []
    reativados: list[int] = []


def _validar_entrada(
    resultados: Sequence[ResultadoCalendario],
    aprovados: Sequence[EventoLocalizado],
    agora: datetime,
) -> None:
    if agora.tzinfo is None or agora.utcoffset() is None:
        raise ValueError("`agora` precisa ter fuso horário")
    chaves = [(r.fonte, r.calendario) for r in resultados]
    if len(set(chaves)) != len(chaves):
        raise ValueError("calendário repetido nos resultados")
    ids_ok = {
        (r.fonte, r.calendario): {e.id_fonte for e in r.eventos}
        for r in resultados
        if r.situacao == "ok"
    }
    for item in aprovados:
        evento = item.evento
        if evento.id_fonte not in ids_ok.get((evento.fonte, evento.calendario), set()):
            raise ValueError("evento aprovado que não está num calendário lido com sucesso")
        if item.localizacao.situacao == "fora":
            raise ValueError("evento fora de SP não pode ser aprovado")


def _aplicar_conteudo(linha: Evento, consolidado: EventoConsolidado) -> None:
    for campo in _CAMPOS_CONTEUDO:
        setattr(linha, campo, getattr(consolidado, campo))


def _escalares_da_rodada(
    linha: Evento,
    consolidado: EventoConsolidado,
    falhas: set[tuple[str, str]],
    limite: int,
) -> EventoConsolidado:
    """Escolhe os escalares a gravar e atualiza `linha.rodadas_escalares_mantidos`.

    Se um calendário já registrado do evento, com slug menor que o menor da rodada, está nos
    resultados desta rodada com situação "falha", os escalares gravados são mantidos (não
    oscilam) e o contador sobe. Ausente da lista ou lido com sucesso sem o evento não congela.
    Depois de `limite` rodadas seguidas mantidas, a rodada aceita os escalares lidos.
    """
    menor_da_rodada = consolidado.calendarios[0]
    congelado = any(
        slug < menor_da_rodada and (linha.fonte, slug) in falhas for slug in linha.calendarios
    )
    if not congelado or linha.rodadas_escalares_mantidos >= limite:
        linha.rodadas_escalares_mantidos = 0
        return consolidado
    linha.rodadas_escalares_mantidos += 1
    mantidos = {c: getattr(linha, c) for c in _CAMPOS_CONTEUDO if c != "status_fonte"}
    return consolidado.model_copy(update=mantidos)


def _localizacao_gravada(linha: Evento) -> tuple[str, str]:
    """Reaplica o filtro geográfico (D6, D11) aos campos de local gravados no evento."""
    observado = EventoNormalizado(
        fonte="luma",
        calendario=linha.calendarios[0],
        id_fonte=linha.id_fonte,
        titulo=linha.titulo,
        inicio=linha.inicio,
        fim=linha.fim,
        dia_inteiro=linha.dia_inteiro,
        local_texto=linha.local_texto,
        local_url=linha.local_url,
        endereco_oculto=linha.endereco_oculto,
        latitude=linha.latitude,
        longitude=linha.longitude,
        status_fonte="desconhecido",
        link_inscricao=linha.link_inscricao,
        descricao=None,
        organizador=linha.organizador,
    )
    localizacao = classificar_localizacao(observado)
    return localizacao.situacao, localizacao.evidencia


def registrar_rodada(
    sessao: Session,
    rodada: uuid.UUID,
    resultados: Sequence[ResultadoCalendario],
    aprovados: Sequence[EventoLocalizado],
    agora: datetime,
    limite_ausencias: int,
) -> ResumoRodada:
    """Grava as execuções da rodada, insere eventos novos aprovados e aplica o ciclo de vida
    a todos os eventos não encerrados. Repetir a mesma `rodada` não muda nada."""
    _validar_entrada(resultados, aprovados, agora)
    if limite_ausencias < 1:
        raise ValueError("limite de ausências precisa ser >= 1")

    ja_existe = sessao.scalar(select(exists().where(Execucao.rodada == rodada)))
    if ja_existe:
        return ResumoRodada(ja_registrada=True)

    for resultado in resultados:
        falha = resultado.falha
        sessao.add(
            Execucao(
                rodada=rodada,
                fonte=resultado.fonte,
                calendario=resultado.calendario,
                executada_em=agora,
                situacao=resultado.situacao,
                quantidade_eventos=len(resultado.eventos),
                quantidade_invalidos=len(resultado.eventos_invalidos),
                motivo_falha=falha.motivo if falha else None,
                status_http=falha.status_http if falha else None,
                detalhe=falha.detalhe if falha else None,
            )
        )

    observacoes: dict[tuple[str, str], list[EventoNormalizado]] = defaultdict(list)
    vistos_invalidos: set[tuple[str, str]] = set()
    sucesso: set[tuple[str, str]] = set()
    falhas = {(r.fonte, r.calendario) for r in resultados if r.situacao == "falha"}
    for resultado in resultados:
        if resultado.situacao in _SUCESSO:
            sucesso.add((resultado.fonte, resultado.calendario))
        if resultado.situacao != "ok":
            continue
        for item in resultado.eventos:
            observacoes[(item.fonte, item.id_fonte)].append(item)
        for invalido in resultado.eventos_invalidos:
            if invalido.id_fonte is not None:
                vistos_invalidos.add((resultado.fonte, invalido.id_fonte))

    localizacoes: dict[tuple[str, str], EventoLocalizado] = {}
    for item in sorted(aprovados, key=lambda a: a.evento.calendario):
        localizacoes.setdefault((item.evento.fonte, item.evento.id_fonte), item)

    vistos = set(observacoes) | vistos_invalidos
    ids_vistos = {id_fonte for _, id_fonte in vistos}
    existentes = {
        (linha.fonte, linha.id_fonte): linha
        for linha in sessao.scalars(
            select(Evento).where(
                (Evento.estado != _E.ENCERRADO.value) | Evento.id_fonte.in_(ids_vistos)
            )
        )
    }

    resumo = ResumoRodada(ja_registrada=False)
    for chave, linha in existentes.items():
        _atualizar_existente(
            linha,
            chave,
            observacoes,
            vistos,
            sucesso,
            falhas,
            agora,
            limite_ausencias,
            resumo,
        )

    novos: list[Evento] = []
    for chave, item in localizacoes.items():
        if chave in existentes:
            continue
        novos.append(_inserir_novo(sessao, observacoes[chave], item, agora))
    sessao.flush()
    resumo.novos.extend(linha.id for linha in novos)
    for lista in (
        resumo.novos,
        resumo.alterados,
        resumo.cancelados,
        resumo.encerrados,
        resumo.nao_encontrados,
        resumo.reativados,
    ):
        lista.sort()
    return resumo


def _inserir_novo(
    sessao: Session,
    observacoes: list[EventoNormalizado],
    item: EventoLocalizado,
    agora: datetime,
) -> Evento:
    consolidado = consolidar_observacoes(observacoes)
    terminou = (
        calcular_termino(consolidado.inicio, consolidado.fim, consolidado.dia_inteiro) <= agora
    )
    decisao = transicionar(
        None,
        0,
        Observacao(
            visto=True,
            hash_mudou=False,
            cancelado_na_fonte=consolidado.status_fonte == "cancelado",
            terminou=terminou,
            ausencia_contavel=False,
        ),
        1,
    )
    linha = Evento(
        fonte=consolidado.fonte,
        id_fonte=consolidado.id_fonte,
        calendarios=list(consolidado.calendarios),
        descricoes=dict(consolidado.descricoes),
        hash_conteudo=hash_conteudo(consolidado, consolidado.descricoes),
        localizacao=item.localizacao.situacao,
        evidencia_localizacao=item.localizacao.evidencia,
        estado=decisao.estado.value,
        ausencias_consecutivas=decisao.ausencias,
        rodadas_escalares_mantidos=0,
        primeira_vez_visto=agora,
        ultima_vez_visto=agora,
        atualizado_em=agora,
    )
    _aplicar_conteudo(linha, consolidado)
    sessao.add(linha)
    return linha


def _atualizar_existente(
    linha: Evento,
    chave: tuple[str, str],
    observacoes: dict[tuple[str, str], list[EventoNormalizado]],
    vistos: set[tuple[str, str]],
    sucesso: set[tuple[str, str]],
    falhas: set[tuple[str, str]],
    agora: datetime,
    limite: int,
    resumo: ResumoRodada,
) -> None:
    atual = EstadoEvento(linha.estado)
    visto = chave in vistos
    obs_rodada = observacoes.get(chave)
    consolidado = consolidar_observacoes(obs_rodada) if obs_rodada else None
    # Encerrado é terminal: o conteúdo não é mais atualizado.
    atualiza_conteudo = consolidado is not None and atual is not _E.ENCERRADO
    if consolidado is not None and atualiza_conteudo:
        # `terminou` (abaixo) usa os escalares efetivamente gravados nesta rodada.
        consolidado = _escalares_da_rodada(linha, consolidado, falhas, limite)

    hash_novo = linha.hash_conteudo
    descricoes = dict(linha.descricoes)
    if consolidado is not None and atualiza_conteudo:
        descricoes.update(consolidado.descricoes)
        hash_novo = hash_conteudo(consolidado, descricoes)

    base = consolidado if atualiza_conteudo and consolidado is not None else linha
    terminou = calcular_termino(base.inicio, base.fim, base.dia_inteiro) <= agora
    cancelado_na_fonte = visto and base.status_fonte == "cancelado"
    contavel = not visto and all((linha.fonte, slug) in sucesso for slug in linha.calendarios)
    hash_mudou = hash_novo != linha.hash_conteudo
    decisao = transicionar(
        atual,
        linha.ausencias_consecutivas,
        Observacao(
            visto=visto,
            hash_mudou=hash_mudou,
            cancelado_na_fonte=cancelado_na_fonte,
            terminou=terminou,
            ausencia_contavel=contavel,
        ),
        limite,
    )

    antes = {campo: getattr(linha, campo) for campo in _CAMPOS_CONTEUDO}
    antes_extra = (linha.localizacao, linha.evidencia_localizacao, list(linha.calendarios))
    if consolidado is not None and atualiza_conteudo:
        _aplicar_conteudo(linha, consolidado)
        linha.calendarios = sorted(set(linha.calendarios) | set(consolidado.calendarios))
        linha.descricoes = descricoes
        linha.hash_conteudo = hash_novo
    if visto:
        linha.localizacao, linha.evidencia_localizacao = _localizacao_gravada(linha)
        linha.ultima_vez_visto = agora
    linha.ausencias_consecutivas = decisao.ausencias

    novo = decisao.estado
    mudou_conteudo = antes != {campo: getattr(linha, campo) for campo in _CAMPOS_CONTEUDO} or (
        antes_extra != (linha.localizacao, linha.evidencia_localizacao, list(linha.calendarios))
    )
    if novo is not atual or hash_mudou or mudou_conteudo:
        linha.atualizado_em = agora
    linha.estado = novo.value

    if novo is _E.ALTERADO and (hash_mudou or atual is not _E.ALTERADO):
        resumo.alterados.append(linha.id)
    if novo is atual:
        return
    if atual is _E.NAO_ENCONTRADO:
        resumo.reativados.append(linha.id)
    destinos = {
        _E.CANCELADO: resumo.cancelados,
        _E.ENCERRADO: resumo.encerrados,
        _E.NAO_ENCONTRADO: resumo.nao_encontrados,
    }
    if novo in destinos:
        destinos[novo].append(linha.id)


PREFIXO_ERRO_SAIDA = "saida_"
# Erros de saída para a mesma chave até o evento entrar em "falha de classificação" (D13).
MAX_ERROS_SAIDA = 3
"""Erros de saída do LLM (contam para a falha de classificação); erros `api_` não contam."""


def _classificado_ok(versao_prompt: str, modelo: str, versao_schema: str) -> Any:
    """Existe classificação `ok` do evento para a chave atual."""
    return exists().where(
        and_(
            Classificacao.evento_id == Evento.id,
            Classificacao.situacao == "ok",
            Classificacao.hash_conteudo == Evento.hash_conteudo,
            Classificacao.versao_prompt == versao_prompt,
            Classificacao.modelo == modelo,
            Classificacao.versao_schema == versao_schema,
        )
    )


def _erros_de_saida_da_chave(versao_prompt: str, modelo: str, versao_schema: str) -> Any:
    """Subconsulta correlacionada: nº de erros de saída do evento para a chave atual."""
    return (
        select(func.count(Classificacao.id))
        .where(
            Classificacao.evento_id == Evento.id,
            Classificacao.situacao == "erro",
            Classificacao.motivo_erro.startswith(PREFIXO_ERRO_SAIDA, autoescape=True),
            Classificacao.hash_conteudo == Evento.hash_conteudo,
            Classificacao.versao_prompt == versao_prompt,
            Classificacao.modelo == modelo,
            Classificacao.versao_schema == versao_schema,
        )
        .correlate(Evento)
        .scalar_subquery()
    )


def eventos_para_classificar(
    sessao: Session,
    versao_prompt: str,
    modelo: str,
    versao_schema: str,
    limite: int,
    max_erros_saida: int | None = None,
) -> list[Evento]:
    """Eventos ativos ou alterados, fora de `localizacao='fora'`, sem classificação `ok` para a
    chave atual (hash do conteúdo + versão do prompt + modelo + versão do schema), por início.

    Com `max_erros_saida`, sai da fila o evento com pelo menos esse número de erros de saída
    (`motivo_erro` começando por "saida_") para a chave atual — ver
    `eventos_com_falha_classificacao` (D13). `None` = sem corte.
    """
    classificado = _classificado_ok(versao_prompt, modelo, versao_schema)
    condicoes = [
        Evento.estado.in_([_E.ATIVO.value, _E.ALTERADO.value]),
        Evento.localizacao != "fora",
        ~classificado,
    ]
    if max_erros_saida is not None:
        condicoes.append(
            _erros_de_saida_da_chave(versao_prompt, modelo, versao_schema) < max_erros_saida
        )
    consulta = select(Evento).where(*condicoes).order_by(Evento.inicio, Evento.id).limit(limite)
    return list(sessao.scalars(consulta))


def eventos_com_falha_classificacao(
    sessao: Session,
    *,
    versao_prompt: str,
    modelo: str,
    versao_schema: str,
    max_erros_saida: int = MAX_ERROS_SAIDA,
) -> list[Evento]:
    """Eventos em falha de classificação (estado derivado, D13), por início.

    Falha = ativo ou alterado, fora de `localizacao='fora'`, sem classificação `ok` e com pelo
    menos `max_erros_saida` erros de saída para a chave atual. Mudar conteúdo, prompt, modelo
    ou schema muda a chave e o evento volta à fila; erros de API (`api_`) não contam.
    """
    consulta = (
        select(Evento)
        .where(
            Evento.estado.in_([_E.ATIVO.value, _E.ALTERADO.value]),
            Evento.localizacao != "fora",
            ~_classificado_ok(versao_prompt, modelo, versao_schema),
            _erros_de_saida_da_chave(versao_prompt, modelo, versao_schema) >= max_erros_saida,
        )
        .order_by(Evento.inicio, Evento.id)
    )
    return list(sessao.scalars(consulta))


def eventos_para_enriquecer(
    sessao: Session,
    *,
    versao_prompt: str,
    modelo: str,
    versao_schema: str,
    slugs_comunidade_ia: frozenset[str],
    limite: int,
) -> list[Evento]:
    """Fila do enriquecimento de preço (D18), por início.

    Evento `ativo`/`alterado`, fora de `localizacao='fora'`, da Luma (`evt-…`) e com link de
    inscrição; com classificação `ok` para a chave atual que diga `e_ia` ou cujo evento seja de
    calendário de comunidade de IA (D15, aplicada aqui na leitura); sem enriquecimento para o
    hash atual (todo resultado, inclusive erro, conta: nada se repete até o hash mudar).
    """
    comunidade = (
        Evento.calendarios.overlap(sorted(slugs_comunidade_ia)) if slugs_comunidade_ia else false()
    )
    classificado_ia = exists().where(
        and_(
            Classificacao.evento_id == Evento.id,
            Classificacao.situacao == "ok",
            Classificacao.hash_conteudo == Evento.hash_conteudo,
            Classificacao.versao_prompt == versao_prompt,
            Classificacao.modelo == modelo,
            Classificacao.versao_schema == versao_schema,
            or_(Classificacao.resultado["e_ia"].as_boolean().is_(True), comunidade),
        )
    )
    enriquecido = exists().where(
        and_(
            Enriquecimento.evento_id == Evento.id,
            Enriquecimento.hash_conteudo == Evento.hash_conteudo,
        )
    )
    consulta = (
        select(Evento)
        .where(
            Evento.estado.in_([_E.ATIVO.value, _E.ALTERADO.value]),
            Evento.localizacao != "fora",
            Evento.id_fonte.startswith("evt-", autoescape=True),
            Evento.link_inscricao.is_not(None),
            classificado_ia,
            ~enriquecido,
        )
        .order_by(Evento.inicio, Evento.id)
        .limit(limite)
    )
    return list(sessao.scalars(consulta))
