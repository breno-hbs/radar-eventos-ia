"""Prompt versionado do classificador e montagem da mensagem por evento (D13).

O conteúdo do evento é dado não confiável: vai como JSON dentro de `<evento_nao_confiavel>`,
com todo "<" escapado (o texto não consegue fechar o delimitador). Só entram os campos
necessários para classificar; link de inscrição, coordenadas e identificadores ficam fora, e
de `local_url` vai só o host (links de reunião carregam senha na query). Pelo mesmo motivo,
toda URL http(s) nos textos livres (título, local, organizador, descrições) vira só o host,
antes da mensagem e do texto_fonte: o link de inscrição nunca aparece inteiro.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from radar.banco.conteudo import descricoes_distintas
from radar.banco.modelos import Evento

VERSAO_PROMPT = "classificador_v1"
SHA256_PROMPT_V1 = "ed0d100998287a8193f419db8ac4da0e5bb7b7a994045662a2c1487899801ab0"
ARQUIVO_PROMPT = Path(__file__).resolve().parents[3] / "prompts" / "classificador_v1.md"

MAX_CARACTERES_MENSAGEM = 12_000
MAX_CARACTERES_DESCRICOES = 6_000
MAX_CARACTERES_CAMPO = 300
MARCA_CORTE = "[descrição truncada]"
ABRE = "<evento_nao_confiavel>"
FECHA = "</evento_nao_confiavel>"
FUSO_EXIBICAO = ZoneInfo("America/Sao_Paulo")
SEM_HOST = "[link]"

_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_PONTUACAO_FINAL = ".,;:!?)]}>\"'"


def carregar_prompt() -> str:
    """Texto do prompt v1. Arquivo alterado sem trocar a versão → RuntimeError."""
    dados = ARQUIVO_PROMPT.read_bytes()
    if hashlib.sha256(dados).hexdigest() != SHA256_PROMPT_V1:
        raise RuntimeError("prompt alterado sem nova versão (SHA256_PROMPT_V1 não bate)")
    return ARQUIVO_PROMPT.read_text(encoding="utf-8")


def _host_da_url(encontrada: re.Match[str]) -> str:
    url = encontrada.group(0)
    limpa = url.rstrip(_PONTUACAO_FINAL)
    return (_host(limpa) or SEM_HOST) + url[len(limpa) :]


def reduzir_urls(texto: str) -> str:
    """Troca cada URL http(s) só pelo host (sem userinfo, porta, caminho, query, fragmento)."""
    return _URL.sub(_host_da_url, texto)


def _cortar(valor: str | None) -> str | None:
    return None if valor is None else reduzir_urls(valor)[:MAX_CARACTERES_CAMPO]


def _host(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return urlsplit(url.strip()).hostname
    except ValueError:
        return None


def _descricoes_cortadas(evento: Evento, orcamento: int) -> tuple[list[str], list[str]]:
    """(descrições para a mensagem, mesmas sem a marca de corte) dentro do orçamento."""
    enviadas: list[str] = []
    fonte: list[str] = []
    restante = orcamento
    reduzidas = {
        chave: None if texto is None else reduzir_urls(texto)
        for chave, texto in (evento.descricoes or {}).items()
    }
    for descricao in descricoes_distintas(reduzidas):
        if len(descricao) <= restante:
            enviadas.append(descricao)
            fonte.append(descricao)
            restante -= len(descricao)
            continue
        parte = descricao[:restante].rstrip()
        enviadas.append(f"{parte} {MARCA_CORTE}".strip())
        if parte:
            fonte.append(parte)
        break
    return enviadas, fonte


def _montar(evento: Evento, localizacao_filtro: str, orcamento: int) -> tuple[str, str]:
    descricoes, descricoes_fonte = _descricoes_cortadas(evento, orcamento)
    titulo = _cortar(evento.titulo)
    local_texto = _cortar(evento.local_texto)
    organizador = _cortar(evento.organizador)
    host = _host(evento.local_url)
    dados = {
        "titulo": titulo,
        "inicio": evento.inicio.astimezone(FUSO_EXIBICAO).isoformat(),
        "fim": None if evento.fim is None else evento.fim.astimezone(FUSO_EXIBICAO).isoformat(),
        "dia_inteiro": evento.dia_inteiro,
        "local_texto": local_texto,
        "host_local_url": host,
        "organizador": organizador,
        "descricoes": descricoes,
    }
    # "<" só aparece dentro de strings JSON; o escape JSON `\u003c` equivale a ele e
    # não fecha o delimitador.
    corpo = json.dumps(dados, ensure_ascii=False, indent=1).replace("<", "\\u003c")
    oculto = "true" if evento.endereco_oculto else "false"
    mensagem = (
        f"localizacao_filtro: {localizacao_filtro}\n"
        f"endereco_oculto: {oculto}\n"
        f"{ABRE}\n{corpo}\n{FECHA}\n"
    )
    partes = [titulo, local_texto, organizador, host, *descricoes_fonte]
    texto_fonte = "\n".join(p for p in partes if p)
    return mensagem, texto_fonte


def montar_mensagem(evento: Evento, localizacao_filtro: str) -> tuple[str, str]:
    """(mensagem do usuário, texto_fonte contra o qual as evidências são conferidas).

    A mensagem nunca passa de `MAX_CARACTERES_MENSAGEM`: se o escape inflar o JSON, o
    orçamento das descrições diminui até caber.
    """
    orcamento = MAX_CARACTERES_DESCRICOES
    while True:
        mensagem, texto_fonte = _montar(evento, localizacao_filtro, orcamento)
        excesso = len(mensagem) - MAX_CARACTERES_MENSAGEM
        if excesso <= 0 or orcamento == 0:
            return mensagem, texto_fonte
        orcamento = max(0, orcamento - excesso)
