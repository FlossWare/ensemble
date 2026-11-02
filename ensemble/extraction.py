from __future__ import annotations

from html.parser import HTMLParser


class _TextExtractor(HTMLParser):
    BLOCK_TAGS = {
        "address", "article", "aside", "blockquote", "br", "dd", "div", "dl",
        "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2",
        "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol",
        "p", "pre", "section", "table", "td", "th", "tr", "ul",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs):
        if tag in {"script", "style", "noscript", "template"}:
            self.skip_depth += 1
        elif self.skip_depth == 0 and tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str):
        if tag in {"script", "style", "noscript", "template"} and self.skip_depth:
            self.skip_depth -= 1
        elif self.skip_depth == 0 and tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str):
        if self.skip_depth == 0:
            self.parts.append(data)


def extract_text(content: str, media_type: str) -> str:
    normalized = media_type.split(";", 1)[0].strip().lower()
    if normalized in {"text/plain", "text/markdown"}:
        return content.strip()
    if normalized in {"text/html", "application/xhtml+xml"}:
        parser = _TextExtractor()
        parser.feed(content)
        return "\n".join(
            line.strip() for line in "".join(parser.parts).splitlines() if line.strip()
        )
    raise ValueError(f"unsupported extraction media type: {normalized}")
