"""Normalize Arknights localization markup before translation or lookup."""

from __future__ import annotations

import re
import unicodedata


MARKUP_RE = re.compile(r"<[^>]*>")
PLACEHOLDER_RE = re.compile(r"\{[^{}]*\}|%[sd]")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?%?")
EDGE_SYMBOLS = " \t\r\n.,:;!?…·•>＜<|-_~*"
OCR_NARROW_FORMS = str.maketrans("％（）", "%()")


def normalize_text(text: str) -> str:
    """Remove inline presentation tags such as <@ro.get> and </>."""
    return MARKUP_RE.sub("", text).strip()


def mask_numbers(text: str) -> str:
    """Replace numbers and format placeholders such as {0} with '#'."""
    return NUMBER_RE.sub("#", PLACEHOLDER_RE.sub("#", text))


def fold_text(text: str) -> str:
    """Compare form: no markup, line breaks or spacing, and full-width characters narrowed."""
    folded = unicodedata.normalize("NFKC", normalize_text(text)).replace("\\n", "")
    return "".join(folded.split())


def loose_key(text: str) -> str:
    """Key that survives OCR differences: spacing, full-width forms, edge symbols, numbers."""
    return mask_numbers(fold_text(text)).strip(EDGE_SYMBOLS)


def fill_numbers(template: str, source_text: str) -> str | None:
    """Put the numbers read from the screen back into a masked translation."""
    numbers = NUMBER_RE.findall(unicodedata.normalize("NFKC", source_text))
    if template.count("#") != len(numbers):
        return None
    values = iter(numbers)
    return re.sub("#", lambda _: next(values), template)
