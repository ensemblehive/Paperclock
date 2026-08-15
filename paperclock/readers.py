from __future__ import annotations

import base64
import io
import json
import re
import zipfile
from email import policy
from email.parser import BytesParser
from pathlib import Path
import threading
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from .policy import BINARY_EXTENSIONS, IMAGE_EXTENSIONS, SUPPORTED_EXTENSIONS

DOCUMENT_EXTENSIONS = BINARY_EXTENSIONS
MAX_FILE_BYTES = 8 * 1024 * 1024

_OCR_ENGINE: object | None = None
_OCR_LOCK = threading.Lock()


def _get_ocr_engine():
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        with _OCR_LOCK:
            if _OCR_ENGINE is None:
                from rapidocr_onnxruntime import RapidOCR
                _OCR_ENGINE = RapidOCR()
    return _OCR_ENGINE


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
    if suffix in IMAGE_EXTENSIONS:
        return _read_image(path, raw)
    if suffix == ".pdf":
        return _read_pdf(path, raw)
    if suffix == ".docx":
        return _read_docx(path, raw)
    if suffix == ".pages":
        return _read_pages(path, raw)
    if suffix == ".eml":
        return _read_email(path, raw)
    if suffix == ".msg":
        return _read_msg(path, raw)
    if suffix == ".csv":
        return _read_csv(path, raw)
    if suffix == ".xlsx":
        return _read_xlsx(path, raw)
    raise UnreadableFile(f"{path}: unsupported file type")


def _read_csv(path: str, raw: bytes) -> str:
    text = _decode_text(path, raw)
    return _clean(text)


def _read_xlsx(path: str, raw: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            # 1. Read shared strings if present
            shared_strings: list[str] = []
            if "xl/sharedStrings.xml" in archive.namelist():
                sst_xml = _safe_read_zip_entry(archive, "xl/sharedStrings.xml")
                sst_root = ElementTree.fromstring(sst_xml)
                for si in sst_root.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}si"):
                    t_nodes = [t.text or "" for t in si.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")]
                    shared_strings.append("".join(t_nodes))

            # 2. Read sheet1
            sheet_name = "xl/worksheets/sheet1.xml"
            if sheet_name not in archive.namelist():
                # find first sheet
                sheets = [n for n in archive.namelist() if n.startswith("xl/worksheets/sheet")]
                if not sheets:
                    raise UnreadableFile(f"{path}: no worksheets found")
                sheet_name = sorted(sheets)[0]

            sheet_xml = _safe_read_zip_entry(archive, sheet_name)
            sheet_root = ElementTree.fromstring(sheet_xml)
            lines: list[str] = []
            ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
            for row in sheet_root.iter(f"{ns}row"):
                row_cells: list[str] = []
                for c in row.iter(f"{ns}c"):
                    reference = c.get("r", "")
                    column_match = re.match(r"([A-Z]+)", reference)
                    if column_match:
                        column_index = _xlsx_column_index(column_match.group(1))
                        while len(row_cells) < column_index:
                            row_cells.append("")
                    t_attr = c.get("t")
                    v_node = c.find(f"{ns}v")
                    val = v_node.text if v_node is not None and v_node.text else ""
                    if t_attr == "s" and val.isdigit():
                        idx = int(val)
                        if 0 <= idx < len(shared_strings):
                            val = shared_strings[idx]
                    elif t_attr == "inlineStr":
                        t_node = c.find(f"{ns}is/{ns}t")
                        if t_node is not None and t_node.text:
                            val = t_node.text
                    row_cells.append(val.strip())
                if any(row_cells):
                    lines.append(" | ".join(row_cells))
            return _clean("\n".join(lines))
    except (zipfile.BadZipFile, ElementTree.ParseError, DefusedXmlException) as exc:
        raise UnreadableFile(f"{path}: could not read Excel workbook") from exc


def _decode_text(path: str, raw: bytes) -> str:
    if b"\x00" in raw[:2048]:
        raise UnreadableFile(f"{path}: appears to be binary")
    for codec in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return raw.decode(codec)
        except UnicodeDecodeError:
            continue
    raise UnreadableFile(f"{path}: unknown text encoding")


def _read_image(path: str, raw: bytes) -> str:
    try:
        from PIL import Image
    except ImportError as exc:
        raise UnreadableFile(f"{path}: image support requires Pillow") from exc
    try:
        img = Image.open(io.BytesIO(raw))
        ocr = _get_ocr_engine()
        result, _ = ocr(img)
        if not result:
            return ""
        lines = [item[1] for item in result if item and len(item) > 1 and item[1]]
        return _clean("\n".join(lines))
    except Exception as exc:
        raise UnreadableFile(f"{path}: could not read image") from exc


def _read_pdf(path: str, raw: bytes) -> str:
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - packaging guard
        raise UnreadableFile(f"{path}: PDF support requires pdfplumber") from exc
    try:
        with pdfplumber.open(io.BytesIO(raw)) as document:
            page_text = []
            for page in document.pages:
                text = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
                if len(text.strip()) < 40:
                    try:
                        img = page.to_image(resolution=150).original
                        ocr = _get_ocr_engine()
                        result, _ = ocr(img)
                        if result:
                            ocr_lines = [item[1] for item in result if item and len(item) > 1 and item[1]]
                            text = "\n".join(ocr_lines)
                    except Exception:
                        pass
                page_text.append(text)

            first_pages = "\n".join(page_text[:2]).casefold()
            table_candidate = any(marker in first_pages for marker in (
                "account statement", "bank statement", "statement of account", "opening balance", "closing balance",
            ))
            rendered: list[str] = []
            for page_number, (page, text) in enumerate(zip(document.pages, page_text), start=1):
                rendered.append(f"[[PAPERCLOCK_PAGE:{page_number}]]\n{text}")
                if table_candidate:
                    tables = page.extract_tables() or []
                    if not tables:
                        tables = page.extract_tables({
                            "vertical_strategy": "text",
                            "horizontal_strategy": "text",
                            "min_words_vertical": 2,
                            "min_words_horizontal": 1,
                            "intersection_tolerance": 5,
                        }) or []
                    for table in tables:
                        for row in table:
                            cells = [re.sub(r"\s+", " ", cell or "").strip() for cell in row]
                            if sum(bool(cell) for cell in cells) >= 2:
                                rendered.append(" | ".join(cells))
            return _clean("\n".join(rendered))
    except Exception as exc:
        raise UnreadableFile(f"{path}: could not read PDF") from exc


def _xlsx_column_index(letters: str) -> int:
    index = 0
    for letter in letters:
        index = index * 26 + ord(letter) - ord("A") + 1
    return index - 1


MAX_UNCOMPRESSED_ENTRY_BYTES = 32 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200


def _safe_read_zip_entry(archive: zipfile.ZipFile, name: str, max_bytes: int = MAX_UNCOMPRESSED_ENTRY_BYTES) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError:
        raise
    if info.file_size > max_bytes:
        raise UnreadableFile(f"{name}: uncompressed entry exceeds {max_bytes // (1024 * 1024)} MB limit")
    if info.file_size > 1024 * 1024 and info.file_size > max(info.compress_size, 1) * MAX_COMPRESSION_RATIO:
        raise UnreadableFile(f"{name}: suspicious compression ratio")
    return archive.read(name)


def _read_docx(path: str, raw: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            xml = _safe_read_zip_entry(archive, "word/document.xml")
        root = ElementTree.fromstring(xml)
        paragraphs: list[str] = []
        for paragraph in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
            parts = [node.text or "" for node in paragraph.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")]
            if parts:
                paragraphs.append("".join(parts))
        return _clean("\n".join(paragraphs))
    except (KeyError, zipfile.BadZipFile, ElementTree.ParseError, DefusedXmlException) as exc:
        raise UnreadableFile(f"{path}: could not read DOCX") from exc


def _read_pages(path: str, raw: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = set(archive.namelist())
            if "QuickLook/Preview.pdf" in names:
                return _read_pdf(path, _safe_read_zip_entry(archive, "QuickLook/Preview.pdf"))
            if "index.xml" in names:
                root = ElementTree.fromstring(_safe_read_zip_entry(archive, "index.xml"))
                return _clean(" ".join(node.text or "" for node in root.iter()))
    except (zipfile.BadZipFile, ElementTree.ParseError, DefusedXmlException) as exc:
        raise UnreadableFile(f"{path}: could not read Pages document") from exc
    raise UnreadableFile(f"{path}: Pages document has no readable preview")


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


def _read_msg(path: str, raw: bytes) -> str:
    try:
        import extract_msg
    except ImportError as exc:  # pragma: no cover - packaging guard
        raise UnreadableFile(f"{path}: MSG support requires extract-msg") from exc
    try:
        message = extract_msg.openMsg(io.BytesIO(raw))
        try:
            return _clean(f"Subject: {message.subject or ''}\n{message.body or ''}")
        finally:
            message.close()
    except Exception as exc:
        raise UnreadableFile(f"{path}: could not read Outlook message") from exc


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def payload_size(payload: dict[str, object]) -> int:
    return len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
