"""HTML cleaning and text chunking for AI extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup, Comment, Tag

from config import MAX_CHUNK_CHARS, MIN_CHUNK_CHARS
from utils import get_logger

logger = get_logger(__name__)

_STRIP_TAGS = frozenset(
    {
        "script",
        "style",
        "noscript",
        "iframe",
        "svg",
        "nav",
        "footer",
        "header",
        "aside",
        "form",
        "button",
    }
)
_NOISE_CLASS_RE = re.compile(
    r"cookie|banner|popup|modal|advert|ads|sidebar|menu|nav|footer|header",
    re.I,
)


@dataclass
class TextChunk:
    """A section of cleaned page text ready for the AI agent."""

    text: str
    source_url: str
    section_hint: str = ""


def _tag_classes(el: Tag) -> str:
    """Safely read class list (some BS4 nodes have attrs=None)."""
    attrs = getattr(el, "attrs", None)
    if not isinstance(attrs, dict):
        return ""
    raw = attrs.get("class") or []
    if isinstance(raw, str):
        return raw
    return " ".join(str(c) for c in raw)


def _tag_id(el: Tag) -> str:
    attrs = getattr(el, "attrs", None)
    if not isinstance(attrs, dict):
        return ""
    val = attrs.get("id") or ""
    return str(val)


def clean_html(html: str, page_url: str) -> str:
    """Remove boilerplate and return readable plain text."""
    if not html:
        return ""

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(_STRIP_TAGS):
        tag.decompose()

    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()

    noise_nodes: list[Tag] = []
    for el in soup.find_all(True):
        if not isinstance(el, Tag):
            continue
        classes = _tag_classes(el)
        el_id = _tag_id(el)
        if _NOISE_CLASS_RE.search(classes) or _NOISE_CLASS_RE.search(el_id):
            noise_nodes.append(el)

    for el in noise_nodes:
        try:
            el.decompose()
        except Exception:
            pass

    main = soup.find("main") or soup.find("article") or soup.body
    if not main:
        return ""

    text = main.get_text(separator="\n", strip=True)
    lines = [ln.strip() for ln in text.splitlines() if len(ln.strip()) > 2]
    return "\n".join(lines)


def chunk_text(text: str, page_url: str, max_chars: int = MAX_CHUNK_CHARS) -> list[TextChunk]:
    """Split long pages into overlapping chunks for LLM context limits."""
    if not text or len(text) < MIN_CHUNK_CHARS:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if len(p.strip()) >= MIN_CHUNK_CHARS]
    if not paragraphs:
        paragraphs = [text[:max_chars]]

    chunks: list[TextChunk] = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        if current_len + len(para) + 2 > max_chars and current:
            chunks.append(
                TextChunk(
                    text="\n\n".join(current),
                    source_url=page_url,
                    section_hint=f"block_{len(chunks) + 1}",
                )
            )
            current = [para]
            current_len = len(para)
        else:
            current.append(para)
            current_len += len(para) + 2

    if current:
        chunks.append(
            TextChunk(
                text="\n\n".join(current),
                source_url=page_url,
                section_hint=f"block_{len(chunks) + 1}",
            )
        )

    logger.debug("Chunked %s → %d section(s)", page_url, len(chunks))
    return chunks


def preprocess_page(html: str, page_url: str) -> list[TextChunk]:
    """Full pre-processing pipeline for one page."""
    try:
        text = clean_html(html, page_url)
        if not text:
            return []
        return chunk_text(text, page_url)
    except Exception as exc:
        logger.warning("Preprocess failed for %s: %s", page_url, exc)
        return []
