import pytest

from ensemble.scraping import ScrapeError, _ReadableHTML, _validate_public_uri


def test_scraper_rejects_local_and_non_http_destinations():
    for uri in [
        "file:///etc/passwd",
        "ftp://example.com/file",
        "http://localhost/admin",
        "http://127.0.0.1/",
        "http://192.168.1.1/",
        "http://[::1]/",
        "http://user:password@example.com/",
    ]:
        with pytest.raises(ScrapeError):
            _validate_public_uri(uri)


def test_scraper_rejects_nonstandard_ports():
    with pytest.raises(ScrapeError, match="standard"):
        _validate_public_uri("https://example.com:8443/")


def test_html_extraction_discards_active_and_non_readable_content():
    parser = _ReadableHTML()
    parser.feed("""
      <html><head><title>Docs title</title><script>steal()</script></head>
      <body><h1>Install</h1><p>Run the command.</p>
      <style>.secret{}</style><a href="/next">Next page</a></body></html>
    """)
    title, text, links = parser.result()
    assert title == "Docs title"
    assert "Install" in text and "Run the command." in text
    assert "steal()" not in text and ".secret" not in text
    assert links == ["/next"]


def test_scraper_rejects_credentials_and_malformed_urls():
    for uri in ["", "not a uri", "https://example.com:abc/"]:
        with pytest.raises(ScrapeError):
            _validate_public_uri(uri)
