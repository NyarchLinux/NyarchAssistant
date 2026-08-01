import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CitationSource:
    number: int
    label: str
    raw: str
    target: str | None = None
    kind: str = "source"


_SOURCES_HEADING_PATTERN = re.compile(
    r"^[ \t]*#{1,6}[ \t]+Sources:?[ \t]*$",
    flags=re.IGNORECASE | re.MULTILINE,
)
_SOURCE_ENTRY_PATTERN = re.compile(
    r"^[ \t]*(?:[-*+][ \t]+)?(?:\*\*)?(?:\[(\d+)\]|(\d+)\.)(?:\*\*)?[ \t]+(.+?)[ \t]*$"
)
_MARKDOWN_LINK_PATTERN = re.compile(r"^\[([^\]]+)\]\((.+)\)\.?$")
_LOCAL_PATH_PATTERN = re.compile(r"^(.+?)[ \t]+[—-][ \t]+`([^`]+)`\.?$")
_TARGET_SUFFIX_PATTERN = re.compile(r"^(.+?)[ \t]+[—-][ \t]+(https?://\S+|/\S+)\.?$")


def format_source_context(
    content: str,
    source: str,
    title: str | None = None,
    source_type: str | None = None,
) -> str:
    """Format contextual content with provenance kept next to the passage."""
    source = str(source or "Unknown source").strip() or "Unknown source"
    lines = ["---"]
    if source_type:
        lines.append(f"Source type: {source_type}")
    lines.append(f"Source: {source}")
    if title:
        lines.append(f"Title: {title}")
    lines.extend(("Content:", str(content or "").strip()))
    return "\n".join(lines)


def source_from_metadata(metadata: dict | None, fallback: str = "Unknown source") -> str:
    """Return the most useful source identifier stored by document readers."""
    metadata = metadata or {}
    for key in ("source", "url", "file_path", "file_name"):
        value = metadata.get(key)
        if value:
            return str(value)
    return fallback


def infer_provided_text_source(text: str) -> str:
    """Recover provenance embedded by integrations before text enters RAG."""
    match = re.search(r"^Source:\s*(\S.*?)\s*$", text, flags=re.MULTILINE)
    if match:
        return match.group(1)
    return "Provided text"


def _parse_source_entry(number: int, raw: str) -> CitationSource:
    raw = raw.strip()
    link_match = _MARKDOWN_LINK_PATTERN.match(raw)
    if link_match:
        label, target = link_match.groups()
        return CitationSource(number, label.strip(), raw, target.strip(), "web")

    path_match = _LOCAL_PATH_PATTERN.match(raw)
    if path_match:
        label, target = path_match.groups()
        return CitationSource(number, label.strip(), raw, target.strip(), "file")

    target_match = _TARGET_SUFFIX_PATTERN.match(raw)
    if target_match:
        label, target = target_match.groups()
        kind = "web" if target.startswith(("https://", "http://")) else "file"
        return CitationSource(number, label.strip(), raw, target.rstrip("."), kind)

    if raw.startswith(("https://", "http://")):
        return CitationSource(number, raw, raw, raw, "web")

    lowered = raw.lower()
    if lowered.startswith("user message"):
        kind = "user"
    elif lowered.startswith("saved memory"):
        kind = "memory"
    elif lowered.startswith("tool:"):
        kind = "tool"
    else:
        kind = "source"
    return CitationSource(number, raw, raw, kind=kind)


def extract_source_section(markdown: str) -> tuple[str, list[CitationSource]]:
    """Extract a trailing Markdown Sources section without changing stored text."""
    headings = list(_SOURCES_HEADING_PATTERN.finditer(markdown))
    if not headings:
        return markdown, []

    heading = headings[-1]
    body = markdown[heading.end():]
    sources = []
    seen_numbers = set()
    for line in body.splitlines():
        if not line.strip():
            continue
        match = _SOURCE_ENTRY_PATTERN.match(line)
        if not match:
            return markdown, []
        number = int(match.group(1) or match.group(2))
        if number in seen_numbers:
            continue
        seen_numbers.add(number)
        sources.append(_parse_source_entry(number, match.group(3)))

    if not sources:
        return markdown, []

    visible_markdown = markdown[:heading.start()].rstrip()
    return visible_markdown, sources
