"""
assistant/web_search.py

A free, no-API-key web search for Escanor (ported from kmare-chat), via DuckDuckGo.
No account, no key, no quota -- matching this project's existing
preference for zero-setup defaults (the mock provider, Ollama needing
no key). Not a general-purpose scraping library.

Two DuckDuckGo endpoints, layered:
1. The no-JS HTML results page -- real web search results, but it's a
   scrape, not an official API, and DuckDuckGo can serve a bot-check
   challenge page instead of results under automated/rapid use
   (confirmed while building this: a handful of requests in quick
   succession was enough to trigger it, and it didn't clear quickly).
2. The official Instant Answer API (api.duckduckgo.com) as a fallback
   when #1 is blocked -- reliable, but much narrower coverage (mostly
   Wikipedia-style topics; frequently empty for recent/specific
   queries, which is exactly when search matters most).

Either way the model gets a real answer back, never a raw exception --
this runs inside a tool-calling loop, and a search failure should be
something the model can see and react to, not something that crashes
the turn.
"""
from __future__ import annotations
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request

_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)"}

_RESULT_RE = re.compile(
    r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
    r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL,
)


def web_search(query: str, max_results: int = 5) -> str:
    results = _html_scrape(query, max_results)
    if results is not None:
        return "\n".join(results) if results else f"No results found for: {query}"

    fallback = _instant_answer_fallback(query)
    if fallback:
        return (
            f"{fallback}\n\n(Note: full web search was temporarily rate-limited; "
            "these are DuckDuckGo's summarized instant-answer results instead, "
            "which may be less complete or current than a full search.)"
        )

    return (
        f"Web search is temporarily unavailable (rate-limited) and no summarized "
        f"answer was found for: {query}. Answer from what you already know, and say "
        "so if you're not confident."
    )


def _html_scrape(query: str, max_results: int) -> list[str] | None:
    """Real web search results via DuckDuckGo's HTML page.

    Returns a list of formatted result strings (possibly empty, if the
    search genuinely found nothing), or None if the request itself was
    blocked -- the caller only falls back to the Instant Answer API in
    the None case, not for a genuine empty result."""
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    request = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError):
        return None

    if "anomaly-modal" in body or "id=\"challenge-form\"" in body:
        return None

    results = []
    for match in _RESULT_RE.finditer(body):
        href, title, snippet = match.groups()
        results.append(f"- {_strip_tags(title)}\n  {_clean_redirect(href)}\n  {_strip_tags(snippet)}")
        if len(results) >= max_results:
            break
    return results


def _instant_answer_fallback(query: str) -> str | None:
    """DuckDuckGo's official JSON API -- reliable, but only useful for
    topics it has a curated abstract or related-topic list for."""
    url = "https://api.duckduckgo.com/?" + urllib.parse.urlencode({"q": query, "format": "json", "no_html": "1"})
    request = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError):
        return None

    parts = []
    if data.get("AbstractText"):
        heading = data.get("Heading") or query
        source = data.get("AbstractSource") or "DuckDuckGo"
        parts.append(f"- {heading} ({source})\n  {data.get('AbstractURL', '')}\n  {data['AbstractText']}")
    for topic in data.get("RelatedTopics", [])[:4]:
        text, topic_url = topic.get("Text"), topic.get("FirstURL")
        if text and topic_url:
            parts.append(f"- {text}\n  {topic_url}")

    return "\n".join(parts) if parts else None


def _strip_tags(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def _clean_redirect(href: str) -> str:
    """DuckDuckGo's HTML results wrap each real URL in a redirect link
    like //duckduckgo.com/l/?uddg=<encoded-real-url>&... -- unwrap it
    so the model (and the user) see the actual destination, not a
    DuckDuckGo tracking link."""
    href = html.unescape(href)
    if "uddg=" in href:
        parsed = urllib.parse.urlparse(href if href.startswith("http") else f"https:{href}")
        query = urllib.parse.parse_qs(parsed.query)
        if "uddg" in query:
            return urllib.parse.unquote(query["uddg"][0])
    return href
