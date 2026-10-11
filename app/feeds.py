"""RSS / Atom normalizer; no scraping of paywalled pages."""
from __future__ import annotations
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
import asyncio, hashlib, logging, re, xml.etree.ElementTree as ET
import httpx

log=logging.getLogger(__name__)


def strip_html(s: str) -> str:
    return unescape(re.sub(r'<[^>]*>', ' ', s or '')).strip()[:2500]


def parse_dt(s: str) -> str | None:
    if not s:
        return None
    try:
        d=datetime.fromisoformat(s.strip().replace('Z','+00:00'))
    except ValueError:
        try: d=parsedate_to_datetime(s)
        except (ValueError, TypeError, IndexError): return None
    if d.tzinfo is None: d=d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).isoformat()


def parse_feed(xml_text: str, source: str) -> list[dict]:
    root=ET.fromstring(xml_text)
    result=[]
    entries=root.findall('.//item')
    atom='{http://www.w3.org/2005/Atom}'
    if not entries: entries=root.findall('.//'+atom+'entry')
    for it in entries[:100]:
        def txt(tag):
            element=it.find(tag)
            return (element.text or '').strip() if element is not None else ''
        title=strip_html(txt('title') or txt(atom+'title'))[:300]
        if not title:continue
        url=txt('link')
        if not url:
            item=it.find(atom+'link')
            if item is not None:url=item.get('href','')
        if not url:url=txt(atom+'id')
        summary=strip_html(txt('description') or txt(atom+'summary') or txt(atom+'content'))
        published=parse_dt(txt('pubDate') or txt(atom+'published') or txt(atom+'updated') or txt('date'))
        guid=txt('guid') or txt(atom+'id') or url
        result.append({'source':source,'title':title,'summary':summary,'url':url,
                       'published_at':published,'source_id':guid,'source_symbols':[]})
    return result


async def rss_loop(engine, settings):
    feed_cfg=settings.read_json('feeds.json')
    warmups = engine.rss_warmups
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        while True:
            for source in feed_cfg:
                if not source.get('enabled'): continue
                if source.get('needs_sec_contact') and ('@' not in settings.sec_contact):
                    engine.db.health(source['name'], 'disabled', 'Set SEC_CONTACT to identifiable User-Agent')
                    continue
                ua = settings.sec_contact if source.get('needs_sec_contact') else 'MarketNewsRadar/0.1 (private research; RSS reader)'
                try:
                    response=await client.get(source['url'],headers={'User-Agent':ua,'Accept':'application/rss+xml,application/atom+xml,application/xml,text/xml,*/*'})
                    response.raise_for_status()
                    items=parse_feed(response.text,source['name'])
                    engine.db.health(source['name'],'ok',f'{len(items)} feed items')
                    # Warmup never sends old startup articles; records for later review.
                    source_key = (source['name'], source['url'])
                    for item in reversed(items):await engine.ingest(item, silent=source_key not in warmups)
                    warmups.add(source_key)
                except Exception as exc:
                    log.warning('RSS %s: %s',source['name'],exc)
                    engine.db.health(source['name'],'error',str(exc))
            await asyncio.sleep(settings.rss_interval)
