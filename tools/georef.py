#!/usr/bin/env python3
"""캡처 이미지의 웹 메르카토르 식(georef = [A, B, C, D])을 구한다.

  원본 픽셀 x = A * 경도(rad) + B,  y = C * 메르카토르Y + D

1) 기준점 방식 (Ventusky 처럼 도시 표시점이 있는 화면)
   python3 tools/georef.py points 캡처.png points.json
   points.json = [["London", 51.5074, -0.1278, 603, 262], ...]   (이름, 위도, 경도, 대략 x, 대략 y)
   대략 좌표 주변에서 남색 원(도시 표시점)의 중심을 찾아 최소제곱으로 맞춘다.

2) 해안선 정합 방식 (기준점이 없는 화면을 이미 맞춘 화면에 맞춤)
   python3 tools/georef.py align tools/configs/<회차>.json <대상 번호> <기준 번호> <대상 종류> <기준 종류>
   번호는 config 의 sources 순서(0부터), 종류는 fr24 | awc | ventusky.
   config 에 적힌 대상의 georef 를 초기값으로, 각 source 의 ui(가림 영역)를 빼고 바다/육지 마스크
   일치율이 최대가 되게 조정한다. 초기값은 도시 2곳의 대략 위치로 계산하면 충분하다 (배율 ±4%, 위치 ±50px).
"""
import json
import math
import sys

import cv2
import numpy as np

D = math.pi / 180


def merc(lat):
    return math.log(math.tan(math.pi / 4 + lat * D / 2))


def find_ring(img, x, y, r=14):
    w = img[y - r:y + r + 1, x - r:x + r + 1].astype(int)
    b, g, rr = w[..., 0], w[..., 1], w[..., 2]
    m = ((rr < 80) & (g < 90) & (b < 120) & (b >= rr)).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
    best = None
    for i in range(1, n):
        bx, by, bw, bh, _ = st[i]
        if 5 <= bw <= 14 and 5 <= bh <= 14 and abs(bw - bh) <= 3:
            cx, cy = bx + bw / 2 - 0.5, by + bh / 2 - 0.5
            d = (cx - r) ** 2 + (cy - r) ** 2
            if best is None or d < best[0]:
                best = (d, x - r + cx, y - r + cy)
    return None if best is None else best[1:]


def fit_points(img_path, pts_path):
    img = cv2.imread(img_path)
    pts = []
    for name, lat, lon, x, y in json.load(open(pts_path, encoding="utf-8")):
        c = find_ring(img, int(x), int(y))
        if c:
            pts.append((c[0], c[1], lon * D, merc(lat), name))
        else:
            print(f"  {name}: 표시점을 찾지 못함")
    X = np.array([[p[2], 1] for p in pts]); Y = np.array([[p[3], 1] for p in pts])
    A, B = np.linalg.lstsq(X, np.array([p[0] for p in pts]), rcond=None)[0]
    C, Dd = np.linalg.lstsq(Y, np.array([p[1] for p in pts]), rcond=None)[0]
    for x, y, lo, my, name in pts:
        print(f"  {name:<12} 오차 x {x - (A * lo + B):+5.1f}px  y {y - (C * my + Dd):+5.1f}px")
    print(json.dumps([round(v, 3) for v in (A, B, C, Dd)]))


def water_valid(kind, img):
    i = img.astype(int); b, g, r = i[..., 0], i[..., 1], i[..., 2]
    mx = np.maximum(np.maximum(r, g), b); mn = np.minimum(np.minimum(r, g), b); sat = mx - mn
    if kind == "fr24":
        water = (b > 190) & (g > 170) & (r < 175) & (b - r > 45); land = (sat < 40) & (mn > 205)
    elif kind == "awc":
        water = (sat < 12) & (mx >= 198) & (mx <= 220); land = (sat < 8) & (mn >= 232)
    else:  # ventusky
        water = (sat < 12) & (mx >= 65) & (mx <= 105); land = (sat < 12) & (mx >= 130) & (mx <= 180)
    return water.astype(np.uint8), (water | land).astype(np.uint8)


def align(cfg_path, ti_, ri_, tkind, rkind):
    from build_overlay import ROOT, Frame, ui_mask
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    T, R = cfg["sources"][ti_], cfg["sources"][ri_]
    ti, ri = cv2.imread(str(ROOT / T["file"])), cv2.imread(str(ROOT / R["file"]))
    rgeo = R["georef"]
    h, w = ri.shape[:2]                                   # 기준 화면이 덮는 경위도 범위로 좌표계를 잡는다
    lon = lambda x: (x - rgeo[1]) / rgeo[0] / D
    lat = lambda y: (2 * math.atan(math.exp((y - rgeo[3]) / rgeo[2])) - math.pi / 2) / D
    tw, tv = water_valid(tkind, ti); rw, rv = water_valid(rkind, ri)
    tv &= ui_mask(ti.shape, T.get("ui", [])) // 255
    rv &= ui_mask(ri.shape, R.get("ui", [])) // 255
    A0, B0, _, D0 = T["georef"]
    cur, s0 = (A0, B0, D0), 0
    for K, steps in [(250, [(0.02, 24), (0.01, 12), (0.005, 6), (0.0025, 3)]),
                     (900, [(0.001, 1), (0.0005, 0.5), (0.00025, 0.25)])]:
        F = Frame(K, lon(0), lon(w), lat(h), lat(0))
        RW = F.warp(rw, rgeo, cv2.INTER_NEAREST); RV = F.warp(rv, rgeo, cv2.INTER_NEAREST)

        def score(A, B, Dd):
            g = (A, B, -A, Dd)
            m = (F.warp(tv, g, cv2.INTER_NEAREST) & RV).astype(bool)
            return (F.warp(tw, g, cv2.INTER_NEAREST)[m] == RW[m]).mean() if m.sum() > 1000 else 0

        s0 = score(*cur)
        for ss, st in steps:
            imp = True
            while imp:
                imp = False
                for ds in (-1, 0, 1):
                    for db in (-1, 0, 1):
                        for dd in (-1, 0, 1):
                            if ds == db == dd == 0:
                                continue
                            c = (cur[0] * (1 + ds * ss), cur[1] + db * st, cur[2] + dd * st)
                            s = score(*c)
                            if s > s0 + 1e-6:
                                s0, cur, imp = s, c, True
    print(f"  바다/육지 일치율 {s0:.4f}")
    print(json.dumps([round(cur[0], 3), round(cur[1], 3), round(-cur[0], 3), round(cur[2], 3)]))


if __name__ == "__main__":
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
    a = sys.argv[1:]
    if a[:1] == ["points"] and len(a) == 3:
        fit_points(a[1], a[2])
    elif a[:1] == ["align"] and len(a) == 6:
        align(a[1], int(a[2]), int(a[3]), a[4], a[5])
    else:
        sys.exit(__doc__)
