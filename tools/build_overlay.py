#!/usr/bin/env python3
"""원본 캡처(또는 이미 재투영된 레이어)를 하나의 메르카토르 좌표계로 모아 중첩 지도 HTML 을 만든다.

사용법:  python3 tools/build_overlay.py tools/configs/<회차>.json
결과물은 config 의 "out" 폴더(기본: inbox/)에 map.html, composite.png, 원본 사본(PNG)으로 저장된다.
그 다음 python3 add_entry.py 로 등록한다.

config 의 각 source 는 georef = [A, B, C, D] 를 가진다.
  원본 픽셀 x = A * 경도(rad) + B,  y = C * 메르카토르Y + D   (웹 메르카토르면 C = -A)
georef 값은 tools/georef.py 로 구한다. 이미 출력 좌표계와 같은 레이어 이미지는 "georef": "frame".

항적과 SIGMET 은 벡터(SVG)로 그려 확대해도 선명하고, 레이더·제트기류·기본 지도는 래스터(WebP)로 넣는다.
"""
import base64
import html as H
import json
import math
import re
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
    """출력 좌표계. u = (경도rad - LON0) * K,  v = (Y0 - 메르카토르Y) * K"""

    def __init__(self, spec):
        self.K = spec["K"]
        self.lon0 = spec["lon0"]
        self.LON0 = self.lon0 * D
        self.Y0 = spec["Y0"] if "Y0" in spec else merc(spec["lat1"])
        self.W = spec.get("W") or int(round((spec["lon1"] - spec["lon0"]) * D * self.K))
        self.H = spec.get("H") or int(round((self.Y0 - merc(spec["lat0"])) * self.K))

    def identity(self):
        return [self.K, -self.K * self.LON0, -self.K, self.K * self.Y0]

    def warp(self, src, g, interp=cv2.INTER_CUBIC):
        A, B, C, Dd = g
        M = np.array([[A / self.K, 0, A * self.LON0 + B], [0, -C / self.K, C * self.Y0 + Dd]], np.float64)
        return cv2.warpAffine(src, M, (self.W, self.H), flags=interp | cv2.WARP_INVERSE_MAP,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=0)

    def from_src(self, xy, g):
        """원본 픽셀 좌표 (N,2) -> 출력 좌표 (N,2)"""
        A, B, C, Dd = g
        xy = np.asarray(xy, np.float64)
        lon = (xy[:, 0] - B) / A
        my = (xy[:, 1] - Dd) / C
        return np.stack([(lon - self.LON0) * self.K, (self.Y0 - my) * self.K], 1)

    def from_lonlat(self, lon, lat):
        if lon < self.lon0:                    # 날짜변경선을 넘는 좌표계(예: 88°E~328°E)
            lon += 360
        return (lon * D - self.LON0) * self.K, (self.Y0 - merc(lat)) * self.K


# ---------------------------------------------------------------- 공통 도구

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
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_px, close_px)))
    if open_px:
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (open_px, open_px)))
    if min_area:
        n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
        keep = np.zeros(n, np.uint8)
        keep[1:] = st[1:, cv2.CC_STAT_AREA] >= min_area
        m = keep[lab]
    return m


def fill_outside(img, valid):
    """valid 밖의 픽셀을 가장 가까운 valid 픽셀 색으로 채운다 (필터가 가장자리에서 검게 번지지 않도록)."""
    if valid.all() or not valid.any():
        return img
    _, lab = cv2.distanceTransformWithLabels((valid > 0).astype(np.uint8) ^ 1, cv2.DIST_L2, 5,
                                             labelType=cv2.DIST_LABEL_PIXEL)
    vy, vx = np.nonzero(valid)
    lut = np.zeros((lab.max() + 1, 2), np.int32)
    lut[lab[vy, vx]] = np.stack([vy, vx], 1)
    ys, xs = np.nonzero(valid == 0)
    out = img.copy()
    src = lut[lab[ys, xs]]
    out[ys, xs] = img[src[:, 0], src[:, 1]]
    return out


def project_palette(px, pal):
    """px (N,3) 를 팔레트 꺾은선(M,3)에 투영 -> (거리, 0..M-1 사이 실수 위치)"""
    best_d = np.full(len(px), 1e9, np.float32)
    best_t = np.zeros(len(px), np.float32)
    for i in range(len(pal) - 1):
        a, c = pal[i], pal[i + 1]
        ab = c - a
        t = np.clip(((px - a) @ ab) / float(ab @ ab), 0, 1)
        d = np.linalg.norm(px - (a + t[:, None] * ab), axis=1)
        u = d < best_d
        best_d[u], best_t[u] = d[u], i + t[u]
    return best_d, best_t


def palette_at(pal, t):
    i = np.clip(np.floor(t).astype(int), 0, len(pal) - 2)
    f = np.clip(t - i, 0, 1)[..., None]
    return pal[i] * (1 - f) + pal[i + 1] * f


def hex_bgr(h):
    return tuple(int(h[i:i + 2], 16) for i in (5, 3, 1))


# ---------------------------------------------------------------- 래스터 레이어

def ext_copy(img, valid, p, ctx):
    return np.dstack([img, ctx["alpha"]])


def undo_top_shade(img, water_bgr):
    """FR24 화면 위쪽의 어두운 그라데이션을 바다색 기준으로 행마다 되돌린다."""
    f = img.astype(np.float32)
    b, g, r = f[..., 0], f[..., 1], f[..., 2]
    water = (b > g) & (g > r) & (b - r > 40) & (g - r > 30)
    true = np.array(water_bgr, np.float32)
    top = img.shape[0] // 3
    ratio = np.ones((img.shape[0], 3), np.float32)
    rows = [y for y in range(top) if water[y].sum() > 50]
    if not rows:
        return img
    for y in rows:
        ratio[y] = np.median(f[y][water[y]] / true, 0)
    ratio[:rows[0]] = ratio[rows[0]]
    for y in range(rows[0], top):
        if y not in rows:
            ratio[y] = ratio[y - 1]
    ratio = cv2.GaussianBlur(ratio[:, None, :], (1, 9), 0)[:, 0, :]
    ratio = np.clip(ratio, 0.35, 1.0)
    ratio[ratio > 0.985] = 1.0
    return np.clip(f / ratio[:, None, :], 0, 255).astype(np.uint8)


def ext_base_fr24(img, valid, p, ctx):
    b, g, r, mx, mn, sat = channels(img)
    track = (b > 180) & (g < 150) & (r < 230) & ((b - g) > 90)
    icons = ((r > 190) & (g > 140) & (b < 110) & (sat > 90)) | ((r > 190) & (g < 120) & (b < 120))
    mask = cv2.dilate(track.astype(np.uint8), np.ones((7, 7), np.uint8)) | \
        cv2.dilate(icons.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (17, 17)))
    valid = valid.copy()
    for x0, y0, x1, y1 in p.get("fill", []):              # 작은 UI 조각은 주변색으로 메움
        mask[y0:y1, x0:x1] = 1
        valid[y0:y1, x0:x1] = 255
    if p.get("water_rgb"):
        img = undo_top_shade(img, p["water_rgb"][::-1])
    out = cv2.inpaint(img, mask * 255, 9, cv2.INPAINT_NS)
    return np.dstack([out, valid])


def ext_radar(img, valid, p, ctx):
    """레이더: 회색 바탕(육지·바다)과 섞인 색을 풀어 범례색으로 되돌리고, 강도에 따라 반투명하게 그린다."""
    pal = np.array(p["palette_rgb"], np.float32)[:, ::-1]          # BGR, 약 -> 강
    dbz = np.array(p["palette_dbz"], np.float32)
    al = p.get("alpha", 0.8)
    sm = cv2.medianBlur(fill_outside(img, valid), 5)               # 흰 유선·잡티 제거
    b, g, r, mx, mn, sat = channels(sm)
    ob, og, orr, omx, omn, osat = channels(img)
    text = cv2.dilate(((omx < 95) & (ob >= orr)).astype(np.uint8), np.ones((7, 7), np.uint8))   # 남색 지명 글자 주변
    cand = (sat >= p.get("min_sat", 14)) & (valid > 0) & (mx >= p.get("min_bright", 105)) & (text == 0)
    ys, xs = np.nonzero(cand)
    px = sm[ys, xs].astype(np.float32)
    best_d = np.full(len(px), 1e9, np.float32)
    best_t = np.zeros(len(px), np.float32)
    for base in p.get("base_grays", [87, 154]):                     # 바탕이 바다/육지 중 맞는 쪽
        d, t = project_palette((px - (1 - al) * base) / al, pal)
        u = d < best_d
        best_d[u], best_t[u] = d[u], t[u]
    keep = best_d < p.get("max_dist", 55)
    mask = np.zeros(img.shape[:2], np.uint8)
    mask[ys[keep], xs[keep]] = 1
    tmap = np.zeros(img.shape[:2], np.float32)
    tmap[ys[keep], xs[keep]] = best_t[keep]
    mask = clean(mask, open_px=2, min_area=p.get("min_area", 16))
    # 등급 잡티 정리: 마스크 안에서 median
    t8 = np.where(mask > 0, tmap * 20 + 1, 0).astype(np.uint8)
    tm = cv2.medianBlur(t8, 5).astype(np.float32)
    tmap = np.where((mask > 0) & (tm > 0), (tm - 1) / 20, tmap) * (mask > 0)
    col = cv2.GaussianBlur(palette_at(pal, tmap).astype(np.float32), (0, 0), 0.8)
    val = np.interp(tmap, np.arange(len(dbz)), dbz)
    a_lo, a_hi = p.get("alpha_range", [0.45, 0.88])
    strength = np.clip(a_lo + (val - dbz[0]) / 30.0 * (a_hi - a_lo), a_lo, a_hi)
    cov = cv2.GaussianBlur(mask.astype(np.float32), (0, 0), 1.1)
    a = np.clip(cov * strength, 0, 1) * (valid > 0)
    ctx["aux"]["radar_dbz"] = (val * (mask > 0)).astype(np.float32)
    return np.dstack([np.clip(col, 0, 255).astype(np.uint8), (a * 255).astype(np.uint8)])


def ext_jet(img, valid, p, ctx):
    """제트기류: 흰 유선·글자를 지우고 붉은·자홍 계열(≈100 km/h 이상)만 부드러운 경계로 남긴다."""
    if p.get("erode_valid"):
        k = p["erode_valid"] * 2 + 1
        valid = cv2.erode(valid, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    src = fill_outside(img, valid)
    med = cv2.medianBlur(src, 11)
    diff = np.abs(src.astype(np.int16) - med.astype(np.int16)).sum(2)
    mn = channels(src)[4]
    mmn = channels(med)[4]
    streak = (diff > p.get("streak_diff", 40)) | ((mn - mmn) > 18)       # 흰 유선, 글자, 경계선
    streak = cv2.dilate(streak.astype(np.uint8) * 255, np.ones((3, 3), np.uint8))
    cl = cv2.inpaint(src, streak, 5, cv2.INPAINT_TELEA)
    cl = cv2.bilateralFilter(cl, 9, 30, 6)
    cl = cv2.GaussianBlur(cl, (0, 0), 1.0)
    hsv = cv2.cvtColor(cl, cv2.COLOR_BGR2HSV_FULL).astype(np.float32)
    hue = hsv[..., 0] * 360 / 256
    csat = channels(cl)[5]
    lo, hi = p.get("hue_range", [300, 8])
    m = ((hue >= lo) | (hue <= hi)) & (csat >= p.get("min_sat", 14)) & (valid > 0)
    m = clean(m, open_px=5, close_px=9, min_area=p.get("min_area", 800))
    a = cv2.GaussianBlur(m.astype(np.float32), (0, 0), 2.2) * p.get("opacity", 0.9)
    a = np.clip(a, 0, 1) * (valid > 0)
    if p.get("palette_rgb"):
        # 회색 바탕과 섞인 색을 풀어 범례의 붉은 계열(100~140 km/h) 색으로 다시 칠한다
        pal = np.array(p["palette_rgb"], np.float32)[:, ::-1]
        al = p.get("alpha", 0.7)
        ys, xs = np.nonzero(m)
        px = cl[ys, xs].astype(np.float32)
        best_d = np.full(len(px), 1e9, np.float32)
        best_t = np.zeros(len(px), np.float32)
        for base in p.get("base_grays", [87, 154]):
            d, t = project_palette((px - (1 - al) * base) / al, pal)
            u = d < best_d
            best_d[u], best_t[u] = d[u], t[u]
        tmap = np.zeros(m.shape, np.float32)
        tmap[ys, xs] = best_t
        w = cv2.GaussianBlur(m.astype(np.float32), (0, 0), 3)          # 마스크 안에서만 부드럽게
        tmap = cv2.GaussianBlur(tmap, (0, 0), 3) / np.maximum(w, 1e-3)
        cl = np.clip(palette_at(pal, tmap), 0, 255).astype(np.uint8)
        spd = p.get("palette_kmh", [100, 110, 120, 130, 140])
        ctx["aux"]["jet_kmh"] = (np.interp(tmap, np.arange(len(spd)), spd) * m).astype(np.float32)
    return np.dstack([cl, (a * 255).astype(np.uint8)])


def ext_flow(img, valid, p, ctx):
    """풍향 유선: 풍속 화면의 흰 유선만 뽑아 흰 선 + 옅은 어두운 테두리로 그린다.

    국경선·지명처럼 화면에 고정된 흰 요소는 같은 구도의 다른 캡처(static_ref, 예: 레이더 화면)에도 있으므로
    두 화면에 모두 밝게 나타나는 픽셀은 뺀다. static_offset 은 ref 대비 이 화면의 이동량(px).
    """
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (p.get("kernel", 7),) * 2)

    def bright_residual(im):
        # 흰 선은 세 채널이 모두 밝아지므로 최솟값 채널에서 가는 밝은 구조(top-hat)를 찾는다
        mn = im.min(2)
        return cv2.morphologyEx(mn, cv2.MORPH_TOPHAT, k).astype(np.float32)
    if p.get("erode_valid"):
        kk = p["erode_valid"] * 2 + 1
        valid = cv2.erode(valid, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kk, kk)))
    src = fill_outside(img, valid)
    r = bright_residual(src)
    if p.get("static_ref"):
        ref = cv2.imread(str(ROOT / p["static_ref"]), cv2.IMREAD_COLOR)
        dx, dy = p.get("static_offset", [0, 0])
        ref = cv2.warpAffine(ref, np.float32([[1, 0, dx], [0, 1, dy]]), (img.shape[1], img.shape[0]),
                             borderMode=cv2.BORDER_REPLICATE)
        static = cv2.dilate((bright_residual(ref) > p.get("static_thr", 14)).astype(np.uint8), np.ones((3, 3), np.uint8))
        r[static > 0] = 0
    core = np.clip((r - p.get("thr", 12)) / p.get("span", 45), 0, 1)
    core = clean(core > 0, min_area=p.get("min_area", 6)) * core            # 점 잡티 제거
    core *= (valid > 0)
    halo = np.clip(cv2.GaussianBlur(cv2.dilate(core, np.ones((3, 3), np.uint8)), (0, 0), 0.8) * p.get("halo", 0.6), 0, 1)
    a = core + halo * (1 - core)
    w = np.where(a > 0, core / np.maximum(a, 1e-3), 0)[..., None]
    dark = np.array(p.get("halo_bgr", [55, 40, 30]), np.float32)
    col = w * 255 + (1 - w) * dark
    return np.dstack([np.clip(col, 0, 255).astype(np.uint8), (np.clip(a * p.get("opacity", 0.85), 0, 1) * 255).astype(np.uint8)])


# ---------------------------------------------------------------- 벡터 레이어

def ext_track(img, valid, p, ctx):
    """항적: 항적색 픽셀을 모아 하나의 매끈한 선으로 잇는다 (아이콘·라벨에 가린 곳은 앞뒤를 이어 메움)."""
    F, g = ctx["F"], ctx["g"]
    if p.get("from_alpha"):
        mask = ctx["alpha"] > 40
    else:
        b, gg, r, mx, mn, sat = channels(img)
        mask = (b > 180) & (gg < 150) & (r < 230) & ((b - gg) > 90)
    mask = clean(mask & (valid > 0), min_area=p.get("min_area", 2))
    ys, xs = np.nonzero(mask)
    uv = F.from_src(np.stack([xs, ys], 1), g)
    step = p.get("bin", 4.0)
    key = np.round(uv[:, 0] / step).astype(int)
    order = np.argsort(key, kind="stable")
    key, uv = key[order], uv[order]
    _, start = np.unique(key, return_index=True)
    pts = np.array([np.median(s, 0) for s in np.split(uv, start[1:])])
    w = p.get("smooth", 7)                      # 이동평균으로 매끈하게 (양 끝은 보존)
    if len(pts) > w:
        k = np.ones(w) / w
        sm = np.stack([np.convolve(np.pad(pts[:, i], w // 2, mode="edge"), k, "valid") for i in (0, 1)], 1)
        pts = np.vstack([pts[:1], sm[1:-1], pts[-1:]])
    airports = p.get("airports", [])            # [["ICN", 위도, 경도], ...] 첫 번째가 출발
    if airports:
        dep = np.array(F.from_lonlat(airports[0][2], airports[0][1]))
        if np.linalg.norm(pts[0] - dep) > np.linalg.norm(pts[-1] - dep):
            pts = pts[::-1]
    ends = p.get("endpoints")                   # [[위도, 경도], [위도, 경도]] 선을 공항까지 잇기
    if ends:
        e = np.array([F.from_lonlat(lo, la) for la, lo in ends])
        if np.linalg.norm(pts[0] - e[0]) > np.linalg.norm(pts[0] - e[1]):
            e = e[::-1]
        pts = np.vstack([e[:1], pts, e[1:]])
    pts = cv2.approxPolyDP(pts.astype(np.float32).reshape(-1, 1, 2), 0.5, False).reshape(-1, 2)
    color, width = p.get("color", "#8A2BE2"), p.get("width", 2.6)
    d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    # 선 굵기는 화면 기준으로 일정하게: --s(현재 확대 배율)로 나눈다
    svg = (f'<path d="{d}" fill="none" stroke="#ffffff" stroke-opacity=".9" stroke-linejoin="round" '
           f'stroke-linecap="round" style="stroke-width:calc({width + 2.4}px / var(--s,1))"/>'
           f'<path d="{d}" fill="none" stroke="{color}" stroke-linejoin="round" stroke-linecap="round" '
           f'style="stroke-width:calc({width}px / var(--s,1))"/>')

    aps = [(code, *F.from_lonlat(lon, lat)) for code, lat, lon in airports]
    for code, x, y in aps:
        svg += (f'<g style="transform:translate({x:.1f}px,{y:.1f}px) scale(calc(1 / var(--s,1)))">'
                f'<circle r="4.5" fill="#fff" stroke="{color}" stroke-width="2.4"/>'
                f'<text x="7" y="-6" font-size="12" font-weight="700" fill="{color}" stroke="#fff" stroke-width="3" '
                f'paint-order="stroke" style="font-family:inherit">{H.escape(code)}</text></g>')

    def draw(canvas):
        q = np.round(pts * 16).astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(canvas, [q], False, (255, 255, 255), int(round(width + 2.4)), cv2.LINE_AA, 4)
        cv2.polylines(canvas, [q], False, hex_bgr(color), int(round(width)), cv2.LINE_AA, 4)
        for code, x, y in aps:
            c = (int(round(x)), int(round(y)))
            cv2.circle(canvas, c, 5, (255, 255, 255), -1, cv2.LINE_AA)
            cv2.circle(canvas, c, 5, hex_bgr(color), 2, cv2.LINE_AA)
            cv2.putText(canvas, code, (c[0] + 7, c[1] - 6), cv2.FONT_HERSHEY_DUPLEX, 0.45, (255, 255, 255), 3, cv2.LINE_AA)
            cv2.putText(canvas, code, (c[0] + 7, c[1] - 6), cv2.FONT_HERSHEY_DUPLEX, 0.45, hex_bgr(color), 1, cv2.LINE_AA)
    return {"svg": svg, "draw": draw, "pts": pts, "color": color}


def ext_sigmet(img, valid, p, ctx):
    """SIGMET: 구역을 빈틈없는 다각형으로 만들고 종류(TS 등)를 가운데에 적는다."""
    F, g = ctx["F"], ctx["g"]
    if p.get("from_alpha"):
        region = ctx["alpha"] > 20
    else:
        b, gg, r, mx, mn, sat = channels(img)
        region = ((r > 100) & (r - gg > 60) & (r - b > 50)) | ((r - gg > 14) & (r - b > 8) & (r > 150))
    # 끊긴 경계선을 잇고(close) 글자·잡티로 생긴 구멍을 메워(외곽선만 사용) 면으로 만든다.
    # 서로 겹친 구역은 하나의 면(합집합)으로 그린다.
    region = clean(region & (valid > 0), close_px=p.get("close", 9), min_area=p.get("min_area", 1500))
    cnts, _ = cv2.findContours(region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    labels = p.get("labels", [])
    polys = []
    for c in cnts:
        c = cv2.approxPolyDP(c, p.get("simplify", 1.6), True).reshape(-1, 2)
        if len(c) < 3:
            continue
        uv = F.from_src(c, g)
        x0, y0 = np.floor(uv.min(0)).astype(int) - 2      # 라벨 위치: 다각형 안에서 가장자리와 가장 먼 점
        x1, y1 = np.ceil(uv.max(0)).astype(int) + 3
        m = np.zeros((y1 - y0, x1 - x0), np.uint8)
        cv2.fillPoly(m, [np.round(uv - [x0, y0]).astype(np.int32)], 1)
        dist = cv2.distanceTransform(m, cv2.DIST_L2, 5)
        cy, cx = np.unravel_index(dist.argmax(), dist.shape)
        lx, ly, room = cx + x0, cy + y0, float(dist.max())
        text = p.get("default_label", "TS")
        if labels:
            dd = [math.hypot(*(np.array(F.from_lonlat(lo, la)) - [lx, ly])) for lo, la, _ in labels]
            j = int(np.argmin(dd))
            if dd[j] < p.get("label_radius", 60):
                text = labels[j][2]
        polys.append((uv, lx, ly, room, text))
    fs = p.get("font_px", 15)
    fill, stroke = p.get("fill", "#C0392B"), p.get("stroke", "#8B1A1A")
    show = lambda room, text: text and room >= fs * 0.45
    parts = []
    for uv, lx, ly, room, text in polys:
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in uv)
        parts.append(f'<polygon points="{pts}" fill="{fill}" fill-opacity=".2" stroke="{stroke}" '
                     f'stroke-linejoin="round" style="stroke-width:calc(1.8px / var(--s,1))"/>')
        if show(room, text):
            parts.append(f'<text x="{lx:.1f}" y="{ly:.1f}" data-r="{room:.0f}" data-n="{len(text)}" text-anchor="middle" dominant-baseline="central" '
                         f'fill="{stroke}" font-weight="600" stroke="#fff" stroke-opacity=".8" paint-order="stroke" '
                         f'style="font-family:inherit;font-size:calc({p.get("screen_font", 12)}px / var(--s,1));'
                         f'stroke-width:calc(3px / var(--s,1))">{H.escape(text)}</text>')

    def draw(canvas):
        over = canvas.copy()
        for uv, *_ in polys:
            cv2.fillPoly(over, [np.round(uv * 16).astype(np.int32)], hex_bgr(fill), cv2.LINE_AA, 4)
        cv2.addWeighted(over, 0.2, canvas, 0.8, 0, canvas)
        for uv, lx, ly, room, text in polys:
            cv2.polylines(canvas, [np.round(uv * 16).astype(np.int32)], True, hex_bgr(stroke), 2, cv2.LINE_AA, 4)
            if show(room, text):
                sc = fs / 30
                (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX, sc, 1)
                org = (int(lx - tw / 2), int(ly + th / 2))
                cv2.putText(canvas, text, org, cv2.FONT_HERSHEY_DUPLEX, sc, (255, 255, 255), 3, cv2.LINE_AA)
                cv2.putText(canvas, text, org, cv2.FONT_HERSHEY_DUPLEX, sc, hex_bgr(stroke), 1, cv2.LINE_AA)
    return {"svg": "".join(parts), "draw": draw, "polys": [(uv, text) for uv, _, _, _, text in polys]}


EXTRACTORS = {
    "copy": ext_copy,
    "base_fr24": ext_base_fr24,
    "radar": ext_radar,
    "jet": ext_jet,
    "flow": ext_flow,
    "track": ext_track,
    "sigmet": ext_sigmet,
}


# ---------------------------------------------------------------- 격자, 출력

def grid_layer(F, step=10):
    lay = np.zeros((F.H, F.W, 4), np.uint8)
    col = (138, 138, 138, 150)
    lon = math.ceil(F.lon0 / step) * step
    while True:
        x, _ = F.from_lonlat(lon, 0)
        if x > F.W:
            break
        cv2.line(lay, (int(round(x)), 0), (int(round(x)), F.H), col, 1, cv2.LINE_AA)
        w = ((lon + 180) % 360) - 180
        lab = f"{abs(w)}{'E' if w > 0 else 'W' if w < 0 else ''}"
        cv2.putText(lay, lab, (int(x) + 4, F.H - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (60, 60, 60, 230), 1, cv2.LINE_AA)
        lon += step
    for lat in range(-80, 90, step):
        _, y = F.from_lonlat(0, lat)
        if 0 <= y <= F.H:
            cv2.line(lay, (0, int(round(y))), (F.W, int(round(y))), col, 1, cv2.LINE_AA)
            cv2.putText(lay, f"{abs(lat)}{'N' if lat > 0 else 'S' if lat < 0 else ''}", (6, int(y) - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (60, 60, 60, 230), 1, cv2.LINE_AA)
    return lay


def webp_b64(bgra, q):
    ok, buf = cv2.imencode(".webp", bgra, [cv2.IMWRITE_WEBP_QUALITY, q])
    assert ok
    return base64.b64encode(buf.tobytes()).decode()


# ---------------------------------------------------------------- 항로 영향 구간

def _lonlat(F, u, v):
    lon = (F.LON0 + u / F.K) / D
    lat = (2 * np.arctan(np.exp(F.Y0 - v / F.K)) - math.pi / 2) / D
    return (lon + 180) % 360 - 180, lat


def _gc_nm(lo1, la1, lo2, la2):
    p1, p2, dl = la1 * D, la2 * D, (lo2 - lo1) * D
    c = np.sin(p1) * np.sin(p2) + np.cos(p1) * np.cos(p2) * np.cos(dl)
    return np.arccos(np.clip(c, -1, 1)) * 3440.065


def _fmt_ll(lo, la):
    return f"{abs(la):.1f}°{'N' if la >= 0 else 'S'} {abs(lo):.1f}°{'E' if lo >= 0 else 'W'}"


def impact_html(F, layers, aux, cfg):
    """항적이 SIGMET·제트기류·강한 레이더 에코를 지나는 구간을 찾아 패널용 HTML 로 만든다."""
    tr = next((l for l in layers.values() if isinstance(l, dict) and "pts" in l), None)
    if tr is None:
        return ""
    P = tr["pts"]
    seg = np.diff(P, axis=0)
    n = np.maximum(1, np.ceil(np.linalg.norm(seg, axis=1) / 2).astype(int))
    pts = np.vstack([P[i] + seg[i] * (np.arange(n[i])[:, None] / n[i]) for i in range(len(seg))] + [P[-1:]])
    lon, lat = _lonlat(F, pts[:, 0], pts[:, 1])
    cum = np.concatenate([[0], np.cumsum(_gc_nm(lon[:-1], lat[:-1], lon[1:], lat[1:]))])
    xi = np.clip(np.round(pts[:, 0]).astype(int), 0, F.W - 1)
    yi = np.clip(np.round(pts[:, 1]).astype(int), 0, F.H - 1)
    ic = cfg.get("impact", {})
    hazards = []                                   # (이름, 색, bool 배열, 값 배열, 값 형식)
    for l in layers.values():
        if isinstance(l, dict) and "polys" in l:
            for uv, text in l["polys"]:
                c = uv.astype(np.float32).reshape(-1, 1, 2)
                inside = np.array([cv2.pointPolygonTest(c, (float(x), float(y)), False) >= 0 for x, y in pts])
                if inside.any():
                    hazards.append((f"SIGMET {text or ''}".strip(), "#8B1A1A", inside, None, None))
    if "jet_kmh" in aux:
        v = aux["jet_kmh"][yi, xi]
        hazards.append(("제트기류", "#901B4D", v >= ic.get("jet_kmh", 100), v,
                        lambda x: f"최대 약 {x:.0f} km/h({x / 1.852:.0f} kt)"))
    if "radar_dbz" in aux:
        v = aux["radar_dbz"][yi, xi]
        hazards.append(("레이더 에코", "#5CBE4B", v >= ic.get("radar_dbz", 30), v,
                        lambda x: f"최대 약 {x:.0f} dBZ"))
    rows = []
    for name, color, on, val, fmt in hazards:
        edges = np.flatnonzero(np.diff(np.concatenate([[0], on.astype(int), [0]])))
        runs = []
        for a, b in zip(edges[::2], edges[1::2] - 1):  # 짧은 끊김(gap_nm 이하)은 한 구간으로
            if runs and cum[a] - cum[runs[-1][1]] <= ic.get("gap_nm", 60):
                runs[-1][1] = b
            else:
                runs.append([a, b])
        for a, b in runs:
            length = cum[b] - cum[a]
            if length < ic.get("min_nm", 15):
                continue
            extra = fmt(float(val[a:b + 1].max())) if fmt else ""
            box = pts[a:b + 1]
            rows.append((cum[a], name, color, cum[b], length, (lon[a], lat[a]), (lon[b], lat[b]), extra, box))
    rows.sort(key=lambda r: r[0])
    if not rows:
        body = '<p class="imp-none">항적이 지나는 구간에서 표시된 위험 기상과 겹치는 곳이 없습니다.</p>'
    else:
        li = []
        for start, name, color, end, length, p0, p1, extra, box in rows:
            d = " ".join(f"{x:.0f},{y:.0f}" for x, y in box[::3])
            li.append(f'<li><button type="button" data-pts="{d}"><span class="dot" style="background:{color}"></span>'
                      f'<span class="it"><b>{H.escape(name)}</b> · 출발 후 {start:,.0f}–{end:,.0f} NM '
                      f'<span class="len">({length:,.0f} NM)</span>'
                      f'{"<br>" + extra if extra else ""}<br><small>{_fmt_ll(*p0)} → {_fmt_ll(*p1)}</small></span></button></li>')
        body = '<ul class="imp">' + "".join(li[:5]) + '</ul>'
        if len(li) > 5:
            body += (f'<details class="more"><summary>나머지 {len(li) - 5}개 구간 더 보기</summary>'
                     f'<ul class="imp">{"".join(li[5:])}</ul></details>')
    total = cum[-1]
    return (f'<div class="impact"><h2>항로 영향 구간 <span class="tot">항적 {total:,.0f} NM</span></h2>{body}'
            f'<p class="imp-note">항목을 누르면 지도에서 해당 구간으로 이동합니다. 자료 시각이 서로 달라 '
            f'실제 조우 여부와는 다를 수 있습니다.</p></div>')


def time_badge(t, ref):
    """레이어 자료 시각(HH:MM)과 기준 시각의 차이를 배지로."""
    if not t or not ref:
        return ""
    def minutes(x):                              # "HH:MM" 또는 "YYYY-MM-DD HH:MM"
        import datetime as dt
        if " " in x:
            return dt.datetime.strptime(x, "%Y-%m-%d %H:%M").timestamp() / 60
        h, m = map(int, x.split(":"))
        return h * 60 + m
    d = int(round(minutes(t) - minutes(ref)))
    if d == 0:
        return '<span class="tb tb-ref">기준</span>'
    sign = "+" if d > 0 else "−"
    a = abs(d)
    txt = f"{sign}{a // 60}h{a % 60:02d}m" if a >= 60 else f"{sign}{a}m"
    if a >= 1440:
        txt = f"{sign}{a // 1440}d{a % 1440 // 60}h"
    cls = "tb-ok" if a <= 30 else "tb-mid" if a <= 120 else "tb-far"
    return f'<span class="tb {cls}" title="항적 기준 시각 대비 자료 시각 차이">{txt}</span>'


def layer_block(L, ref=None):
    lid = L["id"]
    scale = ""
    if L.get("scale"):
        spans = "".join(f'<span style="background:{c}">{H.escape(str(t))}</span>' for c, t in L["scale"])
        if L.get("scale_unit"):
            spans += (f'<span style="background:transparent;color:var(--muted);text-shadow:none;flex:1.4">'
                      f'{H.escape(L["scale_unit"])}</span>')
        scale = f'\n      <div class="scale">{spans}</div>'
        if L.get("scale2"):
            s2 = "".join(f'<span>{H.escape(str(t))}</span>' for t in L["scale2"])
            if L.get("scale2_unit"):
                s2 += f'<span style="flex:1.4">{H.escape(L["scale2_unit"])}</span>'
            scale += f'\n      <div class="scale scale2">{s2}</div>'
    op = "" if L.get("no_opacity") else (
        f'\n      <div class="op">투명도<input type="range" min="0" max="100" value="100" data-for="img-{lid}" '
        f'aria-label="{H.escape(L["label"])} 불투명도"></div>')
    meta = f'\n      <div class="meta">{L["meta"]}</div>' if L.get("meta") else ""
    return (f'    <div class="layer"><div class="row"><input type="checkbox" id="t-{lid}" checked>'
            f'<span class="swatch" style="background:{L["swatch"]}"></span><label for="t-{lid}">{H.escape(L["label"])}</label>'
            f'{time_badge(L.get("time"), ref)}</div>'
            f'{meta}{scale}{op}</div>\n')


def load_source(s):
    img = cv2.imread(str(ROOT / s["file"]), cv2.IMREAD_UNCHANGED)
    if img is None:
        sys.exit(f"이미지를 읽을 수 없습니다: {s['file']}")
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    alpha = img[..., 3].copy() if img.shape[2] == 4 else np.full(img.shape[:2], 255, np.uint8)
    img = np.ascontiguousarray(img[..., :3])
    valid = ui_mask(img.shape, s.get("ui", []))
    valid[alpha == 0] = 0
    return img, alpha, valid


def main(cfg_path):
    cfg = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
    F = Frame(cfg["frame"])
    print(f"출력 좌표계 {F.W}x{F.H}px")

    layers, aux = {}, {}
    for s in cfg["sources"]:
        img, alpha, valid = load_source(s)
        g = F.identity() if s["georef"] == "frame" else s["georef"]
        ctx = {"F": F, "g": g, "alpha": alpha, "aux": {}}
        for lay in s["layers"]:
            res = EXTRACTORS[lay["kind"]](img, valid, lay, ctx)
            if isinstance(res, dict):
                layers[lay["id"]] = res
            else:
                interp = cv2.INTER_NEAREST if s["georef"] == "frame" else cv2.INTER_CUBIC
                layers[lay["id"]] = F.warp(res, g, interp)
            print(f"  {lay['id']:<8} <- {s['file']} ({lay['kind']})")
        for k, v in ctx["aux"].items():
            aux[k] = F.warp(v, g, cv2.INTER_NEAREST if s["georef"] == "frame" else cv2.INTER_LINEAR)
    if "grid" not in layers:
        layers["grid"] = grid_layer(F)

    order = cfg["order"]                     # 아래 -> 위
    bg = cfg.get("map_bg", "#6ACFE3")
    comp = np.zeros((F.H, F.W, 3), np.float32)
    comp[:] = hex_bgr(bg)
    for lid in order:
        l = layers[lid]
        if isinstance(l, dict):
            c8 = np.ascontiguousarray(np.clip(comp, 0, 255).astype(np.uint8))
            l["draw"](c8)
            comp = c8.astype(np.float32)
        else:
            a = l[..., 3:4].astype(np.float32) / 255
            comp = comp * (1 - a) + l[..., :3].astype(np.float32) * a

    out = (ROOT / cfg.get("out", "inbox")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out / "composite.png"), np.clip(comp, 0, 255).astype(np.uint8))
    for s in cfg["sources"]:
        src = (ROOT / s["file"]).resolve()
        dst = out / (s.get("save_as") or src.name)
        if src.suffix.lower() not in (".png", ".jpg", ".jpeg"):   # webp 등은 PNG 로 바꿔 둔다
            dst = dst.with_suffix(".png")
            cv2.imwrite(str(dst), cv2.imread(str(src), cv2.IMREAD_UNCHANGED))
        elif src != dst:
            shutil.copy2(src, dst)

    q = cfg.get("webp_quality", 92)
    elems = []
    for i, lid in enumerate(order):
        l = layers[lid]
        if isinstance(l, dict):
            elems.append(f'      <svg id="img-{lid}" viewBox="0 0 {F.W} {F.H}" aria-hidden="true">{l["svg"]}</svg>')
        else:
            alt = "기본 지도" if i == 0 else ""
            elems.append(f'      <img id="img-{lid}" alt="{alt}" src="data:image/webp;base64,{webp_b64(l, q)}">')

    tpl = (Path(__file__).parent / "map_template.html").read_text(encoding="utf-8")
    if cfg.get("reuse_html"):                 # 기존 지도 HTML 의 설명 패널을 그대로 사용
        old = (ROOT / cfg["reuse_html"]).read_text(encoding="utf-8")
        aside = old[old.index("<h1>"):old.index("</aside>")]
        for x, y in cfg.get("replace", []):
            if y in aside and x not in aside:      # 이미 바뀐 상태
                continue
            assert x in aside, f"바꿀 문구를 찾지 못함: {x}"
            aside = aside.replace(x, y)
        title = re.search(r"<title>(.*?)</title>", old).group(1)
        page = tpl[:tpl.index("<h1>")] + aside + tpl[tpl.index("</aside>"):]
        page = page.replace("{{TITLE}}", title)
    else:
        warn = ' class="warn"'
        notes = "".join(f'      <p{warn if n.get("warn") else ""}>{n["text"]}</p>\n' for n in cfg["notes"])
        page = (tpl.replace("{{TITLE}}", H.escape(cfg["title"]))
                   .replace("{{H1}}", H.escape(cfg["h1"]))
                   .replace("{{SUB}}", H.escape(cfg["sub"]))
                   .replace("{{PANEL}}", "".join(layer_block(L, cfg.get("ref_time")) for L in cfg["panel"]))
                   .replace("{{NOTES}}", notes))
    page = (page.replace("{{IMPACT}}", impact_html(F, layers, aux, cfg))
                .replace("{{MAP_BG}}", bg)
                .replace("{{IMGS}}", "\n".join(elems))
                .replace("{{W}}", str(F.W)).replace("{{H}}", str(F.H))
                .replace("{{K}}", repr(float(F.K))).replace("{{LON0}}", repr(float(F.lon0)))
                .replace("{{Y0}}", repr(F.Y0)))
    assert "{{" not in page
    (out / "map.html").write_text(page, encoding="utf-8")
    print(f"완료: {out}/map.html ({len(page) // 1024} KB), composite.png")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
