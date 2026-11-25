"""Diagnóstico de viabilidade das fontes — Fase 0.

Script descartável: roda UMA vez no GitHub Actions (que tem acesso à internet)
e responde, para cada fonte candidata:
  - o que o robots.txt diz;
  - se existe feed iCal acessível sem login e quais campos ele traz;
  - se as páginas de evento têm dados estruturados (JSON-LD) com preço;
  - quantos eventos parecem ser de IA (contagem grosseira por palavra-chave).

Gera diagnostico/resultado.json e diagnostico/resultado.md.
Não é código do radar: nada aqui será reaproveitado sem revisão.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx
from icalendar import Calendar

UA = "radar-eventos-ia-diagnostico/0.1 (+https://github.com/breno-hbs/radar-eventos-ia)"
PAUSA_SEGUNDOS = 3  # educação com os servidores: uma requisição a cada 3 s
SAIDA = Path(__file__).parent

ROBOTS = [
    "https://luma.com/robots.txt",
    "https://www.meetup.com/robots.txt",
    "https://www.sympla.com.br/robots.txt",
    "https://sao-paulo.aitinkerers.org/robots.txt",
]

# Páginas públicas da Luma usadas SÓ para descobrir os IDs dos calendários
# (cal-...) e da cidade (discplace-...). O radar usará apenas o iCal.
LUMA_PAGINAS = [
    "https://luma.com/aic-cognition-sp",
    "https://luma.com/aic-sa-2-26",
    "https://luma.com/ayg5q20m",
    "https://luma.com/devin-sp-2",
    "https://luma.com/saopaulo",
]
LUMA_ICS = "https://api.lu.ma/ics/get?entity={entity}&id={id}"

MEETUP_ICS = [
    "https://www.meetup.com/aidev-saopaulo/events/ical/",
    "https://www.meetup.com/enterprise-ai-sao-paulo/events/ical/",
]

AITINKERERS = "https://sao-paulo.aitinkerers.org/"

SYMPLA_EVENTO = "https://www.sympla.com.br/evento/6-evento-mulheres-em-ia-mia/3627822"

PALAVRAS_IA = re.compile(
    r"\b(ia|ai|inteligência artificial|inteligencia artificial|artificial intelligence|"
    r"llm|llms|genai|machine learning|aprendizado de máquina|agentes?|agents?|"
    r"chatgpt|openai|claude|gemini|copilot|rag|mlops|deep learning|nlp)\b",
    re.IGNORECASE,
)
PRECO = re.compile(r"(R\$\s?\d|grátis|gratuito|gratuita|free|ingresso|ticket)", re.IGNORECASE)

HORIZONTE_DIAS = 90


def baixar(cliente: httpx.Client, url: str) -> dict[str, Any]:
    """GET com pausa e registro de status. Nunca levanta exceção."""
    time.sleep(PAUSA_SEGUNDOS)
    try:
        r = cliente.get(url)
        return {
            "url": url,
            "url_final": str(r.url),
            "status": r.status_code,
            "content_type": r.headers.get("content-type", ""),
            "tamanho": len(r.content),
            "texto": r.text,
        }
    except httpx.HTTPError as erro:
        return {"url": url, "status": None, "erro": f"{type(erro).__name__}: {erro}", "texto": ""}


def sem_texto(resp: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in resp.items() if k != "texto"}


def json_ld(html: str) -> list[Any]:
    blocos = re.findall(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL
    )
    saida = []
    for bloco in blocos:
        try:
            saida.append(json.loads(bloco))
        except json.JSONDecodeError:
            saida.append({"_invalido": bloco[:200]})
    return saida


def resumo_json_ld(blocos: list[Any]) -> dict[str, Any]:
    """Diz se há um schema.org Event e quais campos úteis ele traz."""
    def itens(b: Any):
        if isinstance(b, list):
            for x in b:
                yield from itens(x)
        elif isinstance(b, dict):
            if "@graph" in b:
                yield from itens(b["@graph"])
            yield b

    eventos = [i for b in blocos for i in itens(b) if "Event" in str(i.get("@type", ""))]
    if not eventos:
        return {"tem_event": False, "qtd_blocos": len(blocos)}
    e = eventos[0]
    offers = e.get("offers")
    return {
        "tem_event": True,
        "campos": sorted(e.keys()),
        "tem_startDate": "startDate" in e,
        "tem_location": "location" in e,
        "tem_offers": offers is not None,
        "offers_exemplo": json.dumps(offers, ensure_ascii=False)[:300] if offers else None,
        "attendanceMode": e.get("eventAttendanceMode"),
        "inLanguage": e.get("inLanguage"),
    }


def analisar_ics(texto: str) -> dict[str, Any]:
    """Lê um feed iCal e resume campos, volume e sinais de IA/preço."""
    cal = Calendar.from_ical(texto)
    agora = dt.datetime.now(dt.timezone.utc)
    limite = agora + dt.timedelta(days=HORIZONTE_DIAS)
    campos: dict[str, int] = {}
    total = futuros = futuros_ia = com_preco_na_descricao = 0
    amostra = []
    for ev in cal.walk("VEVENT"):
        total += 1
        for chave in ev.keys():
            campos[chave] = campos.get(chave, 0) + 1
        inicio = ev.get("DTSTART")
        inicio = inicio.dt if inicio else None
        if isinstance(inicio, dt.date) and not isinstance(inicio, dt.datetime):
            inicio = dt.datetime.combine(inicio, dt.time(), tzinfo=dt.timezone.utc)
        if isinstance(inicio, dt.datetime) and inicio.tzinfo is None:
            inicio = inicio.replace(tzinfo=dt.timezone.utc)
        titulo = str(ev.get("SUMMARY", ""))
        descricao = str(ev.get("DESCRIPTION", ""))
        if PRECO.search(descricao):
            com_preco_na_descricao += 1
        if inicio and agora <= inicio <= limite:
            futuros += 1
            e_ia = bool(PALAVRAS_IA.search(f"{titulo} {descricao}"))
            futuros_ia += e_ia
            if len(amostra) < 8:
                # Amostra mínima: só dados públicos do evento, sem descrição completa.
                amostra.append(
                    {
                        "titulo": titulo,
                        "inicio": inicio.isoformat(),
                        "local": str(ev.get("LOCATION", ""))[:80],
                        "url": str(ev.get("URL", "")),
                        "tamanho_descricao": len(descricao),
                        "parece_ia": e_ia,
                    }
                )
    return {
        "nome_calendario": str(cal.get("X-WR-CALNAME", "")),
        "total_eventos": total,
        f"futuros_{HORIZONTE_DIAS}d": futuros,
        f"futuros_{HORIZONTE_DIAS}d_parecem_ia": futuros_ia,
        "eventos_com_preco_na_descricao": com_preco_na_descricao,
        "campos_presentes": campos,
        "amostra": amostra,
    }


def main() -> None:
    res: dict[str, Any] = {"executado_em": dt.datetime.now(dt.timezone.utc).isoformat()}
    with httpx.Client(headers={"User-Agent": UA}, follow_redirects=True, timeout=30) as c:
        # 1. robots.txt
        res["robots"] = []
        for url in ROBOTS:
            r = baixar(c, url)
            res["robots"].append({**sem_texto(r), "conteudo": r["texto"][:3000]})

        # 2. Luma: descobrir IDs e checar JSON-LD
        cal_ids: set[str] = set()
        place_ids: set[str] = set()
        res["luma_paginas"] = []
        for url in LUMA_PAGINAS:
            r = baixar(c, url)
            html = r["texto"]
            ids_cal = sorted(set(re.findall(r"cal-[A-Za-z0-9]{10,}", html)))
            ids_place = sorted(set(re.findall(r"discplace-[A-Za-z0-9]{10,}", html)))
            cal_ids.update(ids_cal)
            place_ids.update(ids_place)
            res["luma_paginas"].append(
                {
                    **sem_texto(r),
                    "ids_calendario": ids_cal,
                    "ids_cidade": ids_place,
                    "json_ld": resumo_json_ld(json_ld(html)),
                    "links_ics_na_pagina": sorted(set(re.findall(r"https?://[^\"'\s]*ics[^\"'\s]*", html)))[:10],
                }
            )

        # 3. Luma: feeds iCal (calendários e cidade; testa os dois nomes de entidade)
        res["luma_ics"] = []
        alvos = [("calendar", i) for i in sorted(cal_ids)[:8]]
        alvos += [(ent, i) for i in sorted(place_ids)[:2] for ent in ("discplace", "discover")]
        for entidade, ident in alvos:
            url = LUMA_ICS.format(entity=entidade, id=ident)
            r = baixar(c, url)
            item: dict[str, Any] = {**sem_texto(r), "entidade": entidade, "id": ident}
            if r["status"] == 200 and "BEGIN:VCALENDAR" in r["texto"][:200]:
                try:
                    item["analise"] = analisar_ics(r["texto"])
                except Exception as erro:  # diagnóstico: registra e segue
                    item["erro_parse"] = f"{type(erro).__name__}: {erro}"
            else:
                item["inicio_resposta"] = r["texto"][:200]
            res["luma_ics"].append(item)

        # 4. Meetup: iCal funciona sem login?
        res["meetup_ics"] = []
        for url in MEETUP_ICS:
            r = baixar(c, url)
            e_ics = "BEGIN:VCALENDAR" in r["texto"][:200]
            item = {**sem_texto(r), "e_icalendar": e_ics}
            if e_ics:
                try:
                    item["analise"] = analisar_ics(r["texto"])
                except Exception as erro:
                    item["erro_parse"] = f"{type(erro).__name__}: {erro}"
            res["meetup_ics"].append(item)

        # 5. AI Tinkerers: tem feed?
        r = baixar(c, AITINKERERS)
        html = r["texto"]
        res["aitinkerers"] = {
            **sem_texto(r),
            "links_feed": sorted(
                set(re.findall(r"https?://[^\"'\s<>]*(?:\.ics|ical|rss|feed|calendar)[^\"'\s<>]*", html, re.I))
            )[:15],
            "json_ld": resumo_json_ld(json_ld(html)),
            "links_evento": sorted(set(re.findall(r"/p/[a-z0-9\-]+", html)))[:10],
        }

        # 6. Sympla: página de evento tem JSON-LD com preço?
        r = baixar(c, SYMPLA_EVENTO)
        res["sympla_evento"] = {**sem_texto(r), "json_ld": resumo_json_ld(json_ld(r["texto"]))}

    (SAIDA / "resultado.json").write_text(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    (SAIDA / "resultado.md").write_text(relatorio_md(res))
    print(relatorio_md(res))


def relatorio_md(res: dict[str, Any]) -> str:
    h = HORIZONTE_DIAS
    l = [f"# Diagnóstico da Fase 0\n\nExecutado em {res['executado_em']}\n", "## robots.txt"]
    for r in res["robots"]:
        l.append(f"- {r['url']}: status {r['status']}")
    l.append("\n## Luma — páginas (descoberta de IDs e JSON-LD)")
    for p in res["luma_paginas"]:
        l.append(
            f"- {p['url']}: status {p['status']}, calendários {p['ids_calendario']}, "
            f"cidade {p['ids_cidade']}, JSON-LD Event: {p['json_ld'].get('tem_event')}"
        )
    l.append("\n## Luma — feeds iCal")
    for i in res["luma_ics"]:
        a = i.get("analise")
        if a:
            l.append(
                f"- {i['entidade']} {i['id']} ({a['nome_calendario']}): {a['total_eventos']} eventos, "
                f"{a[f'futuros_{h}d']} nos próximos {h} dias, {a[f'futuros_{h}d_parecem_ia']} parecem IA, "
                f"{a['eventos_com_preco_na_descricao']} citam preço na descrição; "
                f"campos: {sorted(a['campos_presentes'])}"
            )
        else:
            l.append(f"- {i['entidade']} {i['id']}: status {i['status']} (não é iCal válido)")
    l.append("\n## Meetup — iCal sem login")
    for i in res["meetup_ics"]:
        l.append(f"- {i['url']}: status {i['status']}, é iCal: {i['e_icalendar']}")
    a = res["aitinkerers"]
    l.append(
        f"\n## AI Tinkerers SP\n- status {a['status']}, links de feed: {a['links_feed']}, "
        f"JSON-LD Event: {a['json_ld'].get('tem_event')}"
    )
    s = res["sympla_evento"]
    l.append(
        f"\n## Sympla — página de evento\n- status {s['status']}, JSON-LD Event: {s['json_ld'].get('tem_event')}, "
        f"tem offers (preço): {s['json_ld'].get('tem_offers')}"
    )
    return "\n".join(l) + "\n"


if __name__ == "__main__":
    main()
