from datetime import datetime, timezone

from llmtrader.news import FeedNews, Headline, NewsProvider, build_news, parse_feed

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item><title>Trump says tariffs on China to rise</title>
        <pubDate>Wed, 16 Sep 2026 15:50:00 GMT</pubDate></item>
  <item><title>Fed speaker hints at a cut</title>
        <pubDate>Wed, 16 Sep 2026 15:10:00 GMT</pubDate></item>
  <item><title>Old news from yesterday</title>
        <pubDate>Tue, 15 Sep 2026 09:00:00 GMT</pubDate></item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><title>Atom headline</title>
         <updated>2026-09-16T15:55:00Z</updated></entry>
</feed>"""


def test_parse_rss_items():
    hs = parse_feed(RSS, source="test")
    assert len(hs) == 3
    assert hs[0].title == "Trump says tariffs on China to rise"
    assert hs[0].published.year == 2026


def test_parse_atom_namespaced_entries():
    hs = parse_feed(ATOM)
    assert len(hs) == 1
    assert hs[0].title == "Atom headline"


def test_parse_bad_xml_returns_empty():
    assert parse_feed("<not xml") == []


def test_age_minutes():
    now = datetime(2026, 9, 16, 16, 0, tzinfo=timezone.utc)
    h = Headline(title="x", published=datetime(2026, 9, 16, 15, 45, tzinfo=timezone.utc))
    assert h.age_minutes(now) == 15


def test_feed_news_filters_by_lookback_and_formats_notes(monkeypatch):
    provider = FeedNews(urls=["http://example.com/feed"], lookback_min=60, max_items=5)
    monkeypatch.setattr(provider, "_fetch", lambda: parse_feed(RSS, source="example.com"))
    provider._fetched_at = 0.0
    notes = provider.notes(datetime(2026, 9, 16, 16, 0, tzinfo=timezone.utc))
    assert len(notes) == 2
    assert "HEADLINE 10m ago" in notes[0]
    assert "tariffs" in notes[0]


def test_null_provider_returns_nothing():
    assert NewsProvider().notes() == []


def test_build_news_respects_config():
    cfg = __import__("llmtrader.config", fromlist=["Config"]).Config()
    assert isinstance(build_news(cfg), NewsProvider)
    assert build_news(cfg).name == "null"
    cfg.news_urls = ["http://example.com/feed"]
    built = build_news(cfg)
    assert isinstance(built, FeedNews)
    assert built.max_items == cfg.news_max_items
