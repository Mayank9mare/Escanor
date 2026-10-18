from datetime import datetime, timezone
from escanor.assistant.news import News, NewsTool, feed_url, parse_feed

FEED = b"""<?xml version="1.0"?><rss><channel>
<item><title>Rains lash Mumbai - The Hindu</title><source>The Hindu</source>
  <pubDate>Sun, 18 Oct 2026 05:00:00 GMT</pubDate><link>https://news.google.com/a</link></item>
<item><title>Chip exports rise - Livemint</title><source>Livemint</source>
  <pubDate>Sun, 18 Oct 2026 07:20:00 GMT</pubDate><link>https://news.google.com/b</link></item>
<item><title>No date story</title></item>
</channel></rss>"""
NOW = datetime(2026, 10, 18, 7, 30, tzinfo=timezone.utc)


def test_feed_url_for_top_stories_and_topics():
    assert feed_url(None, "IN:en") == "https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN%3Aen"
    assert feed_url("tech news", "US:en").startswith("https://news.google.com/rss/search?q=tech+news&hl=en-US")


def test_parse_strips_source_suffix_and_reads_dates():
    items = parse_feed(FEED)
    assert [h.title for h in items] == ["Rains lash Mumbai", "Chip exports rise", "No date story"]
    assert items[0].source == "The Hindu" and items[2].published is None


def test_topic_results_newest_first_with_age():
    news = News("IN:en", fetch=lambda url: FEED, now=lambda: NOW)
    assert news.text().splitlines()[0] == "- Rains lash Mumbai (The Hindu, 2h ago)"
    assert news.text("economy").splitlines()[0] == "- Chip exports rise (Livemint, 10m ago)"


def test_tool_reports_feed_errors_and_is_untrusted():
    def down(url):
        raise OSError("offline")
    assert NewsTool(News(fetch=down)).run().ok is False
    assert NewsTool.untrusted_output
