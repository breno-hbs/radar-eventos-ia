"""Texto das mensagens do Telegram (D19): puro, sem rede nem banco.

Texto puro (sem parse_mode), uma informação por linha, campos do evento cortados (título,
local e resumo em 200 unidades UTF-16; URL acima de 500 não é exibida) para que cada
mensagem caiba no limite de 4096 do Telegram. O resumo semanal é dividido em partes sem
quebrar o bloco de um evento.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from radar.banco.modelos import Enriquecimento, Evento
from radar.classificacao.modelos import SaidaClassificador
from radar.enriquecimento.preco import estado_preco_efetivo
from radar.notificacao.sanitizacao import tamanho_telegram, texto_exibivel, url_exibivel

MAX_MENSAGEM = 4096
MAX_CAMPO = 200
DIAS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
# Evento de dia inteiro é uma data de São Paulo, não um instante (D19).
FUSO_DIA_INTEIRO = ZoneInfo("America/Sao_Paulo")

CABECALHO_AVISO = "Novo evento de IA"
CABECALHO_EXCEPCIONAL = "Atenção: evento de IA não classificado, acontece em breve"
CABECALHO_RESUMO = "Resumo semanal de eventos de IA"
CABECALHO_RESUMO_CONTINUACAO = "Resumo semanal de eventos de IA (continuação)"
NAO_CLASSIFICADO = "Não classificado — confira manualmente"
SEM_LINK = "link indisponível"
SEM_TITULO = "(sem título)"

ROTULO_PRECO = {
    "gratuito_confirmado": "Gratuito",
    "pago_com_valor": "Pago",
    "pago_sem_valor": "Pago (valor não informado)",
    "preco_variavel": "Preço variável",
    "preco_desconhecido": "Preço desconhecido",
}


def _dia(momento: datetime) -> str:
    return f"{DIAS[momento.weekday()]}, {momento:%d/%m/%Y}"


def _data(evento: Evento, fuso: ZoneInfo) -> str:
    if evento.dia_inteiro:
        inicio = evento.inicio.astimezone(FUSO_DIA_INTEIRO)
        texto = _dia(inicio)
        if evento.fim is not None:
            # fim exclusivo: o último dia é o anterior ao fim
            ultimo = evento.fim.astimezone(FUSO_DIA_INTEIRO) - timedelta(days=1)
            if ultimo.date() > inicio.date():
                texto = f"{texto} a {_dia(ultimo)}"
        return f"{texto} (dia inteiro)"
    inicio = evento.inicio.astimezone(fuso)
    texto = f"{_dia(inicio)}, {inicio:%H:%M}"
    if evento.fim is None:
        return texto
    fim = evento.fim.astimezone(fuso)
    if fim.date() == inicio.date():
        return f"{texto}–{fim:%H:%M}"
    return f"{texto} – {_dia(fim)}, {fim:%H:%M}"


def _local(evento: Evento) -> str:
    if evento.localizacao == "online":
        return "Online"
    texto = texto_exibivel(evento.local_texto, MAX_CAMPO)
    if texto:
        return texto
    url = url_exibivel(evento.local_url)
    if url:
        return url
    if evento.endereco_oculto:
        return "Endereço oculto (veja a página do evento)"
    return "Local não informado"


def _reais(centavos: int) -> str:
    reais, resto = divmod(centavos, 100)
    return f"R$ {reais:,}".replace(",", ".") + f",{resto:02d}"


def _preco(saida: SaidaClassificador | None, enriquecimento: Enriquecimento | None) -> str:
    estado = estado_preco_efetivo(None if saida is None else saida.estado_preco, enriquecimento)
    if (
        estado == "pago_com_valor"
        and enriquecimento is not None
        and enriquecimento.estado_preco == "pago_com_valor"
        and enriquecimento.valor_centavos is not None
    ):
        return f"Pago — {_reais(enriquecimento.valor_centavos)}"
    return ROTULO_PRECO[estado]


def _idioma(saida: SaidaClassificador | None) -> str | None:
    if saida is None or saida.tem_traducao:
        return None
    if saida.idioma == "desconhecido":
        return "idioma não confirmado"
    if saida.idioma == "en":
        return "Idioma: inglês"
    if saida.idioma == "outro":
        return "Idioma: estrangeiro (não pt/en)"
    return None


def _titulo(evento: Evento) -> str:
    return texto_exibivel(evento.titulo, MAX_CAMPO) or SEM_TITULO


def _link(evento: Evento) -> str:
    return url_exibivel(evento.link_inscricao) or SEM_LINK


def _bloco(
    evento: Evento,
    saida: SaidaClassificador | None,
    enriquecimento: Enriquecimento | None,
    fuso: ZoneInfo,
) -> list[str]:
    linhas = [
        _titulo(evento),
        _data(evento, fuso),
        f"Local: {_local(evento)}",
        f"Preço: {_preco(saida, enriquecimento)}",
    ]
    idioma = _idioma(saida)
    if idioma:
        linhas.append(idioma)
    if saida is None:
        linhas.append(NAO_CLASSIFICADO)
    else:
        linhas.append(texto_exibivel(saida.resumo, MAX_CAMPO) or "")
    linhas.append(_link(evento))
    return [linha for linha in linhas if linha]


def formatar_aviso(
    evento: Evento,
    saida: SaidaClassificador,
    enriquecimento: Enriquecimento | None,
    fuso: ZoneInfo,
) -> str:
    return "\n".join([CABECALHO_AVISO, "", *_bloco(evento, saida, enriquecimento, fuso)])


def formatar_aviso_excepcional(evento: Evento, fuso: ZoneInfo) -> str:
    linhas = [
        CABECALHO_EXCEPCIONAL,
        "",
        _titulo(evento),
        _data(evento, fuso),
        f"Local: {_local(evento)}",
        NAO_CLASSIFICADO,
        _link(evento),
    ]
    return "\n".join(linhas)


def formatar_resumo_semanal(
    itens: Sequence[tuple[Evento, SaidaClassificador | None, Enriquecimento | None]],
    fuso: ZoneInfo,
) -> list[tuple[list[int], str]]:
    """Partes de até 4096 unidades UTF-16, na ordem dos itens; cada evento em uma só parte."""
    partes: list[tuple[list[int], str]] = []
    ids: list[int] = []
    texto = CABECALHO_RESUMO
    for evento, saida, enriquecimento in itens:
        bloco = "\n".join(_bloco(evento, saida, enriquecimento, fuso))
        candidato = f"{texto}\n\n{bloco}"
        if ids and tamanho_telegram(candidato) > MAX_MENSAGEM:
            partes.append((ids, texto))
            ids = []
            texto = CABECALHO_RESUMO_CONTINUACAO
            candidato = f"{texto}\n\n{bloco}"
        ids.append(evento.id)
        texto = candidato
    if ids:
        partes.append((ids, texto))
    return partes
