"""
assistant/web.py

Two tools for the open web, both marked untrusted_output: whatever a
page says is fenced as data before the model reads it (see
tools/permissions.py).

- web_search: DuckDuckGo results (web_search.py, ported from kmare-code).
- web_fetch: one URL as readable text, in pages of max_chars.

web_fetch refuses loopback, private-network and link-local addresses by
default, re-checking after every redirect. A URL the model picks may
come from a page it just read, and Escanor runs on a machine that has
local services (e.g. the YouTube Music app's API on localhost) that a
web page must not be able to reach through it.
"""
from __future__ import annotations
import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Callable
from ..tools.base import READ, Tool, ToolResult
from .web_search import web_search

MAX_BYTES = 2_000_000
DEFAULT_MAX_CHARS = 12_000
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Escanor/0.1",
    "Accept": "text/html,application/xhtml+xml,text/plain,application/json;q=0.9,*/*;q=0.5",
}
_TEXT_TYPES = ("text/", "application/json", "application/xml", "application/xhtml+xml", "application/ld+json")


class BlockedURL(Exception):
    pass


Resolver = Callable[[str], list[str]]


def _resolve(host: str) -> list[str]:
    return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})


def check_url(url: str, allow_private: bool = False, resolve: Resolver = _resolve) -> None:
    """Raise BlockedURL unless `url` is http(s) to a public address."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise BlockedURL(f"Only http and https URLs can be fetched (got '{parsed.scheme or 'none'}').")
    if not parsed.hostname:
        raise BlockedURL("The URL has no host.")
    if allow_private:
        return
    try:
        addresses = resolve(parsed.hostname)
    except (socket.gaierror, UnicodeError) as e:
        raise BlockedURL(f"Couldn't resolve {parsed.hostname}: {e}") from e
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise BlockedURL(f"{parsed.hostname} points to a local or private address ({address}); not fetching it.")


class _CheckingRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, allow_private: bool, resolve: Resolver):
        self._allow_private = allow_private
        self._resolve = resolve

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl, self._allow_private, self._resolve)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _TextExtractor(HTMLParser):
    """HTML -> readable text: drops scripts/styles/navigation chrome,
    keeps headings and paragraphs on their own lines."""
    _SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "nav", "footer", "form"}
    _BLOCK = {"p", "div", "section", "article", "main", "br", "li", "ul", "ol", "tr", "table",
              "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "header", "dd", "dt", "hr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self._BLOCK:
            self.parts.append("\n")
            if tag in ("h1", "h2", "h3"):
                self.parts.append("#" * int(tag[1]) + " ")
            elif tag == "li":
                self.parts.append("- ")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip_depth:
            self.parts.append(data)


def html_to_text(html_text: str) -> tuple[str, str]:
    """Returns (title, text)."""
    parser = _TextExtractor()
    parser.feed(html_text)
    parser.close()
    text = "".join(parser.parts)
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.split("\n")]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return " ".join(parser.title.split()), text


def fetch_text(url: str, allow_private: bool = False, resolve: Resolver = _resolve,
               opener: urllib.request.OpenerDirector | None = None, timeout: float = 15) -> tuple[str, str, str]:
    """Fetch `url` and return (final_url, title, text). Raises BlockedURL
    or RuntimeError with a message fit to show the model."""
    check_url(url, allow_private, resolve)
    opener = opener or urllib.request.build_opener(_CheckingRedirects(allow_private, resolve))
    request = urllib.request.Request(url, headers=_HEADERS)
    try:
        with opener.open(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "").lower()
            charset = response.headers.get_content_charset() or "utf-8"
            final_url = response.geturl()
            body = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{url} returned HTTP {e.code} {e.reason}.") from e
    except (urllib.error.URLError, OSError) as e:
        # (A redirect to a blocked address raises BlockedURL straight out
        # of _CheckingRedirects; it isn't an OSError, so it passes through.)
        raise RuntimeError(f"Couldn't fetch {url}: {getattr(e, 'reason', e)}") from e

    if "application/pdf" in content_type:
        raise RuntimeError("That's a PDF; PDF reading arrives with the research tools (Phase 7).")
    if content_type and not content_type.startswith(_TEXT_TYPES):
        raise RuntimeError(f"Not a text page ({content_type.split(';')[0]}); can't read it.")
    truncated = len(body) > MAX_BYTES
    text = body[:MAX_BYTES].decode(charset, errors="replace")
    if "html" in content_type or text.lstrip()[:15].lower().startswith(("<!doctype html", "<html")):
        title, text = html_to_text(text)
    else:
        title = ""
    if truncated:
        text += "\n\n[page larger than 2 MB; the rest was not downloaded]"
    return final_url, title, text


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "Search the web; returns titles, URLs and short snippets only. Use it for anything recent or that "
        "you aren't confident about; when the snippets don't contain the answer, read the best result with "
        "web_fetch. For news, use news_headlines instead. Don't search for small talk or well-known facts."
    )
    input_schema = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "What to search for."}},
        "required": ["query"],
    }
    side_effect = READ
    untrusted_output = True

    def __init__(self, search: Callable[[str], str] = web_search):
        self._search = search

    def run(self, query: str) -> ToolResult:
        if not query.strip():
            return ToolResult(ok=False, output="Empty search query.")
        return ToolResult(ok=True, output=self._search(query))


class WebFetchTool(Tool):
    name = "web_fetch"
    description = (
        "Read a web page as plain text. Use it when the user gives a link, or to read a search result "
        "in full. Long pages come in parts: pass `start` from the previous result to continue."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "An http(s) URL."},
            "start": {"type": "integer", "description": "Character offset to continue from (default 0)."},
        },
        "required": ["url"],
    }
    side_effect = READ
    untrusted_output = True

    def __init__(self, allow_private: bool = False, max_chars: int = DEFAULT_MAX_CHARS,
                 fetch: Callable[..., tuple[str, str, str]] = fetch_text):
        self._allow_private = allow_private
        self._max_chars = max_chars
        self._fetch = fetch

    def run(self, url: str, start: int = 0) -> ToolResult:
        try:
            final_url, title, text = self._fetch(url, allow_private=self._allow_private)
        except (BlockedURL, RuntimeError) as e:
            return ToolResult(ok=False, output=str(e))
        start = max(0, int(start or 0))
        chunk = text[start:start + self._max_chars]
        header = f"{title}\n{final_url}" if title else final_url
        end = start + len(chunk)
        if end < len(text):
            chunk += f"\n\n[showing characters {start}-{end} of {len(text)}; call web_fetch again with start={end} for more]"
        elif not chunk:
            chunk = "(no readable text on this page)" if start == 0 else f"(nothing after character {start})"
        return ToolResult(ok=True, output=f"{header}\n\n{chunk}")
