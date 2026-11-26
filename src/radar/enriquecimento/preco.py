"""Enriquecimento de preço pela página do evento na Luma (D4, D18).

Para cada evento da fila (`repositorio.eventos_para_enriquecer`), busca `https://luma.com/<slug>`
respeitando o robots.txt, sem seguir redirecionamento e sem repetir: todo resultado, inclusive
erro, grava uma linha em `enriquecimentos` para o hash atual. Só `flush`; o commit é de quem
chama. Nenhum texto da página vai para banco, log ou exceção.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit

import httpx
from sqlalchemy.orm import Session

from radar.banco import repositorio
from radar.banco.modelos import Enriquecimento, Evento
from radar.classificacao import modelos, prompt
from radar.config import Configuracao
from radar.enriquecimento.modelos import EstadoPreco, ResultadoPreco
from radar.enriquecimento.pagina import interpretar_pagina
from radar.fontes.rede import RobotsTxt

ORIGEM = "https://luma.com"
HOST = "luma.com"
MAX_BYTES_PAGINA = 2 * 1024 * 1024
_SLUG = re.compile(r"/[A-Za-z0-9][A-Za-z0-9_-]*")
# Caminhos da Luma que não são página de evento.
_RESERVADOS = frozenset(
    {
        "event",
        "user",
        "calendar",
        "discover",
        "home",
        "explore",
        "settings",
        "signin",
        "login",
        "signup",
        "create",
        "pricing",
        "api",
        "ics",
    }
)
_PAGO = ("pago_com_valor", "pago_sem_valor")


@dataclass
class ResumoEnriquecimento:
    paginas_buscadas: int = 0
    gravados: list[tuple[int, str]] = field(default_factory=list)
    erros: list[tuple[int, str]] = field(default_factory=list)
    robots_bloqueado: bool = False
    restantes: bool = False


def estado_preco_efetivo(
    estado_llm: EstadoPreco | None, enriquecimento: Enriquecimento | None
) -> EstadoPreco:
    """Preço usado na decisão do aviso (D18).

    Página conclusiva prevalece, exceto página `gratuito_confirmado` com o LLM dizendo pago
    (ingresso vendido fora da Luma): aí vale o LLM. Página desconhecida ou ausente → LLM.
    """
    pagina = None if enriquecimento is None else enriquecimento.estado_preco
    if pagina is not None and pagina != "preco_desconhecido":
        if pagina == "gratuito_confirmado" and estado_llm in _PAGO:
            return estado_llm  # type: ignore[return-value]
        return pagina  # type: ignore[return-value]
    return estado_llm or "preco_desconhecido"


def url_pagina(link: str | None) -> str | None:
    """`https://luma.com/<slug>` se o link é exatamente isso; senão None (link inválido)."""
    if not link:
        return None
    # String crua: urlsplit removeria espaços/controles e ignoraria "?"/"#" vazios no fim.
    if any(c.isspace() or unicodedata.category(c).startswith("C") for c in link):
        return None
    if link.endswith(("?", "#")):
        return None
    try:
        partes = urlsplit(link)
    except ValueError:
        return None
    if partes.scheme != "https" or partes.netloc != HOST or partes.query or partes.fragment:
        return None
    if not _SLUG.fullmatch(partes.path) or partes.path[1:].casefold() in _RESERVADOS:
        return None
    return f"{ORIGEM}{partes.path}"


def _desconhecido(motivo: str, status_http: int | None = None) -> ResultadoPreco:
    return ResultadoPreco(
        estado_preco="preco_desconhecido",
        motivo=motivo,  # type: ignore[arg-type]
        status_http=status_http,
    )


def _buscar(cliente: httpx.Client, url: str) -> ResultadoPreco:
    """Um GET, sem redirecionamento nem repetição, com corte de tamanho."""
    limite = MAX_BYTES_PAGINA  # lido na hora da chamada
    try:
        # identity: sem compressão, o corte de tamanho vale sobre os bytes que chegam (D18).
        with cliente.stream(
            "GET", url, headers={"Accept-Encoding": "identity"}, follow_redirects=False
        ) as resposta:
            status = resposta.status_code
            if 300 <= status < 400:
                return _desconhecido("redirecionamento", status)
            if status != 200:
                return _desconhecido("status_http", status)
            declarado = resposta.headers.get("content-length", "")
            if declarado.isdigit() and int(declarado) > limite:
                return _desconhecido("pagina_grande", status)
            corpo = bytearray()
            for pedaco in resposta.iter_bytes():
                corpo.extend(pedaco)
                if len(corpo) > limite:
                    return _desconhecido("pagina_grande", status)
            texto = bytes(corpo).decode(resposta.encoding or "utf-8", errors="replace")
    except httpx.HTTPError:
        return _desconhecido("rede")
    return interpretar_pagina(texto).model_copy(update={"status_http": status})


def _gravar(
    sessao: Session, evento: Evento, resultado: ResultadoPreco, agora: datetime, rodada: uuid.UUID
) -> None:
    sessao.add(
        Enriquecimento(
            evento_id=evento.id,
            hash_conteudo=evento.hash_conteudo,
            rodada=rodada,
            criado_em=agora,
            estado_preco=resultado.estado_preco,
            valor_centavos=resultado.valor_centavos,
            moeda=resultado.moeda,
            motivo=resultado.motivo,
            status_http=resultado.status_http,
        )
    )
    sessao.flush()


def enriquecer_precos(
    sessao: Session,
    cliente: httpx.Client,
    config: Configuracao,
    agora: datetime,
    rodada: uuid.UUID,
    slugs_comunidade_ia: frozenset[str],
) -> ResumoEnriquecimento:
    """Enriquece até `limite_paginas_enriquecimento_por_execucao` eventos (D18).

    O limite conta eventos processados (cada um grava uma linha), inclusive os que não geram
    requisição. robots.txt é lido uma vez; ilegível → nada gravado e `robots_bloqueado`.
    """
    if agora.tzinfo is None or agora.utcoffset() is None:
        raise ValueError("agora precisa ter fuso horário")
    resumo = ResumoEnriquecimento()
    limite = config.limite_paginas_enriquecimento_por_execucao
    if limite <= 0:
        return resumo

    # Um a mais que o limite, só para saber se sobram pendentes.
    fila = repositorio.eventos_para_enriquecer(
        sessao,
        versao_prompt=prompt.VERSAO_PROMPT,
        modelo=modelos.MODELO,
        versao_schema=modelos.VERSAO_SCHEMA,
        slugs_comunidade_ia=slugs_comunidade_ia,
        limite=limite + 1,
    )
    if not fila:
        return resumo

    robots = RobotsTxt.ler(cliente, ORIGEM)
    if robots.ilegivel:
        # Sem robots legível nada é lido nem gravado: tenta de novo na próxima execução.
        resumo.robots_bloqueado = True
        resumo.restantes = True
        return resumo

    for evento in fila[:limite]:
        url = url_pagina(evento.link_inscricao)
        if url is None:
            resultado = _desconhecido("link_invalido")
        elif not robots.permite(url):
            resultado = _desconhecido("bloqueado_robots")
        else:
            resumo.paginas_buscadas += 1
            resultado = _buscar(cliente, url)
        _gravar(sessao, evento, resultado, agora, rodada)
        resumo.gravados.append((evento.id, resultado.estado_preco))
        if resultado.motivo is not None:
            resumo.erros.append((evento.id, resultado.motivo))

    resumo.restantes = len(fila) > limite
    return resumo
