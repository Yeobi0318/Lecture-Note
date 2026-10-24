# -*- coding: utf-8 -*-
"""배포 zip 만들기 + GitHub Release 올리기 (개발자용).

  python tools/make_release.py v3.1.0 --notes RELEASE_NOTES.md [--repo Yeobi0318/Lecture-Note] [--zip-only]

- zip에는 git에 커밋된 파일만 넣고, 모든 파일을 쓰기 가능(0644)으로 저장합니다
  (예전에 terms.txt가 읽기 전용으로 배포돼 사용자가 편집하지 못한 문제 방지).
- GitHub 인증은 Git Credential Manager에 저장된 토큰을 씁니다(화면에 출력하지 않음).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FOLDER = "LectureNote"  # zip을 풀면 생기는 폴더 이름


def build_zip(tag: str) -> Path:
    files = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    names = [n for n in files.decode("utf-8").split("\0") if n and not n.startswith("tools/")]
    out = ROOT / "dist" / f"{FOLDER}-{tag}.zip"
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for n in names:
            data = (ROOT / n).read_bytes()
            info = zipfile.ZipInfo(f"{FOLDER}/{n}", date_time=(2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16  # 일반 파일, 쓰기 가능
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    print(f"zip: {out} ({out.stat().st_size // 1024}KB, 파일 {len(names)}개)")
    return out


def _token() -> str:
    out = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                         capture_output=True, text=True, check=True).stdout
    return dict(l.split("=", 1) for l in out.splitlines() if "=" in l)["password"]


def _api(method: str, url: str, token: str, data=None, ctype="application/json"):
    body = json.dumps(data).encode() if isinstance(data, dict) else data
    req = urllib.request.Request(url, data=body, method=method, headers={
        "Authorization": f"token {token}", "Accept": "application/vnd.github+json",
        "User-Agent": "lecturekit-release", "Content-Type": ctype})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def release(repo: str, tag: str, notes: str, asset: Path, title: str):
    tok = _token()
    api = f"https://api.github.com/repos/{repo}"
    try:
        rel = _api("GET", f"{api}/releases/tags/{tag}", tok)
        print(f"기존 릴리스 갱신: {rel['html_url']}")
        rel = _api("PATCH", f"{api}/releases/{rel['id']}", tok, {"name": title, "body": notes})
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        rel = _api("POST", f"{api}/releases", tok, {"tag_name": tag, "name": title, "body": notes,
                                                    "draft": False, "prerelease": False})
    for a in rel.get("assets", []):  # 같은 이름의 파일이 있으면 교체
        if a["name"] == asset.name:
            req = urllib.request.Request(a["url"], method="DELETE", headers={
                "Authorization": f"token {tok}", "User-Agent": "lecturekit-release"})
            urllib.request.urlopen(req).close()
    up = rel["upload_url"].split("{")[0] + "?name=" + urllib.parse.quote(asset.name)
    a = _api("POST", up, tok, asset.read_bytes(), ctype="application/zip")
    print(f"릴리스: {rel['html_url']}\n파일: {a['browser_download_url']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tag")
    ap.add_argument("--notes", required=True, help="릴리스 설명 마크다운 파일")
    ap.add_argument("--title")
    ap.add_argument("--repo", default="Yeobi0318/Lecture-Note")
    ap.add_argument("--zip-only", action="store_true")
    a = ap.parse_args()
    z = build_zip(a.tag)
    if not a.zip_only:
        notes = Path(a.notes).read_text(encoding="utf-8")
        release(a.repo, a.tag, notes, z, a.title or f"Lecture Note {a.tag}")


if __name__ == "__main__":
    sys.exit(main())
