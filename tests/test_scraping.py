import asyncio
import pytest

from ensemble.scraping import (
    ScrapeError, _PinnedNetworkBackend, _ReadableHTML, _resolve_public_uri,
    _validate_public_uri,
)


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


def test_public_uri_resolution_returns_the_address_used_for_the_connection(monkeypatch):
    monkeypatch.setattr(
        "ensemble.scraping.socket.getaddrinfo",
        lambda host, port, type: [(None, None, None, None, ("93.184.216.34", port))],
    )
    normalized, hostname, address = _resolve_public_uri("https://example.com/docs")
    assert normalized == "https://example.com/docs"
    assert hostname == "example.com"
    assert address == "93.184.216.34"


def test_public_uri_resolution_idna_normalizes_the_pinned_hostname(monkeypatch):
    seen = []
    def resolve(host, port, type):
        seen.append(host)
        return [(None, None, None, None, ("93.184.216.34", port))]
    monkeypatch.setattr("ensemble.scraping.socket.getaddrinfo", resolve)
    _, hostname, _ = _resolve_public_uri("https://bücher.example/")
    assert hostname == "xn--bcher-kva.example"
    assert seen == ["xn--bcher-kva.example"]


def test_public_uri_resolution_rejects_mixed_public_and_private_dns_answers(monkeypatch):
    monkeypatch.setattr(
        "ensemble.scraping.socket.getaddrinfo",
        lambda host, port, type: [
            (None, None, None, None, ("93.184.216.34", port)),
            (None, None, None, None, ("127.0.0.1", port)),
        ],
    )
    with pytest.raises(ScrapeError, match="private"):
        _resolve_public_uri("https://example.com/")


def test_pinned_network_backend_connects_only_to_the_validated_ip():
    class FakeBackend:
        async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            return host, port

        async def connect_unix_socket(self, path, timeout=None, socket_options=None):
            return path

        async def sleep(self, seconds):
            return None

    backend = _PinnedNetworkBackend("example.com", "93.184.216.34")
    backend.backend = FakeBackend()
    assert asyncio.run(backend.connect_tcp("example.com", 443)) == ("93.184.216.34", 443)
    with pytest.raises(OSError, match="unexpected hostname"):
        asyncio.run(backend.connect_tcp("other.example", 443))
