"""Translate text or OCR JSON output with the DeepL API."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import select
import sys
import threading
from pathlib import Path

from net import https_context

# The app uses the newest zh→ko glossary on the account whose name starts with this.
GLOSSARY_NAME_PREFIX = "arknights_glossary"
GLOSSARY_LOOKUP_TIMEOUT = 8
GLOSSARY_UPLOAD_TIMEOUT = 60
TRANSLATE_TIMEOUT = 30

# DeepL closes an idle connection within about 30 seconds, so one kept here may already be gone.
_connections: dict[str, http.client.HTTPSConnection] = {}
_connections_lock = threading.Lock()


def _api_host(auth_key: str) -> str:
    return "api-free.deepl.com" if auth_key.endswith(":fx") else "api.deepl.com"


def _drop_connection(host: str) -> None:
    connection = _connections.pop(host, None)
    if connection is not None:
        connection.close()


def _new_connection(host: str, timeout: float) -> http.client.HTTPSConnection:
    _drop_connection(host)
    connection = http.client.HTTPSConnection(host, timeout=timeout, context=https_context())
    _connections[host] = connection
    return connection


def prepare_connection(auth_key: str) -> None:
    """Open the connection ahead of a request, sparing it the handshake. A failure surfaces in the request."""
    host = _api_host(auth_key)
    with _connections_lock:
        connection = _connections.get(host)
        if connection is not None and connection.sock is not None:
            # An idle socket only turns readable when the server has closed it.
            if not select.select([connection.sock], [], [], 0)[0]:
                return
        try:
            _new_connection(host, TRANSLATE_TIMEOUT).connect()
        except (OSError, http.client.HTTPException):
            _drop_connection(host)


def _exchange(connection: http.client.HTTPSConnection, method: str, path: str, body: bytes | None, headers: dict[str, str], timeout: float) -> tuple[int, bytes]:
    connection.timeout = timeout
    if connection.sock is not None:
        connection.sock.settimeout(timeout)
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    data = response.read()
    if response.will_close:
        connection.close()
    return response.status, data


def _request(auth_key: str, method: str, path: str, body: bytes | None, timeout: float) -> tuple[int, bytes]:
    """One API call on the kept connection, opening a new one if the server has closed it."""
    host = _api_host(auth_key)
    headers = {"Authorization": f"DeepL-Auth-Key {auth_key}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    with _connections_lock:
        kept = _connections.get(host)
        if kept is not None:
            try:
                return _exchange(kept, method, path, body, headers, timeout)
            except TimeoutError:
                _drop_connection(host)
                raise
            except (OSError, http.client.HTTPException):
                _drop_connection(host)
        try:
            return _exchange(_new_connection(host, timeout), method, path, body, headers, timeout)
        except (OSError, http.client.HTTPException):
            _drop_connection(host)
            raise


def translate(texts: list[str], auth_key: str, source_lang: str, target_lang: str, **options) -> list[dict]:
    """Send one batch to DeepL and return the per-text translation objects.

    ``options`` are passed through to the API, e.g. context, tag_handling, ignore_tags.
    """
    body = json.dumps({
        "text": texts,
        "source_lang": source_lang,
        "target_lang": target_lang,
        **options,
    }, ensure_ascii=False).encode("utf-8")
    try:
        status, data = _request(auth_key, "POST", "/v2/translate", body, TRANSLATE_TIMEOUT)
    except (OSError, http.client.HTTPException) as error:
        raise RuntimeError(f"DeepL API에 연결하지 못했습니다: {error}") from error
    if status != 200:
        raise RuntimeError(f"DeepL API 오류 (HTTP {status}): {data.decode('utf-8', errors='replace')}")

    translations = json.loads(data.decode("utf-8")).get("translations")
    if not isinstance(translations, list) or len(translations) != len(texts):
        raise RuntimeError("DeepL 응답의 번역 개수가 요청한 문장 수와 다릅니다.")
    return translations


def list_glossaries(auth_key: str, name_prefix: str, source_lang: str, target_lang: str) -> list[dict]:
    """This key's account glossaries for one language pair whose name starts with name_prefix."""
    try:
        status, data = _request(auth_key, "GET", "/v2/glossaries", None, GLOSSARY_LOOKUP_TIMEOUT)
    except http.client.HTTPException as error:
        raise OSError(str(error)) from error
    if status != 200:
        raise OSError(f"HTTP {status}")
    glossaries = json.loads(data.decode("utf-8")).get("glossaries", [])
    return [
        item for item in glossaries
        if str(item.get("name", "")).startswith(name_prefix)
        and str(item.get("source_lang", "")).upper() == source_lang and str(item.get("target_lang", "")).upper() == target_lang
    ]


def find_glossary_id(auth_key: str, name_prefix: str, source_lang: str, target_lang: str) -> str | None:
    """Newest ready glossary on this key's account, or None. Glossaries belong to one account."""
    usable = [item for item in list_glossaries(auth_key, name_prefix, source_lang, target_lang) if item.get("ready")]
    if not usable:
        return None
    return max(usable, key=lambda item: str(item.get("creation_time", "")))["glossary_id"]


def create_glossary(auth_key: str, name: str, source_lang: str, target_lang: str, entries: list[tuple[str, str]]) -> dict:
    """Upload a glossary to this key's account. The API limits its size (10 MiB), not its entry count."""
    body = json.dumps({
        "name": name,
        "source_lang": source_lang.lower(),
        "target_lang": target_lang.lower(),
        "entries": "\n".join(f"{source}\t{target}" for source, target in entries),
        "entries_format": "tsv",
    }, ensure_ascii=False).encode("utf-8")
    try:
        status, data = _request(auth_key, "POST", "/v2/glossaries", body, GLOSSARY_UPLOAD_TIMEOUT)
    except (OSError, http.client.HTTPException) as error:
        raise RuntimeError(f"DeepL API에 연결하지 못했습니다: {error}") from error
    if status not in (200, 201):
        raise RuntimeError(f"DeepL API 오류 (HTTP {status}): {data.decode('utf-8', errors='replace')}")
    return json.loads(data.decode("utf-8"))


def delete_glossary(auth_key: str, glossary_id: str) -> None:
    try:
        status, data = _request(auth_key, "DELETE", f"/v2/glossaries/{glossary_id}", None, GLOSSARY_LOOKUP_TIMEOUT)
    except (OSError, http.client.HTTPException) as error:
        raise RuntimeError(f"DeepL API에 연결하지 못했습니다: {error}") from error
    if status not in (200, 204):
        raise RuntimeError(f"DeepL API 오류 (HTTP {status}): {data.decode('utf-8', errors='replace')}")


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
