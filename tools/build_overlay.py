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
    return np.dstack([cl, (a * 255).astype(np.uint8)])


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
    ends = p.get("endpoints")                   # [[위도, 경도], [위도, 경도]] 출발·도착 공항
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

    def draw(canvas):
        q = np.round(pts * 16).astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(canvas, [q], False, (255, 255, 255), int(round(width + 2.4)), cv2.LINE_AA, 4)
        cv2.polylines(canvas, [q], False, hex_bgr(color), int(round(width)), cv2.LINE_AA, 4)
    return {"svg": svg, "draw": draw}


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
            parts.append(f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" dominant-baseline="central" '
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
    return {"svg": "".join(parts), "draw": draw}


EXTRACTORS = {
    "copy": ext_copy,
    "base_fr24": ext_base_fr24,
    "radar": ext_radar,
    "jet": ext_jet,
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


def layer_block(L):
    lid = L["id"]
    scale = ""
    if L.get("scale"):
        spans = "".join(f'<span style="background:{c}">{H.escape(str(t))}</span>' for c, t in L["scale"])
        if L.get("scale_unit"):
            spans += (f'<span style="background:transparent;color:var(--muted);text-shadow:none;flex:1.4">'
                      f'{H.escape(L["scale_unit"])}</span>')
        scale = f'\n      <div class="scale">{spans}</div>'
    op = "" if L.get("no_opacity") else (
        f'\n      <div class="op">투명도<input type="range" min="0" max="100" value="100" data-for="img-{lid}" '
        f'aria-label="{H.escape(L["label"])} 불투명도"></div>')
    meta = f'\n      <div class="meta">{L["meta"]}</div>' if L.get("meta") else ""
    return (f'    <div class="layer"><div class="row"><input type="checkbox" id="t-{lid}" checked>'
            f'<span class="swatch" style="background:{L["swatch"]}"></span><label for="t-{lid}">{H.escape(L["label"])}</label></div>'
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

    layers = {}
    for s in cfg["sources"]:
        img, alpha, valid = load_source(s)
        g = F.identity() if s["georef"] == "frame" else s["georef"]
        ctx = {"F": F, "g": g, "alpha": alpha}
        for lay in s["layers"]:
            res = EXTRACTORS[lay["kind"]](img, valid, lay, ctx)
            if isinstance(res, dict):
                layers[lay["id"]] = res
            else:
                interp = cv2.INTER_NEAREST if s["georef"] == "frame" else cv2.INTER_CUBIC
                layers[lay["id"]] = F.warp(res, g, interp)
            print(f"  {lay['id']:<8} <- {s['file']} ({lay['kind']})")
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
                   .replace("{{PANEL}}", "".join(layer_block(L) for L in cfg["panel"]))
                   .replace("{{NOTES}}", notes))
    page = (page.replace("{{MAP_BG}}", bg)
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
