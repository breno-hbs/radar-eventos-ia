"""Acesso à rede dos coletores: User-Agent, cliente HTTP e robots.txt com falha segura."""

from __future__ import annotations

from urllib.robotparser import RobotFileParser

import httpx

# O robots.txt casa o User-agent pelo token de produto, então ele vem primeiro.
USER_AGENT = "radar-eventos-ia/0.1 (+https://github.com/breno-hbs/radar-eventos-ia)"
TIMEOUT_SEGUNDOS = 30


def criar_cliente() -> httpx.Client:
    """Cliente HTTP do radar: identifica o projeto, tem timeout e não segue redirecionamento."""
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=False,
        timeout=httpx.Timeout(TIMEOUT_SEGUNDOS),
    )


class RobotsTxt:
    """Regras do robots.txt de um host, lidas uma vez.

    Falha segura: 200 aplica as regras; 404/410 libera tudo; qualquer outra resposta
    (3xx, 401, 403, 5xx…) ou erro de rede bloqueia tudo.
    """

    def __init__(self, regras: RobotFileParser | None, liberado: bool) -> None:
        self._regras = regras
        self._liberado = liberado

    @classmethod
    def ler(cls, cliente: httpx.Client, origem: str) -> RobotsTxt:
        """Busca `<origem>/robots.txt` (ex.: `https://api.lu.ma`) sem seguir redirecionamento."""
        try:
            resposta = cliente.get(f"{origem}/robots.txt", follow_redirects=False)
            if resposta.status_code in (404, 410):
                return cls(None, liberado=True)
            if resposta.status_code != 200:
                return cls(None, liberado=False)
            regras = RobotFileParser()
            regras.parse(resposta.text.splitlines())
            return cls(regras, liberado=False)
        except Exception:
            # Qualquer erro (rede, decodificação, parse) bloqueia o host.
            return cls(None, liberado=False)

    def permite(self, url: str) -> bool:
        if self._liberado:
            return True
        if self._regras is None:
            return False
        try:
            return self._regras.can_fetch(USER_AGENT, url)
        except Exception:
            return False
