import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml
from markdown_it import MarkdownIt
from markdown_it.token import Token


IMAGE_TYPES = {
    ".avif": "image/avif",
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
METADATA_KEYS = {"title", "slug", "excerpt", "categories", "tags", "cover"}
MARKDOWN = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])


@dataclass
class Document:
    metadata: dict
    tokens: list[Token]
    images: list[tuple[Token, Path]]
    cover: Path | None

    @property
    def files(self) -> list[Path]:
        paths = [path for _, path in self.images]
        if self.cover is not None:
            paths.append(self.cover)
        return list(dict.fromkeys(paths))

    def render(self, image_urls: dict[Path, str] | None = None) -> str:
        if image_urls is None:
            return MARKDOWN.renderer.render(self.tokens, MARKDOWN.options, {})
        original = [token.attrGet("src") for token, _ in self.images]
        try:
            for token, path in self.images:
                token.attrSet("src", image_urls[path])
            return self.render()
        finally:
            for (token, _), source in zip(self.images, original):
                token.attrSet("src", source)


def read_document(source: Path) -> Document:
    source = source.resolve()
    metadata, body = split_metadata(source.read_text(encoding="utf-8-sig"))
    tokens = MARKDOWN.parse(body)
    # Use an initial H1 for the WordPress title, and avoid repeating it in the body.
    if tokens and tokens[0].type == "heading_open" and tokens[0].tag == "h1":
        heading = "".join(token.content for token in tokens[1].children or [])
        if heading and ("title" not in metadata or metadata["title"] == heading):
            metadata.setdefault("title", heading)
            tokens = tokens[3:]
    if not metadata.get("title", "").strip() or not tokens:
        raise ValueError("Provide an article title and Markdown body.")
    images = []
    for token in walk_tokens(tokens):
        if token.type != "image":
            continue
        reference = token.attrGet("src") or ""
        url = urlsplit(reference)
        if url.scheme in {"https", "http"} and url.netloc:
            continue
        images.append((token, image_path(reference, source.parent)))
    cover = image_path(metadata["cover"], source.parent) if "cover" in metadata else None
    return Document(metadata, tokens, images, cover)


def split_metadata(markdown: str) -> tuple[dict, str]:
    lines = markdown.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, markdown
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise ValueError("YAML front matter must end with a line containing ---.")
    try:
        metadata = yaml.safe_load("".join(lines[1:end]))
    except yaml.YAMLError as error:
        raise ValueError("Invalid YAML front matter.") from error
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict) or metadata.keys() - METADATA_KEYS:
        raise ValueError("Use only title, slug, excerpt, categories, tags, and cover in front matter.")
    for field, value in metadata.items():
        if field in {"categories", "tags"}:
            if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
                raise ValueError(f"{field} must be a list of existing WordPress term slugs.")
            metadata[field] = list(dict.fromkeys(item.strip() for item in value))
        elif not isinstance(value, str) or (field != "excerpt" and not value.strip()):
            raise ValueError(f"{field} must be text.")
        else:
            metadata[field] = value.strip()
    if "slug" in metadata and not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", metadata["slug"]):
        raise ValueError("Use lowercase ASCII letters, digits, and hyphens for slug.")
    return metadata, "".join(lines[end + 1 :])


def walk_tokens(tokens):
    for token in tokens:
        yield token
        if token.children:
            yield from walk_tokens(token.children)


def image_path(reference: str, directory: Path) -> Path:
    url = urlsplit(reference)
    relative = unquote(url.path)
    if (
        url.scheme or url.netloc or url.query or url.fragment
        or not relative or ":" in relative or relative.startswith(("/", "\\"))
    ):
        raise ValueError(f"Use a relative image path inside the article folder: {reference}")
    path = (directory / relative).resolve()
    if not path.is_relative_to(directory) or path.suffix.lower() not in IMAGE_TYPES:
        raise ValueError(f"Use a supported image inside the article folder: {reference}")
    if not path.is_file():
        raise ValueError(f"Image file not found: {reference}")
    if not path.stat().st_size:
        raise ValueError(f"Image file is empty: {reference}")
    return path
