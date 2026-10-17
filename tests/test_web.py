import email.message
import io
import pytest
from escanor.assistant import web
from escanor.assistant.web import BlockedURL, WebFetchTool, WebSearchTool, check_url, fetch_text, html_to_text


def resolver(mapping):
    return lambda host: mapping[host]


PUBLIC = resolver({"example.com": ["93.184.215.14"], "evil.test": ["127.0.0.1"], "mixed.test": ["8.8.8.8", "10.0.0.5"]})


@pytest.mark.parametrize("url,reason", [
    ("ftp://example.com/x", "Only http and https"),
    ("http:///nohost", "no host"),
    ("http://evil.test/", "local or private"),
    ("http://mixed.test/", "local or private"),  # any private address is enough
])
def test_should_block_url_when_not_public_http(url, reason):
    with pytest.raises(BlockedURL, match=reason):
        check_url(url, resolve=PUBLIC)


def test_should_allow_public_https_url():
    check_url("https://example.com/page", resolve=PUBLIC)


def test_should_allow_private_host_when_configured():
    check_url("http://evil.test/", allow_private=True, resolve=PUBLIC)


def test_should_block_redirect_to_private_address():
    handler = web._CheckingRedirects(False, PUBLIC)
    with pytest.raises(BlockedURL):
        handler.redirect_request(None, None, 302, "Found", {}, "http://evil.test/admin")


def test_should_extract_title_headings_and_drop_scripts_when_converting_html():
    title, text = html_to_text(
        "<html><head><title> My  Page </title><style>p{}</style></head><body>"
        "<nav>menu</nav><h1>Hello</h1><p>First &amp; second.</p><script>alert(1)</script>"
        "<ul><li>one</li><li>two</li></ul></body></html>"
    )
    assert title == "My Page"
    assert text == "# Hello\n\nFirst & second.\n\n- one\n\n- two"
    assert "alert" not in text and "menu" not in text


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, content_type: str, url: str = "https://example.com/final"):
        super().__init__(body)
        self.headers = email.message.Message()
        self.headers["Content-Type"] = content_type
        self._url = url

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    def __init__(self, response):
        self.response = response

    def open(self, request, timeout):
        return self.response


def test_should_return_text_when_page_is_html():
    opener = FakeOpener(FakeResponse(b"<title>T</title><p>Body</p>", "text/html; charset=utf-8"))
    assert fetch_text("https://example.com", resolve=PUBLIC, opener=opener) == ("https://example.com/final", "T", "Body")


def test_should_refuse_when_page_is_binary():
    opener = FakeOpener(FakeResponse(b"\x89PNG", "image/png"))
    with pytest.raises(RuntimeError, match="Not a text page"):
        fetch_text("https://example.com", resolve=PUBLIC, opener=opener)


def test_should_explain_when_page_is_pdf():
    opener = FakeOpener(FakeResponse(b"%PDF", "application/pdf"))
    with pytest.raises(RuntimeError, match="PDF"):
        fetch_text("https://example.com", resolve=PUBLIC, opener=opener)


def test_should_page_long_text_when_fetch_tool_runs():
    tool = WebFetchTool(max_chars=10, fetch=lambda url, allow_private: ("https://x.test", "Title", "0123456789abcdefghij"))
    first = tool.run(url="https://x.test")
    assert first.output.startswith("Title\nhttps://x.test\n\n0123456789")
    assert "start=10" in first.output
    second = tool.run(url="https://x.test", start=10)
    assert "abcdefghij" in second.output and "start=" not in second.output


def test_should_return_error_result_when_fetch_blocked():
    def blocked(url, allow_private):
        raise BlockedURL("nope")
    assert WebFetchTool(fetch=blocked).run(url="http://evil.test").ok is False


def test_should_reject_empty_query_when_searching():
    assert WebSearchTool(search=lambda q: "results").run(query="  ").ok is False
    assert WebSearchTool(search=lambda q: f"results for {q}").run(query="kokoro").output == "results for kokoro"


def test_web_tools_are_marked_untrusted():
    assert WebSearchTool.untrusted_output and WebFetchTool.untrusted_output
