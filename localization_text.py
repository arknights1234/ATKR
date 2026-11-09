"""Normalize Arknights localization markup before translation or lookup."""

from __future__ import annotations

import re
import unicodedata


# Markup tags such as <@ba.vup> and </> hold no CJK text. Brackets around words, such as
# <休整期结束时>, are shown in the game as written, so they stay.
MARKUP_RE = re.compile(r"<[^<>\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]*>")
ANY_BRACKET_RE = re.compile(r"<[^>]*>")
SOURCE_NAME_RE = re.compile(r"【[^【】]+】")
TRANSLATED_NAME_RE = re.compile(r"【[^【】]+】|\[[^\[\]]+\]")
PLACEHOLDER_RE = re.compile(r"\{[^{}]*\}|%[sd]")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?%?")
EDGE_SYMBOLS = " \t\r\n.,:;!?…·•>＜<|-_~*"
OCR_NARROW_FORMS = str.maketrans("％（）", "%()")


def normalize_text(text: str) -> str:
    """Remove inline presentation tags such as <@ro.get> and </>."""
    return MARKUP_RE.sub("", text).strip()


def strip_all_brackets(text: str) -> str:
    """The form older versions used, which also dropped bracketed words such as <休整期结束时>."""
    return ANY_BRACKET_RE.sub("", text).strip()


def fix_bracketed_names(source: str, translated: str, exact) -> str:
    """Give each 【name】 its dictionary wording; DeepL keeps the brackets but guesses the name inside.

    DeepL keeps the brackets in order, so the Nth pair in the source is the Nth in the result.
    """
    names = SOURCE_NAME_RE.findall(source)
    slots = TRANSLATED_NAME_RE.findall(translated)
    if not names or len(names) != len(slots):
        return translated
    replacements = iter(names)
    return TRANSLATED_NAME_RE.sub(lambda match: exact(next(replacements)) or match.group(0), translated)


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
