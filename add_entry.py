#!/usr/bin/env python3
"""inbox/ 에 넣은 파일로 새 회차를 등록하고 GitHub 에 push 한다.

사용법:  python3 add_entry.py
         (옵션) --time "2026-09-23 1330" --title "제목" --memo "메모" --yes --no-push
"""
import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    sys.exit("Pillow 가 필요합니다:  python3 -m pip install --user Pillow")

ROOT = Path(__file__).resolve().parent
INBOX = ROOT / "inbox"
ENTRIES_DIR = ROOT / "entries"
ENTRIES_JSON = ROOT / "entries.json"
LATEST = ROOT / "latest" / "index.html"
IMAGE_EXT = {".png", ".jpg", ".jpeg"}
THUMB_WIDTH = 800
NAV_MARK = "route-map-archive-nav"

NAV_TEMPLATE = """
<!-- {mark}: 아카이브 내비게이션 (add_entry.py 가 삽입) -->
<div id="{mark}" data-entry="{id}"></div>
<script>
(function(){{
  var host=document.getElementById('{mark}'); if(!host||!host.attachShadow) return;
  var id=host.dataset.entry, root=host.attachShadow({{mode:'open'}});
  root.innerHTML='<style>'+
    ':host{{all:initial}}'+
    'nav{{position:fixed;left:12px;top:12px;z-index:2147483000;display:flex;gap:4px;padding:4px;border-radius:9px;'+
    'background:rgba(247,249,250,.94);border:1px solid #C9D2DB;box-shadow:0 2px 10px rgba(0,0,0,.18);'+
    'font:500 13px/1 system-ui,-apple-system,"Apple SD Gothic Neo","Malgun Gothic",sans-serif}}'+
    'a,span{{display:inline-flex;align-items:center;min-height:32px;padding:0 10px;border-radius:6px;color:#16283D;text-decoration:none;white-space:nowrap}}'+
    'a:hover{{background:#E4E9EE}} a:focus-visible{{outline:2px solid #A8327E;outline-offset:1px}} span{{opacity:.35}}'+
    '@media (prefers-color-scheme:dark){{nav{{background:rgba(30,42,54,.94);border-color:#34485A}} a,span{{color:#E4EAF0}} a:hover{{background:#2A3947}}}}'+
    '</style><nav aria-label="회차 이동"><a href="../../">목록</a><span data-k="prev">이전 회차</span><span data-k="next">다음 회차</span></nav>';
  function link(k,e){{
    var old=root.querySelector('[data-k="'+k+'"]'); if(!e||!old) return;
    var a=document.createElement('a'); a.href='../'+encodeURIComponent(e.id)+'/'; a.textContent=old.textContent;
    a.title=(e.title||'')+' · '+(e.time_utc||''); old.replaceWith(a);
  }}
  fetch('../../entries.json',{{cache:'no-cache'}}).then(function(r){{return r.json();}}).then(function(d){{
    var list=(Array.isArray(d)?d:d.entries||[]).slice().sort(function(a,b){{return (a.time_utc||'').localeCompare(b.time_utc||'');}});
    var i=list.findIndex(function(e){{return e.id===id;}}); if(i<0) return;
    link('prev',list[i-1]); link('next',list[i+1]);
  }}).catch(function(){{}});
}})();
</script>
"""


def ask(prompt, default=None):
    suffix = f" [{default}]" if default else ""
    val = input(f"{prompt}{suffix}: ").strip()
    return val or (default or "")


def yes_no(prompt, assume_yes):
    if assume_yes:
        return True
    return input(f"{prompt} (y/N): ").strip().lower() in ("y", "yes", "ㅛ")


def parse_time(text):
    """'2026-09-23 1330', '2026-09-23T13:30Z', '20260923 1330 UTC' 등을 UTC datetime 으로."""
    m = re.fullmatch(r"\s*(\d{4})-?(\d{2})-?(\d{2})[\sT_]*(\d{2}):?(\d{2})\s*(?:Z|UTC)?\s*", text, re.I)
    if not m:
        raise ValueError(text)
    y, mo, d, h, mi = map(int, m.groups())
    return dt.datetime(y, mo, d, h, mi, tzinfo=dt.timezone.utc)


def load_entries():
    if ENTRIES_JSON.exists():
        return json.loads(ENTRIES_JSON.read_text(encoding="utf-8"))
    return []


def save_entries(entries):
    entries.sort(key=lambda e: e["time_utc"], reverse=True)
    ENTRIES_JSON.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_latest(entries):
    LATEST.parent.mkdir(exist_ok=True)
    if entries:
        target = f"../entries/{entries[0]['id']}/"
        body = f'<p><a href="{target}">최신 회차로 이동</a></p>'
        refresh = f'<meta http-equiv="refresh" content="0; url={target}">'
    else:
        target, refresh = "../", '<meta http-equiv="refresh" content="0; url=../">'
        body = '<p><a href="../">목록으로 이동</a></p>'
    LATEST.write_text(
        "<!DOCTYPE html>\n<html lang=\"ko\">\n<head>\n<meta charset=\"utf-8\">\n"
        f"<title>최신 회차</title>\n{refresh}\n<link rel=\"canonical\" href=\"{target}\">\n"
        f"</head>\n<body>\n{body}\n</body>\n</html>\n",
        encoding="utf-8",
    )


def inject_nav(html, entry_id):
    if NAV_MARK in html:
        return html
    nav = NAV_TEMPLATE.format(mark=NAV_MARK, id=entry_id)
    idx = html.lower().rfind("</body>")
    return html + nav if idx < 0 else html[:idx] + nav + html[idx:]


def pick_composite(images, assume_yes):
    named = [p for p in images if p.stem.lower().startswith(("composite", "합성"))]
    if named:
        return named[0]
    if len(images) == 1:
        return images[0]
    largest = max(images, key=lambda p: p.stat().st_size)
    if assume_yes:
        return largest
    print("합성 이미지로 쓸 파일을 고르세요:")
    for i, p in enumerate(images, 1):
        print(f"  {i}. {p.name}")
    choice = ask("번호", str(images.index(largest) + 1))
    return images[int(choice) - 1]


def git(*args):
    print("$ git", " ".join(args))
    subprocess.run(["git", *args], cwd=ROOT, check=True)


def main():
    ap = argparse.ArgumentParser(description="inbox/ 파일로 새 회차 등록")
    ap.add_argument("--time", help='기준시각 UTC, 예: "2026-09-23 1330"')
    ap.add_argument("--title")
    ap.add_argument("--memo")
    ap.add_argument("--yes", action="store_true", help="확인 질문에 모두 예")
    ap.add_argument("--no-push", action="store_true", help="git commit/push 생략")
    args = ap.parse_args()

    files = [p for p in INBOX.glob("*") if p.is_file() and not p.name.startswith(".")]
    htmls = sorted(p for p in files if p.suffix.lower() in (".html", ".htm"))
    images = sorted(p for p in files if p.suffix.lower() in IMAGE_EXT)
    if len(htmls) != 1:
        sys.exit(f"inbox 에 .html 파일이 정확히 1개 있어야 합니다 (현재 {len(htmls)}개).")
    if not images:
        sys.exit("inbox 에 .png/.jpg 이미지가 없습니다.")

    while True:
        raw = args.time or ask('기준시각 UTC (예: 2026-09-23 1330)')
        try:
            when = parse_time(raw)
            break
        except ValueError:
            print("  형식을 알아볼 수 없습니다. 예: 2026-09-23 1330")
            args.time = None
    entry_id = when.strftime("%Y-%m-%d_%H%MZ")
    title = args.title if args.title is not None else ask("제목")
    memo = args.memo if args.memo is not None else ask("메모 (없으면 Enter)")
    if not title:
        sys.exit("제목은 비워 둘 수 없습니다.")

    entries = load_entries()
    dest = ENTRIES_DIR / entry_id
    if dest.exists() or any(e["id"] == entry_id for e in entries):
        if not yes_no(f"회차 {entry_id} 가 이미 있습니다. 덮어쓸까요?", args.yes):
            sys.exit("취소했습니다.")
        shutil.rmtree(dest, ignore_errors=True)
        entries = [e for e in entries if e["id"] != entry_id]

    composite_src = pick_composite(images, args.yes)
    (dest / "sources").mkdir(parents=True)

    html = htmls[0].read_text(encoding="utf-8")
    (dest / "index.html").write_text(inject_nav(html, entry_id), encoding="utf-8")

    with Image.open(composite_src) as im:
        im.load()
        full = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB")
        full.save(dest / "composite.webp", "WEBP", quality=90, method=6)
        thumb = full.convert("RGB")
        if thumb.width > THUMB_WIDTH:
            thumb = thumb.resize((THUMB_WIDTH, round(thumb.height * THUMB_WIDTH / thumb.width)), Image.LANCZOS)
        thumb.save(dest / "thumb.jpg", "JPEG", quality=85, optimize=True, progressive=True)

    sources = []
    for p in images:
        if p == composite_src:
            continue
        shutil.copy2(p, dest / "sources" / p.name)
        sources.append(f"entries/{entry_id}/sources/{p.name}")

    base = f"entries/{entry_id}"
    entries.append({
        "id": entry_id,
        "title": title,
        "time_utc": when.strftime("%Y-%m-%dT%H:%MZ"),
        "registered": dt.date.today().isoformat(),
        "memo": memo,
        "map": f"{base}/",
        "composite": f"{base}/composite.webp",
        "thumb": f"{base}/thumb.jpg",
        "sources": sources,
    })
    save_entries(entries)
    write_latest(entries)
    print(f"등록 완료: {entry_id}  (원본 캡처 {len(sources)}개)")

    for p in files:
        p.unlink()
    print("inbox 를 비웠습니다.")

    if args.no_push:
        return
    git("add", "--", base, "entries.json", "latest")
    git("commit", "-m", f"add entry {entry_id}")
    git("push")


if __name__ == "__main__":
    main()
