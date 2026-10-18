"""
assistant/news.py

Real headlines. A web search for "latest news" mostly finds news sites'
front pages ("Google News - Headlines", "CBS World News") with no actual
stories in them, so the model had nothing to report (B20). Google News'
public RSS feeds give dated headlines with their source, need no key,
and follow your region and language ([assistant] news_region).

- news_headlines()            top stories
- news_headlines("cricket")   a topic or search

Headlines come from outside, so the output is untrusted like any web text.
"""
from __future__ import annotations
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable
from ..tools.base import READ, Tool, ToolResult

FEED = "https://news.google.com/rss"
MAX_BYTES = 2_000_000


@dataclass
class Headline:
    title: str
    source: str
    published: datetime | None
    link: str


def feed_url(topic: str | None, region: str) -> str:
    """region like 'IN:en' (country:language)."""
    country, _, language = (region or "IN:en").partition(":")
    language = language or "en"
    params = {"hl": f"{language}-{country}", "gl": country, "ceid": f"{country}:{language}"}
    if topic and topic.strip():
        return f"{FEED}/search?" + urllib.parse.urlencode({"q": topic.strip(), **params})
    return f"{FEED}?" + urllib.parse.urlencode(params)


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Escanor/0.1"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read(MAX_BYTES)


def parse_feed(data: bytes) -> list[Headline]:
    root = ET.fromstring(data)
    headlines = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        source = (item.findtext("source") or "").strip()
        if source and title.endswith(f" - {source}"):
            title = title[: -len(f" - {source}")]
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "")
        except (TypeError, ValueError):
            published = None
        if title:
            headlines.append(Headline(title, source, published, (item.findtext("link") or "").strip()))
    return headlines


def _age(published: datetime | None, now: datetime) -> str:
    if published is None:
        return ""
    minutes = int((now - published).total_seconds() // 60)
    if minutes < 60:
        return f"{max(minutes, 1)}m ago"
    if minutes < 48 * 60:
        return f"{minutes // 60}h ago"
    return f"{minutes // 1440}d ago"


class News:
    def __init__(self, region: str = "IN:en", fetch: Callable[[str], bytes] = _get,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.region = region
        self._fetch = fetch
        self._now = now

    def headlines(self, topic: str | None = None, limit: int = 8) -> list[Headline]:
        items = parse_feed(self._fetch(feed_url(topic, self.region)))
        if topic:  # search results come by relevance; newest first reads better
            items.sort(key=lambda h: h.published or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return items[:limit]

    def text(self, topic: str | None = None, limit: int = 8) -> str:
        items = self.headlines(topic, limit)
        if not items:
            return f"No headlines found{f' for {topic!r}' if topic else ''}."
        now = self._now()
        return "\n".join(
            f"- {h.title} ({', '.join(p for p in (h.source, _age(h.published, now)) if p)})" for h in items
        )


class NewsTool(Tool):
    name = "news_headlines"
    description = (
        "Current news headlines with source and age. No topic = top stories; or a topic like 'tech', "
        "'cricket', 'Bengaluru', 'OpenAI'. Use this (not web_search) for news; open a story with web_fetch "
        "only if the user wants details."
    )
    input_schema = {
        "type": "object",
        "properties": {"topic": {"type": "string"}, "limit": {"type": "integer", "description": "default 8"}},
    }
    side_effect = READ
    untrusted_output = True

    def __init__(self, news: News):
        self.news = news

    def run(self, topic: str | None = None, limit: int = 8) -> ToolResult:
        try:
            return ToolResult(ok=True, output=self.news.text(topic, max(1, min(int(limit or 8), 20))))
        except (OSError, ET.ParseError) as e:
            return ToolResult(ok=False, output=f"Couldn't load the news feed: {e}")
