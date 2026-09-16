import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

USER_AGENT = "llm-trader/0.1 (+rss headline watcher)"


@dataclass
class Headline:
    title: str
    published: datetime
    source: str = ""

    def age_minutes(self, now=None):
        now = now or datetime.now(timezone.utc)
        return int((now - self.published).total_seconds() // 60)


def _parse_date(text):
    if not text:
        return None
    try:
        return parsedate_to_datetime(text).astimezone(timezone.utc)
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def parse_feed(xml_text, source=""):
    out = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    for item in root.iter():
        tag = item.tag.split("}")[-1].lower()
        if tag not in ("item", "entry"):
            continue
        title = None
        date = None
        for child in item:
            ctag = child.tag.split("}")[-1].lower()
            if ctag == "title" and child.text:
                title = child.text.strip()
            elif ctag in ("pubdate", "published", "updated", "date") and child.text:
                date = date or child.text
        if title:
            out.append(
                Headline(
                    title=" ".join(title.split()),
                    published=_parse_date(date) or datetime.now(timezone.utc),
                    source=source,
                )
            )
    return out


class NewsProvider:
    name = "null"

    def notes(self, now=None, limit=None):
        return []


class FeedNews(NewsProvider):
    name = "rss"

    def __init__(self, urls, lookback_min=180, max_items=6, timeout=8, cache_s=180):
        self.urls = list(urls)
        self.lookback_min = lookback_min
        self.max_items = max_items
        self.timeout = timeout
        self.cache_s = cache_s
        self._cache = []
        self._fetched_at = 0.0

    def _fetch(self):
        headlines = []
        for url in self.urls:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    body = r.read().decode("utf-8", errors="replace")
            except Exception:
                continue
            headlines += parse_feed(body, source=url.split("/")[2] if "//" in url else url)
        headlines.sort(key=lambda h: h.published, reverse=True)
        return headlines

    def headlines(self, now=None):
        now = now or datetime.now(timezone.utc)
        if time.time() - self._fetched_at > self.cache_s:
            self._cache = self._fetch()
            self._fetched_at = time.time()
        return [h for h in self._cache if h.age_minutes(now) <= self.lookback_min]

    def notes(self, now=None, limit=None):
        limit = limit or self.max_items
        out = []
        for h in self.headlines(now)[:limit]:
            age = h.age_minutes(now)
            src = f" [{h.source}]" if h.source else ""
            out.append(f"HEADLINE {age}m ago{src}: {h.title}")
        return out


def build_news(cfg):
    urls = getattr(cfg, "news_urls", None) or []
    if not urls:
        return NewsProvider()
    return FeedNews(
        urls,
        lookback_min=getattr(cfg, "news_lookback_min", 180),
        max_items=getattr(cfg, "news_max_items", 6),
    )
