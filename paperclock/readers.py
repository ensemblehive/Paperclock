from __future__ import annotations

import base64
import html
import io
import json
import re
import zipfile
from email import policy
from email.parser import BytesParser
from pathlib import Path
from xml.etree import ElementTree


TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".html", ".htm", ".xml",
    ".eml", ".ics", ".rtf", ".log",
}
DOCUMENT_EXTENSIONS = {".pdf", ".docx"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | DOCUMENT_EXTENSIONS
MAX_FILE_BYTES = 8 * 1024 * 1024


class UnreadableFile(ValueError):
    pass


def decode_upload(item: dict[str, object]) -> str:
    path = str(item.get("path", "untitled"))
    raw_content = item.get("content", "")
    encoding = item.get("encoding", "text")
    if encoding == "base64":
        try:
            raw = base64.b64decode(str(raw_content), validate=True)
        except (ValueError, TypeError) as exc:
            raise UnreadableFile(f"{path}: invalid base64 data") from exc
    else:
        raw = str(raw_content).encode("utf-8")
    return extract_text(path, raw)


def extract_text(path: str, raw: bytes) -> str:
    if len(raw) > MAX_FILE_BYTES:
        raise UnreadableFile(f"{path}: larger than 8 MB")

    suffix = Path(path).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise UnreadableFile(f"{path}: unsupported file type")
    if suffix == ".pdf":
        return _read_pdf(path, raw)
    if suffix == ".docx":
        return _read_docx(path, raw)
    if suffix == ".eml":
        return _read_email(path, raw)

    text = _decode_text(path, raw)
    if suffix in {".html", ".htm", ".xml"}:
        text = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", text, flags=re.I | re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        text = html.unescape(text)
    elif suffix == ".rtf":
        text = re.sub(r"\\[a-z]+-?\d* ?|[{}]", " ", text, flags=re.I)
    return _clean(text)


def _decode_text(path: str, raw: bytes) -> str:
    if b"\x00" in raw[:2048]:
        raise UnreadableFile(f"{path}: appears to be binary")
    for codec in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return raw.decode(codec)
        except UnicodeDecodeError:
            continue
    raise UnreadableFile(f"{path}: unknown text encoding")


def _read_pdf(path: str, raw: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - packaging guard
        raise UnreadableFile(f"{path}: PDF support requires pypdf") from exc
    try:
        reader = PdfReader(io.BytesIO(raw))
        return _clean("\n".join(page.extract_text() or "" for page in reader.pages))
    except Exception as exc:
        raise UnreadableFile(f"{path}: could not read PDF") from exc


def _read_docx(path: str, raw: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(xml)
        paragraphs: list[str] = []
        for paragraph in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
            parts = [node.text or "" for node in paragraph.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")]
            if parts:
                paragraphs.append("".join(parts))
        return _clean("\n".join(paragraphs))
    except (KeyError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise UnreadableFile(f"{path}: could not read DOCX") from exc


def _read_email(path: str, raw: bytes) -> str:
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
        chunks = [f"Subject: {message.get('subject', '')}"]
        for part in message.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                chunks.append(part.get_content())
        return _clean("\n".join(chunks))
    except Exception as exc:
        raise UnreadableFile(f"{path}: could not read email") from exc


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def payload_size(payload: dict[str, object]) -> int:
    return len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
