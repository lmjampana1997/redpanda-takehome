"""Turns a MediaWiki action=compare diff HTML fragment into plain
added/removed text. This is the extraction logic milestone 3 deliberately
left out of Connect — a diff table needs real HTML parsing (tracking which
<td> is actually changed vs. unchanged context), not a tag-stripping regex.
"""

from html.parser import HTMLParser


class _DiffHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.added: list[str] = []
        self.removed: list[str] = []
        self._current_side: str | None = None
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "td":
            return
        classes = dict(attrs).get("class") or ""
        # diff-addedline/diff-deletedline mark actually-changed cells.
        # diff-context marks unchanged surrounding lines — skip those, or
        # every diff would be padded with lines nothing happened to.
        if "diff-addedline" in classes:
            self._current_side = "added"
        elif "diff-deletedline" in classes:
            self._current_side = "removed"
        else:
            self._current_side = None
        self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._current_side:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "td" or not self._current_side:
            return
        text = "".join(self._buffer).strip()
        if text:
            (self.added if self._current_side == "added" else self.removed).append(text)
        self._current_side = None
        self._buffer = []


def parse_diff_html(diff_html: str | None) -> tuple[list[str], list[str]]:
    """Returns (added_lines, removed_lines)."""
    parser = _DiffHTMLParser()
    parser.feed(diff_html or "")
    return parser.added, parser.removed


def format_diff_for_prompt(diff_html: str | None, max_chars: int = 4000) -> str:
    added, removed = parse_diff_html(diff_html)
    parts = []
    if removed:
        parts.append("REMOVED:\n" + "\n".join(f"- {line}" for line in removed))
    if added:
        parts.append("ADDED:\n" + "\n".join(f"+ {line}" for line in added))
    text = "\n\n".join(parts) if parts else "(no diff content available)"
    return text[:max_chars]
