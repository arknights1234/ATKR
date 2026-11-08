"""Download and activate the runtime dictionary committed to the public GitHub repository."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app_paths import active_runtime_dictionary_path, install_dir, runtime_dictionary_pointer
from net import https_context


PUBLIC_DICTIONARY_REPO = "arknights1234/ATKR"
DICTIONARY_BRANCH = "main"
DICTIONARY_REPO_PATH = "data/runtime_dictionary.sqlite3"
USER_AGENT = "ArknightsScreenTranslator"


def _get_json(url: str) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        with urlopen(Request(url, headers=headers), timeout=25, context=https_context()) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        if error.code == 404:
            raise RuntimeError("공개 저장소에서 사전 파일을 찾지 못했습니다.") from error
        raise RuntimeError(f"GitHub API 오류 (HTTP {error.code})") from error
    except URLError as error:
        raise RuntimeError(f"GitHub에 연결할 수 없습니다: {error.reason}") from error


def latest_dictionary() -> dict[str, str]:
    """Describe the newest committed dictionary: version label, blob hash and download URL."""
    base = f"https://api.github.com/repos/{PUBLIC_DICTIONARY_REPO}"
    commits = _get_json(f"{base}/commits?sha={DICTIONARY_BRANCH}&path={DICTIONARY_REPO_PATH}&per_page=1")
    if not isinstance(commits, list) or not commits:
        raise RuntimeError("저장소에 사전 파일 커밋이 없습니다.")
    commit = str(commits[0]["sha"])
    date = str(commits[0]["commit"]["committer"]["date"])[:10].replace("-", "")
    contents = _get_json(f"{base}/contents/{DICTIONARY_REPO_PATH}?ref={commit}")
    blob_sha = contents.get("sha") if isinstance(contents, dict) else None
    if not blob_sha:
        raise RuntimeError("GitHub 응답에서 사전 파일 정보를 읽지 못했습니다.")
    return {
        "tag": f"{date}-{commit[:7]}",
        "blob_sha": str(blob_sha).lower(),
        "url": f"https://raw.githubusercontent.com/{PUBLIC_DICTIONARY_REPO}/{commit}/{DICTIONARY_REPO_PATH}",
    }


def latest_release() -> dict[str, str]:
    """Newest published release: version without the leading v, tag and page URL."""
    release = _get_json(f"https://api.github.com/repos/{PUBLIC_DICTIONARY_REPO}/releases/latest")
    tag = str(release.get("tag_name", "")) if isinstance(release, dict) else ""
    if not tag:
        raise RuntimeError("GitHub 응답에서 릴리스 정보를 읽지 못했습니다.")
    return {"version": tag.lstrip("vV"), "tag": tag, "url": str(release.get("html_url") or f"https://github.com/{PUBLIC_DICTIONARY_REPO}/releases/latest")}


def version_newer(candidate: str, current: str) -> bool:
    """True when candidate (e.g. 1.3.1) is a later version than current; unreadable parts count as 0."""
    def parts(version: str) -> list[int]:
        return [int(piece) if piece.isdigit() else 0 for piece in re.split(r"[.\-+]", version.strip())[:3]]
    a, b = parts(candidate), parts(current)
    width = max(len(a), len(b))
    return a + [0] * (width - len(a)) > b + [0] * (width - len(b))


def dictionary_update_available() -> tuple[str, bool]:
    """(newest tag, whether it differs from the dictionary in use), without downloading it."""
    latest = latest_dictionary()
    active = active_runtime_dictionary_path()
    return latest["tag"], not (active.is_file() and git_blob_sha(active) == latest["blob_sha"])


def git_blob_sha(path: Path) -> str:
    """Hash a file the way Git does, which is what GitHub reports for it."""
    digest = hashlib.sha1(f"blob {path.stat().st_size}\0".encode())
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def validate_dictionary(path: Path) -> tuple[int, int]:
    connection = None
    try:
        connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if integrity != "ok" or not {"lookup", "terms", "metadata"}.issubset(tables):
            raise ValueError("사전 파일이 올바른 SQLite 사전 형식이 아닙니다.")
        lookup_count = connection.execute("SELECT COUNT(*) FROM lookup").fetchone()[0]
        term_count = connection.execute("SELECT COUNT(*) FROM terms").fetchone()[0]
        return int(lookup_count), int(term_count)
    except sqlite3.Error as error:
        raise ValueError(f"사전 SQLite 검증 실패: {error}") from error
    finally:
        if connection is not None:
            connection.close()


def _activate(target: Path) -> None:
    """Point the app at the new file, then drop older updates. A running overlay may hold one open."""
    runtime_dictionary_pointer().write_text(target.name, encoding="utf-8")
    for old in target.parent.glob("runtime_dictionary_*.sqlite3"):
        if old != target:
            try:
                old.unlink()
            except OSError:
                pass


def update_dictionary() -> tuple[str, bool, int, int]:
    latest = latest_dictionary()
    tag, blob_sha = latest["tag"], latest["blob_sha"]
    active = active_runtime_dictionary_path()
    if active.is_file() and git_blob_sha(active) == blob_sha:
        return tag, False, *validate_dictionary(active)

    safe_tag = re.sub(r"[^A-Za-z0-9._-]+", "_", tag).strip("._") or "latest"
    data_dir = install_dir() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / f"runtime_dictionary_{safe_tag}.sqlite3"
    temp_path = data_dir / f".runtime_dictionary_{safe_tag}.download"
    try:
        request = Request(latest["url"], headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=90, context=https_context()) as response, temp_path.open("wb") as output:
            while block := response.read(1024 * 1024):
                output.write(block)
        if git_blob_sha(temp_path) != blob_sha:
            raise RuntimeError("GitHub에 등록된 해시와 받은 사전 파일이 일치하지 않습니다.")
        lookup_count, term_count = validate_dictionary(temp_path)
        temp_path.replace(target)
    except (HTTPError, URLError, OSError) as error:
        raise RuntimeError(f"사전 파일 다운로드 실패: {error}") from error
    finally:
        temp_path.unlink(missing_ok=True)
    _activate(target)
    return tag, True, lookup_count, term_count
