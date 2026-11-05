"""Normalize Arknights localization markup before translation or lookup."""

from __future__ import annotations

import re
import unicodedata


MARKUP_RE = re.compile(r"<[^>]*>")
PLACEHOLDER_RE = re.compile(r"\{[^{}]*\}|%[sd]")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
EDGE_SYMBOLS = " \t\r\n.,:;!?…·•>＜<|-_~*"


def normalize_text(text: str) -> str:
    """Remove inline presentation tags such as <@ro.get> and </>."""
    return MARKUP_RE.sub("", text).strip()


def mask_numbers(text: str) -> str:
    """Replace numbers and format placeholders such as {0} with '#'."""
    return NUMBER_RE.sub("#", PLACEHOLDER_RE.sub("#", text))


def loose_key(text: str) -> str:
    """Key that survives OCR differences: spacing, full-width forms, edge symbols, numbers."""
    folded = unicodedata.normalize("NFKC", normalize_text(text))
    folded = "".join(folded.split())
    return mask_numbers(folded).strip(EDGE_SYMBOLS)


def fill_numbers(template: str, source_text: str) -> str | None:
    """Put the numbers read from the screen back into a masked translation."""
    numbers = NUMBER_RE.findall(unicodedata.normalize("NFKC", source_text))
    if template.count("#") != len(numbers):
        return None
    values = iter(numbers)
    return re.sub("#", lambda _: next(values), template)
