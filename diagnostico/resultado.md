# Diagnóstico da Fase 0

Executado em 2026-11-25T14:52:09.829160+00:00

## robots.txt
- https://luma.com/robots.txt: status 200
- https://www.meetup.com/robots.txt: status 200
- https://www.sympla.com.br/robots.txt: status 503
- https://sao-paulo.aitinkerers.org/robots.txt: status 200

## Luma — páginas (descoberta de IDs e JSON-LD)
- https://luma.com/aic-cognition-sp: status 200, calendários ['cal-E74MDlDKBaeAwXK'], cidade ['discplace-AQZnCu9wl4LmOIp'], JSON-LD Event: True
- https://luma.com/aic-sa-2-26: status 200, calendários ['cal-E74MDlDKBaeAwXK'], cidade [], JSON-LD Event: False
- https://luma.com/ayg5q20m: status 200, calendários ['cal-sAAnGigXnTQWpw6'], cidade [], JSON-LD Event: True
- https://luma.com/devin-sp-2: status 200, calendários ['cal-s9CExmUfS7kMu50'], cidade [], JSON-LD Event: True
- https://luma.com/saopaulo: status 200, calendários ['cal-12E3GKkrCVMzPqX', 'cal-7NlnR7M5Ew20s0e', 'cal-87tZfJUGfnwsYEK', 'cal-HtbK5aYvR5B2WAu', 'cal-K9IcbN14WTbkzOs', 'cal-MW9Jul11VErBTNj', 'cal-OkvizsF0APaGInD', 'cal-OoWLL7OgNNy2cCj', 'cal-SDJ8BXWwdvWpZlE', 'cal-hzPakTiFYq4cKjC', 'cal-l9pKMl4NqZDGIYs', 'cal-qLK9y9ELGYVA0j5'], cidade ['discplace-AQZnCu9wl4LmOIp'], JSON-LD Event: False

## Luma — feeds iCal
- calendar cal-12E3GKkrCVMzPqX (Triaxis Capital): 1 eventos, 1 nos próximos 90 dias, 0 parecem IA, 0 citam preço na descrição; campos: ['DESCRIPTION', 'DTEND', 'DTSTAMP', 'DTSTART', 'GEO', 'LOCATION', 'ORGANIZER', 'SEQUENCE', 'STATUS', 'SUMMARY', 'UID']
- calendar cal-7NlnR7M5Ew20s0e (SpaceXSI for Sao Paulo, Brazil): 10 eventos, 1 nos próximos 90 dias, 0 parecem IA, 0 citam preço na descrição; campos: ['DESCRIPTION', 'DTEND', 'DTSTAMP', 'DTSTART', 'GEO', 'LOCATION', 'ORGANIZER', 'SEQUENCE', 'STATUS', 'SUMMARY', 'UID']
- calendar cal-87tZfJUGfnwsYEK (Personal): 18 eventos, 1 nos próximos 90 dias, 0 parecem IA, 0 citam preço na descrição; campos: ['DESCRIPTION', 'DTEND', 'DTSTAMP', 'DTSTART', 'GEO', 'LOCATION', 'ORGANIZER', 'SEQUENCE', 'STATUS', 'SUMMARY', 'UID']
- calendar cal-E74MDlDKBaeAwXK (The AI Collective): 300 eventos, 58 nos próximos 90 dias, 56 parecem IA, 1 citam preço na descrição; campos: ['DESCRIPTION', 'DTEND', 'DTSTAMP', 'DTSTART', 'GEO', 'LOCATION', 'ORGANIZER', 'SEQUENCE', 'STATUS', 'SUMMARY', 'UID']
- calendar cal-HtbK5aYvR5B2WAu (MUG São Paulo - MongoDB User Group): 1 eventos, 1 nos próximos 90 dias, 0 parecem IA, 0 citam preço na descrição; campos: ['DESCRIPTION', 'DTEND', 'DTSTAMP', 'DTSTART', 'GEO', 'LOCATION', 'ORGANIZER', 'SEQUENCE', 'STATUS', 'SUMMARY', 'UID']
- calendar cal-K9IcbN14WTbkzOs (WoMakersCode): 9 eventos, 1 nos próximos 90 dias, 1 parecem IA, 0 citam preço na descrição; campos: ['DESCRIPTION', 'DTEND', 'DTSTAMP', 'DTSTART', 'GEO', 'LOCATION', 'ORGANIZER', 'SEQUENCE', 'STATUS', 'SUMMARY', 'UID']
- calendar cal-MW9Jul11VErBTNj (Personal): 4 eventos, 2 nos próximos 90 dias, 0 parecem IA, 0 citam preço na descrição; campos: ['DESCRIPTION', 'DTEND', 'DTSTAMP', 'DTSTART', 'GEO', 'LOCATION', 'ORGANIZER', 'SEQUENCE', 'STATUS', 'SUMMARY', 'UID']
- calendar cal-OkvizsF0APaGInD (Personal): 2 eventos, 1 nos próximos 90 dias, 0 parecem IA, 0 citam preço na descrição; campos: ['DESCRIPTION', 'DTEND', 'DTSTAMP', 'DTSTART', 'GEO', 'LOCATION', 'ORGANIZER', 'SEQUENCE', 'STATUS', 'SUMMARY', 'UID']
- discplace discplace-AQZnCu9wl4LmOIp: status 400 (não é iCal válido)
- discover discplace-AQZnCu9wl4LmOIp (What’s Happening in São Paulo): 12 eventos, 12 nos próximos 90 dias, 3 parecem IA, 3 citam preço na descrição; campos: ['DESCRIPTION', 'DTEND', 'DTSTAMP', 'DTSTART', 'GEO', 'LOCATION', 'ORGANIZER', 'SEQUENCE', 'STATUS', 'SUMMARY', 'UID']

## Meetup — iCal sem login
- https://www.meetup.com/aidev-saopaulo/events/ical/: status 200, é iCal: True
- https://www.meetup.com/enterprise-ai-sao-paulo/events/ical/: status 200, é iCal: True

## AI Tinkerers SP
- status 200, links de feed: ['https://aitinkerers.org/api/public/v1/events/blog_UMa2gozI_k0/calendar.ics'], JSON-LD Event: False

## Sympla — página de evento
- status 200, JSON-LD Event: False, tem offers (preço): None
