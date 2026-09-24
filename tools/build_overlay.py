#!/usr/bin/env python3
"""원본 캡처 여러 장을 하나의 메르카토르 좌표계로 재투영해 중첩 지도 HTML 을 만든다.

사용법:  python3 tools/build_overlay.py tools/configs/<회차>.json
결과물은 config 의 "out" 폴더(기본: inbox/)에 map.html, composite.png, 원본 캡처 사본으로 저장된다.
그 다음 python3 add_entry.py 로 등록한다.

config 의 각 source 는 georef = [A, B, C, D] 를 가진다.
  원본 픽셀 x = A * 경도(rad) + B,  y = C * 메르카토르Y + D   (웹 메르카토르면 C = -A)
georef 값은 tools/georef.py 로 구한다.
"""
import base64
import html as H
import json
import math
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

D = math.pi / 180
ROOT = Path(__file__).resolve().parent.parent


def merc(lat):
    return math.log(math.tan(math.pi / 4 + lat * D / 2))


class Frame:
    def __init__(self, K, lon0, lon1, lat0, lat1):
        self.K, self.lon0 = K, lon0
        self.LON0, self.Y0 = lon0 * D, merc(lat1)
        self.W = int(round((lon1 - lon0) * D * K))
        self.H = int(round((merc(lat1) - merc(lat0)) * K))

    def warp(self, src, g, interp=cv2.INTER_LINEAR):
        A, B, C, Dd = g
        M = np.array([[A / self.K, 0, A * self.LON0 + B], [0, -C / self.K, C * self.Y0 + Dd]], np.float64)
        return cv2.warpAffine(src, M, (self.W, self.H), flags=interp | cv2.WARP_INVERSE_MAP,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=0)

    def xy(self, lon, lat):
        return (lon * D - self.LON0) * self.K, (self.Y0 - merc(lat)) * self.K


def ui_mask(shape, rects):
    m = np.full(shape[:2], 255, np.uint8)
    for x0, y0, x1, y1 in rects:
        m[y0:y1, x0:x1] = 0
    return m


def channels(img):
    i = img.astype(np.int16)
    b, g, r = i[..., 0], i[..., 1], i[..., 2]
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    return b, g, r, mx, mn, mx - mn


def clean(mask, open_px=0, close_px=0, min_area=0):
    m = (mask > 0).astype(np.uint8)
    if close_px:
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((close_px, close_px), np.uint8))
    if open_px:
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((open_px, open_px), np.uint8))
    if min_area:
        n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
        keep = np.zeros(n, np.uint8)
        keep[1:] = st[1:, cv2.CC_STAT_AREA] >= min_area
        m = keep[lab]
    return m


# ---- 레이어 추출기: 원본 이미지(BGR) -> (BGRA 레이어, 알파가 들어간 원본 좌표계 이미지) ----

def undo_top_shade(img, water_bgr):
    """FR24 화면 위쪽의 어두운 그라데이션을 바다색 기준으로 행마다 되돌린다."""
    f = img.astype(np.float32)
    b, g, r = f[..., 0], f[..., 1], f[..., 2]
    water = (b > g) & (g > r) & (b - r > 40) & (g - r > 30)
    true = np.array(water_bgr, np.float32)
    ratio = np.ones((img.shape[0], 3), np.float32)
    rows = [y for y in range(img.shape[0] // 3) if water[y].sum() > 50]
    for y in rows:
        ratio[y] = np.median(f[y][water[y]] / true, 0)
    if not rows:
        return img
    ratio[:rows[0]] = ratio[rows[0]]
    for y in range(rows[0], img.shape[0] // 3):
        if y not in rows:
            ratio[y] = ratio[y - 1]
    ratio = cv2.GaussianBlur(ratio[:, None, :], (1, 9), 0)[:, 0, :]
    ratio = np.clip(ratio, 0.35, 1.0)
    ratio[ratio > 0.985] = 1.0
    return np.clip(f / ratio[:, None, :], 0, 255).astype(np.uint8)


def ext_base_fr24(img, ui, p):
    b, g, r, mx, mn, sat = channels(img)
    track = (b > 180) & (g < 150) & (r < 230) & ((b - g) > 90)
    planes = (r > 190) & (g > 140) & (b < 110) & (sat > 90)          # 노란 항공기 아이콘
    red = (r > 190) & (g < 120) & (b < 120)                            # 선택된 항공기(빨강)
    dark_icon = cv2.dilate(((planes | red)).astype(np.uint8), np.ones((9, 9), np.uint8))
    mask = cv2.dilate((track | planes | red).astype(np.uint8), np.ones((7, 7), np.uint8)) | dark_icon
    ui = ui.copy()
    for x0, y0, x1, y1 in p.get("fill", []):                          # 작은 UI 조각은 주변색으로 메움
        mask[y0:y1, x0:x1] = 1
        ui[y0:y1, x0:x1] = 255
    if p.get("water_rgb"):
        img = undo_top_shade(img, p["water_rgb"][::-1])
    out = cv2.inpaint(img, mask * 255, 6, cv2.INPAINT_TELEA)
    return np.dstack([out, ui])


def ext_track_fr24(img, ui, p):
    b, g, r, mx, mn, sat = channels(img)
    track = (b > 180) & (g < 150) & (r < 230) & ((b - g) > 90)
    m = clean(track, close_px=3, min_area=p.get("min_area", 30)) * (ui > 0)
    m = cv2.dilate(m, np.ones((2, 2), np.uint8))
    return np.dstack([img, m * 255])


def ext_sigmet_awc(img, ui, p):
    b, g, r, mx, mn, sat = channels(img)
    outline = (r > 110) & (r - g > 60) & (r - b > 50)
    pink = (r - g > 14) & (r - b > 8) & (r > 150)
    region = clean((outline | pink) & (ui > 0), close_px=5, min_area=p.get("min_area", 1500))
    cnts, _ = cv2.findContours(region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    fill = np.zeros_like(region)
    cv2.drawContours(fill, cnts, -1, 1, -1)
    fill &= (ui > 0)
    out = img.copy()
    a = np.zeros(img.shape[:2], np.uint8)
    inner = fill.astype(bool)
    out[inner] = (70, 60, 190)          # 채움색 (BGR)
    a[inner] = 70
    text = inner & (mx < 90)            # 'TS' 등 글자
    edge = inner & outline
    out[text] = (20, 20, 20); a[text] = 255
    out[edge] = (26, 26, 139); a[edge] = 255
    edge2 = cv2.dilate(edge.astype(np.uint8), np.ones((2, 2), np.uint8)).astype(bool) & inner
    out[edge2] = (26, 26, 139); a[edge2] = 255
    return np.dstack([out, a])


def ext_radar_ventusky(img, ui, p):
    b, g, r, mx, mn, sat = channels(img)
    m = (sat >= 26) & (mx >= 95)
    m = clean(m, open_px=2, close_px=3, min_area=12) * (ui > 0)
    return np.dstack([img, m * 255])


def ext_jet_ventusky(img, ui, p):
    """Ventusky 풍속 화면에서 제트기류 구간만 뽑는다.

    Ventusky 범례에서 100 km/h 이상(자홍 → 적갈 → 검정)만 붉은·자홍 색상(hue 300°~360°)을 쓰고,
    90 km/h 이하(주황·노랑·초록·파랑)는 쓰지 않는다. 회색 바탕과 섞여도 색상(hue)은 거의 변하지 않으므로
    색상 범위로 가른다. 흰 유선·글자는 median 필터로 줄인다.
    """
    sm = cv2.medianBlur(img, 9)
    hsv = cv2.cvtColor(sm, cv2.COLOR_BGR2HSV_FULL).astype(np.float32)    # H: 0~255
    hue = hsv[..., 0] * 360 / 256
    b, g, r, mx, mn, sat = channels(sm)
    lo, hi = p.get("hue_range", [300, 8])
    m = ((hue >= lo) | (hue <= hi)) & (sat >= p.get("min_sat", 14))
    m = clean(m, open_px=5, close_px=9, min_area=p.get("min_area", 800)) * (ui > 0)
    m = cv2.GaussianBlur(m.astype(np.float32), (5, 5), 0)
    return np.dstack([img, (m * 255).astype(np.uint8)])


EXTRACTORS = {
    "base_fr24": ext_base_fr24,
    "track_fr24": ext_track_fr24,
    "sigmet_awc": ext_sigmet_awc,
    "radar_ventusky": ext_radar_ventusky,
    "jet_ventusky": ext_jet_ventusky,
}


def grid_layer(F, step=10):
    lay = np.zeros((F.H, F.W, 4), np.uint8)
    col = (138, 138, 138, 150)
    lon = math.ceil(F.lon0 / step) * step
    while True:
        x, _ = F.xy(lon, 0)
        if x > F.W:
            break
        cv2.line(lay, (int(round(x)), 0), (int(round(x)), F.H), col, 1, cv2.LINE_AA)
        lab = f"{abs(lon)}{'E' if lon > 0 else 'W' if lon < 0 else ''}"
        cv2.putText(lay, lab, (int(x) + 4, F.H - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (60, 60, 60, 230), 1, cv2.LINE_AA)
        lon += step
    for lat in range(-80, 90, step):
        _, y = F.xy(0, lat)
        if 0 <= y <= F.H:
            cv2.line(lay, (0, int(round(y))), (F.W, int(round(y))), col, 1, cv2.LINE_AA)
            cv2.putText(lay, f"{abs(lat)}{'N' if lat > 0 else 'S' if lat < 0 else ''}", (6, int(y) - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (60, 60, 60, 230), 1, cv2.LINE_AA)
    return lay


def webp_b64(bgra):
    ok, buf = cv2.imencode(".webp", bgra, [cv2.IMWRITE_WEBP_QUALITY, 88])
    assert ok
    return base64.b64encode(buf.tobytes()).decode()


def layer_block(L):
    lid = L["id"]
    meta = L.get("meta", "")
    scale = ""
    if L.get("scale"):
        spans = "".join(f'<span style="background:{c}">{H.escape(str(t))}</span>' for c, t in L["scale"])
        if L.get("scale_unit"):
            spans += f'<span style="background:transparent;color:var(--muted);text-shadow:none;flex:1.4">{H.escape(L["scale_unit"])}</span>'
        scale = f'\n      <div class="scale">{spans}</div>'
    op = "" if L.get("no_opacity") else (
        f'\n      <div class="op">투명도<input type="range" min="0" max="100" value="100" data-for="img-{lid}" '
        f'aria-label="{H.escape(L["label"])} 불투명도"></div>')
    meta_html = f'\n      <div class="meta">{meta}</div>' if meta else ""
    return (f'    <div class="layer"><div class="row"><input type="checkbox" id="t-{lid}" checked>'
            f'<span class="swatch" style="background:{L["swatch"]}"></span><label for="t-{lid}">{H.escape(L["label"])}</label></div>'
            f'{meta_html}{scale}{op}</div>\n')


def main(cfg_path):
    cfg_path = Path(cfg_path)
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    base_dir = ROOT                          # config 의 파일 경로는 저장소 루트 기준
    fr = cfg["frame"]
    F = Frame(fr["K"], fr["lon0"], fr["lon1"], fr["lat0"], fr["lat1"])
    print(f"출력 좌표계 {F.W}x{F.H}px")

    layers = {}
    for s in cfg["sources"]:
        img = cv2.imread(str((base_dir / s["file"]).resolve()), cv2.IMREAD_COLOR)
        if img is None:
            sys.exit(f"이미지를 읽을 수 없습니다: {s['file']}")
        ui = ui_mask(img.shape, s.get("ui", []))
        for lay in s["layers"]:
            src = EXTRACTORS[lay["kind"]](img, ui, lay)
            layers[lay["id"]] = F.warp(src, s["georef"])
            print(f"  {lay['id']:<8} <- {s['file']} ({lay['kind']})")
    layers["grid"] = grid_layer(F)

    order = cfg["order"]                     # 아래 -> 위
    bg = cfg.get("map_bg", "#6ACFE3")
    comp = np.zeros((F.H, F.W, 3), np.float32)
    comp[:] = [int(bg[i:i + 2], 16) for i in (5, 3, 1)]
    for lid in order:
        l = layers[lid].astype(np.float32)
        a = l[..., 3:4] / 255
        comp = comp * (1 - a) + l[..., :3] * a

    out = (ROOT / cfg.get("out", "inbox")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out / "composite.png"), comp.astype(np.uint8))
    for s in cfg["sources"]:
        src = (base_dir / s["file"]).resolve()
        dst = out / (s.get("save_as") or src.name)
        if src.suffix.lower() not in (".png", ".jpg", ".jpeg"):   # webp 등은 PNG 로 바꿔 둔다
            dst = dst.with_suffix(".png")
            cv2.imwrite(str(dst), cv2.imread(str(src)))
        elif src != dst:
            shutil.copy2(src, dst)

    imgs = "\n".join(f'      <img id="img-{lid}" alt="{"기본 지도" if i == 0 else ""}" src="data:image/webp;base64,{webp_b64(layers[lid])}">'
                     for i, lid in enumerate(order))
    panel = "".join(layer_block(L) for L in cfg["panel"])
    warn = ' class="warn"'
    notes = "".join(f'      <p{warn if n.get("warn") else ""}>{n["text"]}</p>\n' for n in cfg["notes"])
    tpl = (Path(__file__).parent / "map_template.html").read_text(encoding="utf-8")
    page = (tpl.replace("{{TITLE}}", H.escape(cfg["title"]))
               .replace("{{H1}}", H.escape(cfg["h1"]))
               .replace("{{SUB}}", H.escape(cfg["sub"]))
               .replace("{{MAP_BG}}", bg)
               .replace("{{IMGS}}", imgs)
               .replace("{{PANEL}}", panel)
               .replace("{{NOTES}}", notes)
               .replace("{{W}}", str(F.W)).replace("{{H}}", str(F.H))
               .replace("{{K}}", repr(float(F.K))).replace("{{LON0}}", repr(float(F.lon0)))
               .replace("{{Y0}}", repr(F.Y0)))
    (out / "map.html").write_text(page, encoding="utf-8")
    print(f"완료: {out}/map.html, composite.png ({len(page) // 1024} KB)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
