from __future__ import annotations

from pathlib import PurePath


IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".tiff"})
DOCUMENT_EXTENSIONS = frozenset({".pdf", ".docx", ".pages", ".eml", ".msg", ".csv", ".xlsx"})
SUPPORTED_EXTENSIONS = DOCUMENT_EXTENSIONS | IMAGE_EXTENSIONS
TEXT_EXTENSIONS = frozenset({".eml", ".csv"})
BINARY_EXTENSIONS = SUPPORTED_EXTENSIONS - TEXT_EXTENSIONS

IGNORED_DIRECTORY_NAMES = frozenset(
    {
        ".git", ".svn", ".hg", "venv", ".venv", "env", "site-packages",
        "__pycache__", "node_modules", "bower_components", "build", "dist",
        "target", "out", "bin", "obj", "library", "appdata", "tmp", ".cache",
    }
)

UNSUPPORTED_FILE_TYPE = "unsupported_file_type"
IGNORED_DIRECTORY = "ignored_directory"
IRRELEVANT_DOCUMENT = "irrelevant_document"
NO_ACTIONABLE_COMMITMENT = "no_actionable_commitment"
HISTORICAL_NOISE = "historical_noise"
DUPLICATE_COMMITMENT = "duplicate_commitment"


def is_supported_path(path: str | PurePath) -> bool:
    return PurePath(path).suffix.casefold() in SUPPORTED_EXTENSIONS


def is_hidden_name(name: str) -> bool:
    return name.startswith(".")


def is_ignored_directory(name: str) -> bool:
    return is_hidden_name(name) or name.casefold() in IGNORED_DIRECTORY_NAMES


def ignored_path_reason(path: str | PurePath) -> str | None:
    parts = PurePath(path).parts
    if any(is_ignored_directory(part) for part in parts[:-1]):
        return IGNORED_DIRECTORY
    if parts and is_hidden_name(parts[-1]):
        return IGNORED_DIRECTORY
    return None


def rejection(reason: str, path: str, detail: str = "") -> str:
    suffix = f": {detail}" if detail else ""
    return f"{reason}: {path}{suffix}"
