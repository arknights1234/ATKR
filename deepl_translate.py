"""Translate text or PaddleOCR JSON output with the DeepL API."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from net import https_context

GLOSSARY_LOOKUP_TIMEOUT = 8


def translate(texts: list[str], auth_key: str, source_lang: str, target_lang: str, **options) -> list[dict]:
    """Send one batch to DeepL and return the per-text translation objects.

    ``options`` are passed through to the API, e.g. context, tag_handling, ignore_tags.
    """
    api_host = "api-free.deepl.com" if auth_key.endswith(":fx") else "api.deepl.com"
    url = f"https://{api_host}/v2/translate"
    body = json.dumps({
        "text": texts,
        "source_lang": source_lang,
        "target_lang": target_lang,
        **options,
    }, ensure_ascii=False).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={
            "Authorization": f"DeepL-Auth-Key {auth_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=30, context=https_context()) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"DeepL API 오류 (HTTP {error.code}): {details}") from error
    except URLError as error:
        raise RuntimeError(f"DeepL API에 연결하지 못했습니다: {error.reason}") from error

    translations = result.get("translations")
    if not isinstance(translations, list) or len(translations) != len(texts):
        raise RuntimeError("DeepL 응답의 번역 개수가 요청한 문장 수와 다릅니다.")
    return translations


def find_glossary_id(auth_key: str, name_prefix: str, source_lang: str, target_lang: str) -> str | None:
    """Newest ready glossary on this key's account, or None. Glossaries belong to one account."""
    api_host = "api-free.deepl.com" if auth_key.endswith(":fx") else "api.deepl.com"
    request = Request(f"https://{api_host}/v2/glossaries", headers={"Authorization": f"DeepL-Auth-Key {auth_key}"})
    with urlopen(request, timeout=GLOSSARY_LOOKUP_TIMEOUT, context=https_context()) as response:
        glossaries = json.loads(response.read().decode("utf-8")).get("glossaries", [])
    usable = [
        item for item in glossaries
        if str(item.get("name", "")).startswith(name_prefix) and item.get("ready")
        and str(item.get("source_lang", "")).upper() == source_lang and str(item.get("target_lang", "")).upper() == target_lang
    ]
    if not usable:
        return None
    return max(usable, key=lambda item: str(item.get("creation_time", "")))["glossary_id"]


def read_ocr_json(path: Path) -> tuple[list[str], list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    detections = data.get("detections", [])
    items = [item for item in detections if str(item.get("text", "")).strip()]
    texts = [str(item["text"]).strip() for item in items]
    if not texts:
        raise ValueError(f"OCR JSON에 번역할 텍스트가 없습니다: {path}")
    return texts, items


def main() -> int:
    parser = argparse.ArgumentParser(description="DeepL API 중국어 → 한국어 번역 테스트")
    parser.add_argument("text", nargs="*", help="번역할 중국어 문장 (공백이 있는 문장은 따옴표로 감싸세요)")
    parser.add_argument("--ocr-json", type=Path, help="OCR 결과 JSON을 읽어 번역하고 결합 결과 JSON 저장")
    parser.add_argument("--source", default="ZH", help="원문 언어 코드 (기본: ZH)")
    parser.add_argument("--target", default="KO", help="번역 언어 코드 (기본: KO)")
    args = parser.parse_args()

    auth_key = os.environ.get("DEEPL_AUTH_KEY", "").strip()
    if not auth_key:
        print("DEEPL_AUTH_KEY 환경 변수가 없습니다. 키를 채팅이나 코드에 넣지 말고 환경 변수로 설정하세요.", file=sys.stderr)
        return 2

    detections: list[dict] = []
    if args.ocr_json:
        try:
            texts, detections = read_ocr_json(args.ocr_json)
        except (OSError, json.JSONDecodeError, ValueError) as error:
            print(f"OCR 결과 읽기 실패: {error}", file=sys.stderr)
            return 2
    else:
        text = " ".join(args.text).strip() or input("번역할 중국어 문장: ").strip()
        if not text:
            print("번역할 텍스트가 비어 있습니다.", file=sys.stderr)
            return 2
        texts = [text]

    try:
        translations = translate(texts, auth_key, args.source.upper(), args.target.upper())
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1

    for original, translated in zip(texts, translations):
        detected = translated.get("detected_source_language", args.source.upper())
        print(f"{original}\n→ {translated.get('text', '')}  [감지 언어: {detected}]")

    if args.ocr_json:
        for item, translated in zip(detections, translations):
            item["translation"] = translated.get("text", "")
            item["detected_source_language"] = translated.get("detected_source_language")
        output_path = args.ocr_json.with_name(f"{args.ocr_json.stem}.translated.json")
        output_path.write_text(
            json.dumps({"source": str(args.ocr_json), "detections": detections}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"번역 결과 저장: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
