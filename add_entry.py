#!/usr/bin/env python3
"""inbox/ 에 넣은 파일로 새 회차를 등록하고 GitHub 에 push 한다.

사용법:  python3 add_entry.py
         (옵션) --time "2026-09-23 1330" --title "제목" --memo "메모" --yes --no-push
"""
import argparse
import base64
import datetime as dt
import io
import json
import re
import shutil
import subprocess
import sys
import time
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
    var a=document.createElement('a'); a.href='../'+encodeURIComponent(e.id)+'/'+(e.version?'?v='+e.version:''); a.textContent=old.textContent;
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
        v = entries[0].get("version")
        target = f"../entries/{entries[0]['id']}/" + (f"?v={v}" if v else "")
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


LAYER_TAGS = {"track": "항적", "sigmet": "SIGMET", "radar": "레이더", "jet": "제트기류", "aurora": "오로라"}


def layer_tags(html):
    """지도 HTML 의 레이어 목록(label for="t-...")에서 태그를 만든다."""
    ids = re.findall(r'<label for="t-([\w-]+)"', html)
    return [LAYER_TAGS[i] for i in ids if i in LAYER_TAGS]


def inject_nav(html, entry_id):
    if NAV_MARK in html:
        return html
    nav = NAV_TEMPLATE.format(mark=NAV_MARK, id=entry_id)
    idx = html.lower().rfind("</body>")
    return html + nav if idx < 0 else html[:idx] + nav + html[idx:]


def composite_from_html(html):
    """지도 HTML 에 data URI 로 들어 있는 레이어 이미지를 순서대로 겹쳐 한 장으로 만든다."""
    layers = []
    for mime, b64 in re.findall(r'<img\b[^>]*?\bsrc="data:(image/[\w+.-]+);base64,([A-Za-z0-9+/=\s]+)"', html):
        try:
            im = Image.open(io.BytesIO(base64.b64decode(b64)))
            im.load()
        except Exception:
            continue
        layers.append(im.convert("RGBA"))
    if not layers:
        return None
    size = layers[0].size
    layers = [im for im in layers if im.size == size]
    m = re.search(r"--map-bg\s*:\s*(#[0-9A-Fa-f]{6})", html)
    bg = tuple(int(m.group(1)[i:i + 2], 16) for i in (1, 3, 5)) if m else (255, 255, 255)
    out = Image.new("RGBA", size, bg + (255,))
    for im in layers:
        out = Image.alpha_composite(out, im)
    print(f"지도 HTML 의 레이어 {len(layers)}장을 겹쳐 합성 이미지를 만들었습니다.")
    return out.convert("RGB")


def pick_composite(images, assume_yes, can_generate=False):
    """합성 이미지로 쓸 파일을 고른다. None 이면 HTML 레이어로 자동 생성."""
    named = [p for p in images if p.stem.lower().startswith(("composite", "합성"))]
    if named:
        return named[0]
    if can_generate:
        return None
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
    ap.add_argument("--tags", help='추가 태그(쉼표 구분), 예: "OZ222,ICN→JFK"')
    ap.add_argument("--yes", action="store_true", help="확인 질문에 모두 예")
    ap.add_argument("--no-push", action="store_true", help="git commit/push 생략")
    args = ap.parse_args()

    files = [p for p in INBOX.glob("*") if p.is_file() and not p.name.startswith(".")]
    htmls = sorted(p for p in files if p.suffix.lower() in (".html", ".htm"))
    images = sorted(p for p in files if p.suffix.lower() in IMAGE_EXT)
    if len(htmls) != 1:
        sys.exit(f"inbox 에 .html 파일이 정확히 1개 있어야 합니다 (현재 {len(htmls)}개).")
    html = htmls[0].read_text(encoding="utf-8")
    generated = None if any(p.stem.lower().startswith(("composite", "합성")) for p in images) \
        else composite_from_html(html)
    if not images and generated is None:
        sys.exit("inbox 에 이미지가 없고, 지도 HTML 에서도 합성할 레이어를 찾지 못했습니다.")

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
    prev = next((e for e in entries if e["id"] == entry_id), {})
    if args.tags is None:
        old = [t for t in prev.get("tags", []) if t not in LAYER_TAGS.values()]
        args.tags = ask("태그 (쉼표 구분, 예: 편명·구간, 없으면 Enter)", ",".join(old))
    extra = [t.strip() for t in args.tags.split(",") if t.strip()]
    dest = ENTRIES_DIR / entry_id
    if dest.exists() or any(e["id"] == entry_id for e in entries):
        if not yes_no(f"회차 {entry_id} 가 이미 있습니다. 덮어쓸까요?", args.yes):
            sys.exit("취소했습니다.")
        shutil.rmtree(dest, ignore_errors=True)
        entries = [e for e in entries if e["id"] != entry_id]

    composite_src = pick_composite(images, args.yes, can_generate=generated is not None)
    (dest / "sources").mkdir(parents=True)

    (dest / "index.html").write_text(inject_nav(html, entry_id), encoding="utf-8")

    if composite_src is None:
        full = generated
    else:
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
        "tags": extra + [t for t in layer_tags(html) if t not in extra],
        "version": int(time.time()),              # 링크에 붙여 브라우저 캐시를 피한다
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
