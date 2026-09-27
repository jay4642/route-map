/* 항로 기상 중첩 지도: 제안 항로 표시 + 항로 그리기·거리 측정
 *
 * 지도 HTML 에는 </body> 앞에 이 파일을 부르는 script 태그 한 줄만 넣는다.
 * 투영 상수(W, H, K, LON0, Y0, OX, OY)는 지도 HTML 의 인라인 스크립트에서 그대로 읽어 window.MAP_PROJ 로 꺼낸다.
 * 제안 항로는 같은 폴더의 routes.json 에서 읽는다 (없으면 표시하지 않음).
 */
(function () {
  'use strict';
  var D = Math.PI / 180, R_KM = 6371.0088, NM_KM = 1.852, SEG_KM = 50;

  // ------------------------------------------------------------------ 투영
  function readProj() {
    var scripts = document.querySelectorAll('script:not([src])');
    for (var i = 0; i < scripts.length; i++) {
      var m = scripts[i].textContent.match(/const\s+W\s*=[^;]*;/);
      if (!m) continue;
      var o = {}, re = /\b(W|H|K|LON0|Y0|OX|OY)\s*=\s*(-?\d+(?:\.\d+)?(?:e[-+]?\d+)?)/g, r;
      while ((r = re.exec(m[0]))) o[r[1]] = parseFloat(r[2]);
      if (['W', 'H', 'K', 'LON0', 'Y0'].every(function (k) { return isFinite(o[k]); })) {
        o.OX = o.OX || 0; o.OY = o.OY || 0;         // 지도 스크립트에 없으면 0 (역변환식과 동일)
        return o;
      }
    }
    return null;
  }
  var P = readProj();
  var map = document.getElementById('map'), stack = document.getElementById('stack');
  var aside = document.querySelector('aside');
  if (!P || !map || !stack || !aside) return;

  function normLon(lon) { return P.LON0 + (((lon - P.LON0) % 360) + 360) % 360; }   // LON0 기준 0~360°
  function wrap180(lon) { return ((lon + 180) % 360 + 360) % 360 - 180; }
  function toXY(lat, lon) {                                      // 위경도 → 지도 픽셀
    return [(normLon(lon) - P.LON0) * D * P.K - P.OX,
            (P.Y0 - Math.log(Math.tan(Math.PI / 4 + lat * D / 2))) * P.K - P.OY];
  }
  function toLL(x, y) {                                          // 지도 픽셀 → 위경도 (지도 스크립트와 같은 식)
    var lon = (x + P.OX) / (P.K * D) + P.LON0;
    var lat = (2 * Math.atan(Math.exp(P.Y0 - (y + P.OY) / P.K)) - Math.PI / 2) / D;
    return [lat, wrap180(lon)];
  }
  window.MAP_PROJ = { W: P.W, H: P.H, K: P.K, LON0: P.LON0, Y0: P.Y0, OX: P.OX, OY: P.OY, toXY: toXY, toLL: toLL };

  // ------------------------------------------------------------------ 대권 계산
  function distKm(a, b) {                                         // 하버사인
    var p1 = a[0] * D, p2 = b[0] * D, dp = p2 - p1, dl = (b[1] - a[1]) * D;
    var h = Math.sin(dp / 2) * Math.sin(dp / 2) + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) * Math.sin(dl / 2);
    return 2 * R_KM * Math.asin(Math.min(1, Math.sqrt(h)));
  }
  function bearing(a, b) {
    var p1 = a[0] * D, p2 = b[0] * D, dl = (b[1] - a[1]) * D;
    var y = Math.sin(dl) * Math.cos(p2), x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
    return (Math.atan2(y, x) / D + 360) % 360;
  }
  function vec(p) { var f = p[0] * D, l = p[1] * D; return [Math.cos(f) * Math.cos(l), Math.cos(f) * Math.sin(l), Math.sin(f)]; }
  function gcPoints(a, b) {                                       // 50 km 이하 간격으로 쪼갠 대권 점들
    var dkm = distKm(a, b), d = dkm / R_KM, n = Math.max(1, Math.ceil(dkm / SEG_KM)), out = [];
    var va = vec(a), vb = vec(b);
    for (var i = 0; i <= n; i++) {
      var t = i / n;
      if (d < 1e-9) { out.push(a.slice()); continue; }
      var sa = Math.sin((1 - t) * d) / Math.sin(d), sb = Math.sin(t * d) / Math.sin(d);
      var q = [sa * va[0] + sb * vb[0], sa * va[1] + sb * vb[1], sa * va[2] + sb * vb[2]];
      out.push([Math.atan2(q[2], Math.hypot(q[0], q[1])) / D, Math.atan2(q[1], q[0]) / D]);
    }
    return out;
  }
  var PERIOD = 360 * D * P.K;
  function pathXY(lls) {                                          // 이웃 점과 가까운 쪽으로 이어 붙여 반대편으로 튀지 않게
    var out = [], prev = null;
    lls.forEach(function (ll) {
      var xy = toXY(ll[0], ll[1]);
      if (prev) xy[0] += Math.round((prev[0] - xy[0]) / PERIOD) * PERIOD;
      out.push(xy); prev = xy;
    });
    return out;
  }
  function routeLine(pts) {                                       // 웨이포인트들을 대권 곡선으로 이은 점 목록
    var all = [];
    for (var i = 0; i + 1 < pts.length; i++) {
      var g = gcPoints(pts[i], pts[i + 1]);
      all = all.concat(i ? g.slice(1) : g);
    }
    return all;
  }
  function totalKm(pts) { var s = 0; for (var i = 0; i + 1 < pts.length; i++) s += distKm(pts[i], pts[i + 1]); return s; }

  // 모든 점을 지나는 하나의 매끄러운 곡선 (구심 Catmull-Rom 을 지구 표면(3차원 단위벡터)에서 계산)
  // 반환: line = 50 km 이하 간격의 곡선 점들, segKm = 웨이포인트 사이 곡선 길이, start = 각 구간이 시작하는 line 인덱스
  function unit(v) { var n = Math.hypot(v[0], v[1], v[2]) || 1; return [v[0] / n, v[1] / n, v[2] / n]; }
  function toLatLon(q) { return [Math.atan2(q[2], Math.hypot(q[0], q[1])) / D, Math.atan2(q[1], q[0]) / D]; }
  function lerp3(a, b, ta, tb, t) {
    var w = tb - ta < 1e-12 ? 0 : (t - ta) / (tb - ta);
    return [a[0] + (b[0] - a[0]) * w, a[1] + (b[1] - a[1]) * w, a[2] + (b[2] - a[2]) * w];
  }
  function smoothCurve(pts) {
    var n = pts.length;
    if (n < 3) {                                                    // 두 점이면 대권 그대로
      var g = n === 2 ? gcPoints(pts[0], pts[1]) : pts.slice();
      return { line: g, segKm: n === 2 ? [distKm(pts[0], pts[1])] : [], start: [0, g.length - 1] };
    }
    var V = pts.map(vec);
    var e0 = unit([2 * V[0][0] - V[1][0], 2 * V[0][1] - V[1][1], 2 * V[0][2] - V[1][2]]);          // 양 끝 연장점
    var eN = unit([2 * V[n - 1][0] - V[n - 2][0], 2 * V[n - 1][1] - V[n - 2][1], 2 * V[n - 1][2] - V[n - 2][2]]);
    var line = [], segKm = [], start = [];
    for (var i = 0; i + 1 < n; i++) {
      var p0 = i ? V[i - 1] : e0, p1 = V[i], p2 = V[i + 1], p3 = i + 2 < n ? V[i + 2] : eN;
      var d = function (a, b) { return Math.pow(Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]), 0.5) || 1e-6; };
      var t0 = 0, t1 = t0 + d(p0, p1), t2 = t1 + d(p1, p2), t3 = t2 + d(p2, p3);
      var m = Math.max(2, Math.ceil(distKm(pts[i], pts[i + 1]) * 1.25 / SEG_KM)), seg, len;
      for (var tries = 0; tries < 4; tries++) {                     // 한 칸이 50 km 를 넘지 않을 때까지 촘촘히
        seg = []; len = 0; var maxStep = 0;
        for (var j = 0; j <= m; j++) {
          var t = t1 + (t2 - t1) * j / m;
          var A1 = lerp3(p0, p1, t0, t1, t), A2 = lerp3(p1, p2, t1, t2, t), A3 = lerp3(p2, p3, t2, t3, t);
          var B1 = lerp3(A1, A2, t0, t2, t), B2 = lerp3(A2, A3, t1, t3, t);
          var q = toLatLon(unit(lerp3(B1, B2, t1, t2, t)));
          if (j === 0) q = pts[i].slice(); if (j === m) q = pts[i + 1].slice();      // 웨이포인트를 정확히 지나게
          if (seg.length) { var k = distKm(seg[seg.length - 1], q); len += k; maxStep = Math.max(maxStep, k); }
          seg.push(q);
        }
        if (maxStep <= SEG_KM) break;
        m = Math.ceil(m * maxStep / SEG_KM) + 1;
      }
      start.push(line.length ? line.length - 1 : 0);
      line = line.concat(line.length ? seg.slice(1) : seg);
      segKm.push(len);
    }
    start.push(line.length - 1);
    return { line: line, segKm: segKm, start: start };
  }
  function curveKm(c) { return c.segKm.reduce(function (a, b) { return a + b; }, 0); }

  // ------------------------------------------------------------------ 표시 형식
  function comma(n) { return n.toLocaleString('en-US'); }
  function fmtDist(km) {
    var nm = km / NM_KM;
    if (nm < 10) return { nm: nm.toFixed(1), km: km.toFixed(1) };
    return { nm: comma(Math.round(nm)), km: comma(Math.round(km)) };
  }
  function fmtPair(km) { var f = fmtDist(km); return f.nm + ' NM · ' + f.km + ' km'; }
  function fmtLat(v) { return Math.abs(v).toFixed(2) + (v >= 0 ? 'N' : 'S'); }
  function fmtLon(v) { v = wrap180(v); return Math.abs(v).toFixed(2) + (v >= 0 ? 'E' : 'W'); }
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function pts2str(xy) { return xy.map(function (p) { return p[0].toFixed(1) + ',' + p[1].toFixed(1); }).join(' '); }
  var SC = 'scale(calc(1 / var(--s,1)))';                          // 확대 배율과 상관없이 화면에서 같은 크기
  function at(x, y) { return 'transform:translate(' + x.toFixed(1) + 'px,' + y.toFixed(1) + 'px) ' + SC; }

  // ------------------------------------------------------------------ 스타일
  var css = document.createElement('style');
  css.textContent = [
    '.rt-svg{position:absolute;inset:0;width:100%;height:100%;pointer-events:none;overflow:visible}',
    '.rt-svg text{font-family:inherit}',
    '#rt-draw .wp{pointer-events:none}',
    '.rt-drawing #rt-draw .wp{pointer-events:auto;cursor:move}',
    '.rt-drawing{cursor:crosshair!important}',
    '.rt-drawing.drag{cursor:grabbing!important}',
    '#rt-btn svg{width:20px;height:20px}',
    '.rt-status{left:12px;top:58px;padding:6px 12px;font-size:12.5px;display:none;max-width:calc(100% - 76px);z-index:5}',
    '.rt-drawing .rt-status,.rt-noting .rt-status{display:block}',
    '.rt-noting{cursor:copy!important}',
    '.rt-note{pointer-events:auto;cursor:pointer}',
    '.rt-note:hover rect{filter:brightness(1.15)}',
    '.rt-pop{position:absolute;z-index:6;width:250px;transform-origin:0 0;transform:scale(calc(1 / var(--s,1))) translate(-50%, calc(-100% - 38px));',
    '  background:var(--panel);color:var(--ink);border:1px solid var(--rule);border-radius:10px;box-shadow:0 8px 24px rgba(0,0,0,.25);padding:10px;font-size:12.5px;cursor:auto}',
    '.rt-pop[hidden]{display:none}',
    '.rt-pop b{display:flex;align-items:center;gap:4px;margin-bottom:6px}',
    '.rt-pop small{color:var(--muted);font-weight:400;margin-left:auto}',
    '.rt-pop textarea{width:100%;box-sizing:border-box;min-height:74px;resize:vertical;font:inherit;font-size:13px;color:var(--ink);background:var(--paper);border:1px solid var(--rule);border-radius:7px;padding:6px 8px}',
    '.rt-pop textarea:focus{outline:2px solid var(--magenta);outline-offset:0}',
    '.rt-pop .rt-btns{margin:8px 0 0}',
    '.rt-pop .rt-btns .pri{background:var(--magenta);border-color:var(--magenta);color:#fff;font-weight:600}',
    '.rt-notes{list-style:none;margin:6px 0 0;padding:0;display:flex;flex-direction:column;gap:4px}',
    '.rt-notes li{display:flex;gap:6px;align-items:flex-start;font-size:12px;line-height:1.4}',
    '.rt-notes li span{color:var(--ink)!important;white-space:pre-line;overflow-wrap:anywhere}',
    '.rt-notes li span.empty{color:var(--muted)!important;font-style:italic}',
    '.rt-nbtn{flex:none;font:inherit;font-size:11px;font-weight:700;color:#fff;border:0;border-radius:9px;padding:1px 7px;cursor:pointer}',
    '.rt-status b{color:#D9480F}',
    '.rt-flash::after{content:"";position:absolute;inset:0;background:#fff;opacity:.6;pointer-events:none;z-index:20}',
    '#rt-cap-btn[aria-busy] svg,#rt-note-btn svg{width:20px;height:20px}',
    '.rt-sec{border-top:1px solid var(--rule);padding:12px 0}',
    '.rt-sec h2{font-size:14px;font-weight:600;margin:0 0 6px;display:flex;align-items:center;gap:8px}',
    '.rt-sec h2 label{flex:1;cursor:pointer;display:flex;align-items:center;gap:10px}',
    '.rt-sec input[type=checkbox]{width:16px;height:16px;accent-color:var(--magenta);margin:0}',
    '.rt-route{display:flex;gap:8px;align-items:flex-start;margin:8px 0 0;font-size:12.5px}',
    '.rt-route > div{flex:1;min-width:0}',
    '.rt-route input{margin-top:3px!important}',
    '.rt-badge{display:inline-grid;place-items:center;min-width:18px;height:18px;padding:0 4px;border-radius:5px;color:#fff;font-size:11px;font-weight:700;margin-right:4px}',
    '.rt-del{flex:none;font:inherit;font-size:12px;min-height:28px;padding:0 9px;border-radius:7px;border:1px solid var(--rule);background:transparent;color:var(--ink);cursor:pointer}',
    '.rt-del:hover{border-color:#B42318;color:#B42318}',
    '.rt-link{font:inherit;font-size:12px;color:var(--magenta);background:none;border:0;padding:0;cursor:pointer;text-decoration:underline}',
    '.rt-route .ln{flex:none;width:26px;height:0;margin-top:9px;border-top:3px solid}',
    '.rt-route .ln.dash{border-top-style:dashed}',
    '.rt-route b{font-weight:600}',
    '.rt-route span{color:var(--muted)}',
    '.rt-route .rt-badge{color:#fff}',
    '.rt-help{font-size:12px;color:var(--muted);margin:4px 0 8px}',
    '.rt-btns{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}',
    '.rt-btns button{font:inherit;font-size:12.5px;min-height:32px;padding:0 10px;border-radius:7px;border:1px solid var(--rule);background:transparent;color:var(--ink);cursor:pointer}',
    '.rt-btns button.on{background:#D9480F;border-color:#D9480F;color:#fff;font-weight:600}',
    '.rt-btns button:disabled{opacity:.45;cursor:default}',
    '.rt-table{width:100%;table-layout:fixed;border-collapse:collapse;font-size:11.5px;line-height:1.35}',
    '.rt-table th,.rt-table td{padding:4px 2px;border-bottom:1px solid var(--rule);text-align:right;vertical-align:top;overflow-wrap:anywhere}',
    '.rt-table th{font-weight:600;color:var(--muted);font-size:11px}',
    '.rt-table th:first-child,.rt-table td:first-child{text-align:center}',
    '.rt-table col.c1{width:7%} .rt-table col.c2{width:16%} .rt-table col.c3{width:18%} .rt-table col.c4{width:21%} .rt-table col.c5{width:23%} .rt-table col.c6{width:15%}',
    '.rt-table tfoot td{font-weight:600;border-bottom:0;padding-top:6px}',
    '.rt-empty{font-size:12px;color:var(--muted);padding:6px 0}'
  ].join('\n');
  document.head.appendChild(css);

  function svgLayer(id) {
    var s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    s.setAttribute('id', id); s.setAttribute('class', 'rt-svg'); s.setAttribute('aria-hidden', 'true');
    s.setAttribute('viewBox', '0 0 ' + P.W + ' ' + P.H);
    stack.appendChild(s);
    return s;
  }
  var entryId = (document.getElementById('route-map-archive-nav') || {}).dataset;
  entryId = (entryId && entryId.entry) || (location.pathname.match(/entries\/([^/]+)/) || [])[1] || location.pathname;
  var KEY = 'route-tools:' + entryId;

  // ------------------------------------------------------------------ 1. 제안 항로
  var sugSvg = svgLayer('rt-suggest'), drawSvg = svgLayer('rt-draw');
  var notes = aside.querySelector('.notes');
  function insertSection(el) { aside.insertBefore(el, notes || null); }

  // ------------------------------------------------------------------ 2. 항로 그리기·거리 측정
  var pts = [];                                                    // [[lat, lon], ...]
  try { var saved = JSON.parse(localStorage.getItem(KEY) || 'null'); if (Array.isArray(saved)) pts = saved.filter(validPt); } catch (e) {}
  function validPt(p) { return Array.isArray(p) && p.length >= 2 && isFinite(p[0]) && isFinite(p[1]) && Math.abs(p[0]) <= 85; }
  function save() { try { localStorage.setItem(KEY, JSON.stringify(pts)); } catch (e) {} }

  var linesG = document.createElementNS('http://www.w3.org/2000/svg', 'g'), wpsG = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  drawSvg.appendChild(linesG); drawSvg.appendChild(wpsG);
  var DRAW = '#D9480F';

  var curve = smoothCurve([]);
  function renderLines() {
    curve = smoothCurve(pts);
    linesG.innerHTML = pts.length > 1 ? curveSvg(curve.line, DRAW, false, '총 ' + fmtPair(curveKm(curve))) : '';
  }
  function curveSvg(line, color, dash, endLabel, badge, yOff) {       // 곡선 + 끝점 라벨(전체 거리 한 번)
    var xy = pathXY(line), str = pts2str(xy);
    var ds = dash ? ';stroke-dasharray:calc(10px / var(--s,1)) calc(6px / var(--s,1))' : '';
    var h = '<polyline points="' + str + '" fill="none" stroke="#fff" stroke-opacity=".8" stroke-linejoin="round" stroke-linecap="round" style="stroke-width:calc(5.5px / var(--s,1))' + ds + '"/>' +
            '<polyline points="' + str + '" fill="none" stroke="' + esc(color) + '" stroke-linejoin="round" stroke-linecap="round" style="stroke-width:calc(2.8px / var(--s,1))' + ds + '"/>';
    var e = xy[xy.length - 1], pv = xy[Math.max(0, xy.length - 4)];
    var right = e[0] >= pv[0];                                      // 곡선이 끝나는 방향 바깥쪽에 라벨
    if (e[0] > P.W * 0.72) right = false;                           // 지도 가장자리 근처면 안쪽으로
    else if (e[0] < P.W * 0.28) right = true;
    h += '<g style="' + at(e[0], e[1]) + '"><text x="' + (right ? 10 : -10) + '" y="' + (16 + (yOff || 0)) + '" text-anchor="' + (right ? 'start' : 'end') +
         '" font-size="12" font-weight="700" fill="' + esc(color) + '" stroke="#fff" stroke-width="3.4" paint-order="stroke">' +
         (badge ? esc(badge) + ' · ' : '') + endLabel + '</text></g>';
    return h;
  }
  function wpTransform(i) { var xy = toXY(pts[i][0], pts[i][1]); return at(xy[0], xy[1]); }
  function renderWps() {
    wpsG.innerHTML = pts.map(function (p, i) {
      return '<g class="wp" data-i="' + i + '" style="' + wpTransform(i) + '">' +
        '<circle r="12" fill="transparent"/><circle r="5.5" fill="#fff" stroke="' + DRAW + '" stroke-width="2.6"/>' +
        '<text x="8" y="-7" font-size="11" font-weight="700" fill="' + DRAW + '" stroke="#fff" stroke-width="3" paint-order="stroke">' + (i + 1) + '</text></g>';
    }).join('');
  }
  function renderAll() { renderLines(); renderWps(); renderTable(); save(); }

  // 패널: 항로 그리기 섹션
  var sec = document.createElement('div');
  sec.className = 'rt-sec';
  sec.id = 'rt-panel';
  sec.innerHTML = '<h2>항로 그리기·거리 측정</h2>' +
    '<p class="rt-help">오른쪽 위 연필 버튼(또는 <kbd>M</kbd>)으로 켜고 지도를 눌러 점을 찍으면, 모든 점을 지나는 하나의 곡선으로 이어집니다. 점은 끌어서 옮기고, 우클릭(모바일은 길게 누르기)으로 지웁니다. <kbd>Backspace</kbd> 마지막 점 취소 · <kbd>Esc</kbd> 그리기 끝. 다 그리면 <b>제안 항로로 추가</b>로 A·B·C… 항로로 저장합니다.</p>' +
    '<div class="rt-btns"><button type="button" id="rt-toggle2">그리기 켜기</button><button type="button" id="rt-clear">전체 지우기</button>' +
    '<button type="button" id="rt-save">제안 항로로 추가</button><button type="button" id="rt-json">JSON 내보내기</button><button type="button" id="rt-csv">CSV 내보내기</button>' +
    '<button type="button" id="rt-load">JSON 불러오기</button><input type="file" id="rt-file" accept=".json,application/json" hidden></div>' +
    '<div id="rt-table"></div>';
  insertSection(sec);

  function rows() {                                               // 구간 거리·누적은 그려진 곡선을 따라 잰 길이
    var out = [], cum = 0, c = smoothCurve(pts);
    pts.forEach(function (p, i) {
      var seg = i ? c.segKm[i - 1] : null, s0 = i ? c.start[i - 1] : 0;
      if (seg != null) cum += seg;
      out.push({ n: i + 1, lat: p[0], lon: wrap180(p[1]), seg: seg, cum: i ? cum : null,
                 brg: i ? bearing(c.line[s0], c.line[s0 + 1]) : null });
    });
    return out;
  }
  function renderTable() {
    var box = document.getElementById('rt-table');
    if (!pts.length) { box.innerHTML = '<div class="rt-empty">아직 찍은 점이 없습니다.</div>'; updateStatus(); syncButtons(); return; }
    var rs = rows(), two = function (km) { if (km == null) return '–'; var f = fmtDist(km); return f.nm + ' NM<br>' + f.km + ' km'; };
    box.innerHTML = '<table class="rt-table"><colgroup><col class="c1"><col class="c2"><col class="c3"><col class="c4"><col class="c5"><col class="c6"></colgroup>' +
      '<thead><tr><th>#</th><th>위도</th><th>경도</th><th>구간</th><th>누적</th><th>초기 방위</th></tr></thead><tbody>' +
      rs.map(function (r) {
        return '<tr><td>' + r.n + '</td><td>' + fmtLat(r.lat) + '</td><td>' + fmtLon(r.lon) + '</td><td>' + two(r.seg) + '</td><td>' + two(r.cum) +
          '</td><td>' + (r.brg == null ? '–' : String(Math.round(r.brg) % 360).padStart(3, '0') + '°') + '</td></tr>';
      }).join('') +
      '</tbody><tfoot><tr><td colspan="3" style="text-align:left">총거리 (' + pts.length + '점)</td><td colspan="3">' + fmtPair(curveKm(smoothCurve(pts))) + '</td></tr></tfoot></table>';
    updateStatus(); syncButtons();
  }

  // 지도 위: 토글 버튼과 상태 표시
  var zoom = map.querySelector('.zoom');
  var oldMeas = document.getElementById('zmeas');                  // 기존 두 점 측정 버튼은 새 도구로 대체
  if (oldMeas) oldMeas.style.display = 'none';
  var btn = document.createElement('button');
  btn.id = 'rt-btn'; btn.type = 'button'; btn.setAttribute('aria-pressed', 'false');
  btn.setAttribute('aria-label', '항로 그리기'); btn.title = '항로 그리기·거리 측정 (M)';
  btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20l4.5-1 10-10-3.5-3.5-10 10z"/><path d="M13.5 6.5l3.5 3.5"/><circle cx="4" cy="20" r="1.2" fill="currentColor"/></svg>';
  if (zoom) zoom.appendChild(btn);
  var status = document.createElement('div');
  status.className = 'float rt-status'; status.setAttribute('aria-live', 'polite');
  map.appendChild(status);
  var keys = aside.querySelector('.keys');
  if (keys) keys.innerHTML = keys.innerHTML.replace(/<kbd>M<\/kbd>[^·]*·\s*<kbd>Esc<\/kbd>[^<]*/,
    '<kbd>M</kbd> 항로 그리기 · <kbd>N</kbd> 항로 메모 · <kbd>Backspace</kbd> 마지막 점 취소 · <kbd>Esc</kbd> 끝내기');

  var drawing = false, noting = false;
  function setDrawing(on) {
    drawing = on;
    if (on && noting) setNoting(false);
    map.classList.toggle('rt-drawing', on);
    btn.setAttribute('aria-pressed', on);
    syncButtons(); updateStatus();
  }
  function syncButtons() {
    var t = document.getElementById('rt-toggle2');
    if (t) { t.textContent = drawing ? '그리기 끄기' : '그리기 켜기'; t.classList.toggle('on', drawing); }
    ['rt-clear', 'rt-json', 'rt-csv'].forEach(function (id) { var b = document.getElementById(id); if (b) b.disabled = !pts.length; });
    var sv = document.getElementById('rt-save'); if (sv) sv.disabled = pts.length < 2;
  }
  function updateStatus() {
    if (noting) { status.innerHTML = '<b>메모 달기</b> · 제안 항로 선 위를 누르면 메모 칸이 열립니다 · <kbd>Esc</kbd> 끝'; return; }
    status.innerHTML = '<b>항로 그리기</b> · 지도를 눌러 점 추가 · ' + pts.length + '점' + (pts.length > 1 ? ' · 총 ' + fmtPair(curveKm(smoothCurve(pts))) : '');
  }
  btn.addEventListener('click', function () { setDrawing(!drawing); });
  document.getElementById('rt-toggle2').onclick = function () { setDrawing(!drawing); };
  document.getElementById('rt-clear').onclick = function () {
    if (!pts.length) return;
    if (window.confirm('찍은 점 ' + pts.length + '개를 모두 지울까요?')) { pts = []; renderAll(); }
  };

  // 화면 좌표 → 지도 픽셀 (확대·이동 상태는 .stack 의 실제 위치로 계산)
  function clientToMap(cx, cy) {
    var r = stack.getBoundingClientRect(), s = r.width / P.W;
    return [(cx - r.left) / s, (cy - r.top) / s];
  }
  function clientToLL(cx, cy) { var m = clientToMap(cx, cy); return toLL(m[0], m[1]); }

  // 짧은 클릭/탭(이동 5px 미만)만 점 추가, 그 이상은 지도 이동
  var down = null, active = {}, nActive = 0;
  map.addEventListener('pointerdown', function (e) {
    if (!(e.pointerId in active)) { active[e.pointerId] = 1; nActive++; }
    if (!(drawing || noting) || e.button > 0 || nActive > 1 || e.target.closest('button,.chips,.readout,.zoom,.rt-status,.wp,.rt-note,.rt-pop')) { down = null; return; }
    down = { id: e.pointerId, x: e.clientX, y: e.clientY, moved: false };
  });
  map.addEventListener('pointermove', function (e) {
    if (down && e.pointerId === down.id && Math.hypot(e.clientX - down.x, e.clientY - down.y) >= 5) down.moved = true;
  });
  function release(e) {
    if (e.pointerId in active) { delete active[e.pointerId]; nActive--; }
    if (e.type === 'pointerup' && down && e.pointerId === down.id && !down.moved && noting) addNoteAt(e.clientX, e.clientY);
    else if (e.type === 'pointerup' && down && e.pointerId === down.id && !down.moved && drawing) {
      var ll = clientToLL(e.clientX, e.clientY);
      if (Math.abs(ll[0]) <= 85) { pts.push(ll); renderAll(); }
    }
    if (down && e.pointerId === down.id) down = null;
  }
  map.addEventListener('pointerup', release);
  map.addEventListener('pointercancel', release);

  // 점 끌기 · 우클릭/길게 눌러 삭제
  var wdrag = null, lastDelete = 0;
  function removePoint(i) { if (i >= 0 && i < pts.length) { pts.splice(i, 1); lastDelete = Date.now(); renderAll(); } }
  wpsG.addEventListener('pointerdown', function (e) {
    var g = e.target.closest('.wp'); if (!g || !drawing) return;
    e.stopPropagation(); e.preventDefault();
    if (e.button === 2) return;                                     // 우클릭은 contextmenu 에서 삭제
    var i = +g.dataset.i;
    wdrag = { g: g, i: i, id: e.pointerId, x: e.clientX, y: e.clientY, moved: false, timer: null };
    if (e.pointerType !== 'mouse') wdrag.timer = setTimeout(function () { var d = wdrag; wdrag = null; if (d) removePoint(d.i); }, 600);
    try { g.setPointerCapture(e.pointerId); } catch (_) {}
  });
  wpsG.addEventListener('pointermove', function (e) {
    if (!wdrag || e.pointerId !== wdrag.id) return;
    if (!wdrag.moved && Math.hypot(e.clientX - wdrag.x, e.clientY - wdrag.y) < 5) return;
    if (!wdrag.moved) { wdrag.moved = true; clearTimeout(wdrag.timer); }
    var ll = clientToLL(e.clientX, e.clientY);
    if (Math.abs(ll[0]) > 85) return;
    pts[wdrag.i] = ll;
    wdrag.g.setAttribute('style', wpTransform(wdrag.i));
    renderLines(); renderTable();
  });
  function wpEnd(e) {
    if (!wdrag || e.pointerId !== wdrag.id) return;
    clearTimeout(wdrag.timer);
    var moved = wdrag.moved; wdrag = null;
    if (moved) { save(); renderWps(); }
  }
  wpsG.addEventListener('pointerup', wpEnd);
  wpsG.addEventListener('pointercancel', wpEnd);
  wpsG.addEventListener('contextmenu', function (e) {
    var g = e.target.closest('.wp'); if (!g) return;
    e.preventDefault(); e.stopPropagation();
    if (Date.now() - lastDelete < 800) return;                      // 길게 누르기로 이미 지운 경우
    removePoint(+g.dataset.i);
  });

  // 단축키: 지도 스크립트보다 먼저 받아 M·Backspace·Esc 를 처리
  window.addEventListener('keydown', function (e) {
    var t = e.target;
    if ((t instanceof Element && t.closest('input,textarea,select,[contenteditable]')) || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === 'Escape' && !pop.hidden) { closePop(); }
    else if (e.key === 'm' || e.key === 'M') { setDrawing(!drawing); }
    else if (e.key === 'n' || e.key === 'N') { setNoting(!noting); }
    else if (e.key === 'Escape' && noting) { setNoting(false); }
    else if (e.key === 'Backspace' && drawing) { if (pts.length) { pts.pop(); renderAll(); } }
    else if (e.key === 'Escape' && drawing) { setDrawing(false); }
    else return;
    e.preventDefault(); e.stopImmediatePropagation();
  }, true);

  // 내보내기·불러오기
  function download(name, text, type) {
    var a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type: type }));
    a.download = name; document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  }
  var base = 'route_' + String(entryId).replace(/[^\w-]+/g, '_');
  document.getElementById('rt-json').onclick = function () {
    var rs = rows();
    download(base + '.json', JSON.stringify(rs.map(function (r) {
      return { n: r.n, lat: +r.lat.toFixed(5), lon: +r.lon.toFixed(5) };
    }), null, 2) + '\n', 'application/json');
  };
  document.getElementById('rt-csv').onclick = function () {
    var rs = rows(), f = function (v, d) { return v == null ? '' : v.toFixed(d); };
    var lines = ['번호,위도,경도,구간_NM,구간_km,누적_NM,누적_km,초기방위_deg'].concat(rs.map(function (r) {
      return [r.n, r.lat.toFixed(5), r.lon.toFixed(5), f(r.seg && r.seg / NM_KM, 1), f(r.seg, 1), f(r.cum && r.cum / NM_KM, 1), f(r.cum, 1), f(r.brg, 1)].join(',');
    }));
    download(base + '.csv', '﻿' + lines.join('\r\n') + '\r\n', 'text/csv');
  };
  var file = document.getElementById('rt-file');
  document.getElementById('rt-load').onclick = function () { file.value = ''; file.click(); };
  file.addEventListener('change', function () {
    var f = file.files && file.files[0]; if (!f) return;
    var rd = new FileReader();
    rd.onload = function () {
      try {
        var v = JSON.parse(rd.result);
        if (v && !Array.isArray(v)) v = v.waypoints || v.points;
        var np = (v || []).map(function (p) {
          return Array.isArray(p) ? [+p[0], +p[1]] : [+(p.lat != null ? p.lat : p.latitude), +(p.lon != null ? p.lon : (p.lng != null ? p.lng : p.longitude))];
        }).filter(validPt);
        if (!np.length) throw new Error('empty');
        pts = np; renderAll();
      } catch (err) { window.alert('웨이포인트 JSON을 읽지 못했습니다. [[위도, 경도], ...] 또는 [{"lat":..,"lon":..}, ...] 형식이어야 합니다.'); }
    };
    rd.readAsText(f);
  });

  // ------------------------------------------------------------------ 1. 제안 항로 A·B·C·D… (추가·켜기/끄기·삭제)
  var RKEY = KEY + ':routes', LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
  var PALETTE = ['#6B2FB3', '#1F4E8C', '#0F8B6C', '#C2410C', '#B8860B', '#BE185D', '#0E7490', '#4D7C0F'];
  var baseRoutes = [], rs = { user: [], removed: [], hidden: [], notes: {} };
  try {
    var rv = JSON.parse(localStorage.getItem(RKEY) || 'null');
    if (rv && typeof rv === 'object') {
      rs.user = (rv.user || []).filter(function (r) { return r && r.id && Array.isArray(r.points) && r.points.filter(validPt).length > 1; });
      rs.removed = Array.isArray(rv.removed) ? rv.removed : [];
      rs.hidden = Array.isArray(rv.hidden) ? rv.hidden : [];
      rs.notes = rv.notes && typeof rv.notes === 'object' ? rv.notes : {};
      Object.keys(rs.notes).forEach(function (id) { rs.notes[id] = (rs.notes[id] || []).filter(function (n) { return n && n.text && isFinite(n.lat) && isFinite(n.lon); }); });
    }
  } catch (e) {}
  function saveRoutes() {                                         // 이유를 적지 않은 빈 메모는 저장하지 않는다
    var notes = {};
    Object.keys(rs.notes).forEach(function (id) {
      var ns = (rs.notes[id] || []).filter(function (n) { return n && n.text; });
      if (ns.length) notes[id] = ns;
    });
    try { localStorage.setItem(RKEY, JSON.stringify({ user: rs.user, removed: rs.removed, hidden: rs.hidden, notes: notes })); } catch (e) {}
  }
  function allRoutes() {
    return baseRoutes.filter(function (r) { return rs.removed.indexOf(r.id) < 0; }).concat(rs.user)
      .sort(function (a, b) { return a.id.length - b.id.length || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0); });
  }
  function nextLetter() {                                         // 비어 있는 첫 글자 (지운 기본 항로 글자는 복원을 위해 비워 둠)
    var used = baseRoutes.map(function (r) { return r.id; }).concat(rs.user.map(function (r) { return r.id; }));
    for (var i = 0; i < LETTERS.length; i++) if (used.indexOf(LETTERS[i]) < 0) return LETTERS[i];
    for (var k = 1; ; k++) if (used.indexOf('R' + k) < 0) return 'R' + k;
  }
  function colorOf(id) { var i = LETTERS.indexOf(id); return PALETTE[(i < 0 ? 0 : i) % PALETTE.length]; }
  function routeLL(r) {
    if (r.great_circle) return gcPoints(r.great_circle[0], r.great_circle[1]);
    return r.smooth ? smoothCurve(r.points).line : routeLine(r.points);
  }
  function routeKm(r) {
    if (r.great_circle) return distKm(r.great_circle[0], r.great_circle[1]);
    return r.smooth ? curveKm(smoothCurve(r.points)) : totalKm(r.points);
  }

  var sugSec = document.createElement('div');
  sugSec.className = 'rt-sec'; sugSec.id = 'rt-sug';
  sugSec.innerHTML = '<h2><label><input type="checkbox" id="rt-sug-on" checked>제안 항로</label></h2><div id="rt-sug-list"></div>';
  aside.insertBefore(sugSec, aside.querySelector('.layer') || notes || null);
  var sugOn = sugSec.querySelector('#rt-sug-on'), sugChip = null, chipsBox = document.getElementById('chips');
  if (chipsBox) {
    sugChip = document.createElement('button');
    sugChip.type = 'button'; sugChip.setAttribute('aria-pressed', 'true'); sugChip.title = '제안 항로 전체 켜기·끄기';
    sugChip.innerHTML = '<span class="d" style="background:#6B2FB3"></span>제안 항로';
    sugChip.onclick = function () { sugOn.checked = !sugOn.checked; sugOn.dispatchEvent(new Event('change')); };
    chipsBox.insertBefore(sugChip, chipsBox.firstChild);
  }
  sugOn.addEventListener('change', function () {
    sugSvg.style.display = sugOn.checked ? '' : 'none';
    if (sugChip) sugChip.setAttribute('aria-pressed', sugOn.checked);
  });

  function renderRoutes() {
    var list = allRoutes(), shown = list.filter(function (r) { return rs.hidden.indexOf(r.id) < 0; });
    // 같은 곳에서 끝나는 항로 라벨이 겹치지 않게 순서대로 한 줄씩 내림
    sugSvg.innerHTML = shown.map(function (r, k) {
      return curveSvg(routeLL(r), r.color || colorOf(r.id), r.style === 'dash', fmtPair(routeKm(r)), r.id, k * 16);
    }).join('') + shown.map(noteMarkers).join('');
    var box = document.getElementById('rt-sug-list');
    box.innerHTML = (list.length ? list.map(function (r) {
      var c = r.color || colorOf(r.id), on = rs.hidden.indexOf(r.id) < 0;
      return '<div class="rt-route"><input type="checkbox" data-show="' + esc(r.id) + '"' + (on ? ' checked' : '') + ' aria-label="' + esc(r.id) + ' 표시">' +
        '<i class="ln' + (r.style === 'dash' ? ' dash' : '') + '" style="border-color:' + esc(c) + '"></i>' +
        '<div><b><span class="rt-badge" style="background:' + esc(c) + '">' + esc(r.id) + '</span>' + esc(r.name) + '</b><br>총 ' + fmtPair(routeKm(r)) +
        (r.smooth ? ' <span>(' + r.points.length + '점 곡선)</span>' : '') + noteList(r) + '</div>' +
        '<button type="button" class="rt-del" data-del="' + esc(r.id) + '" aria-label="' + esc(r.id) + ' ' + esc(r.name) + ' 삭제">삭제</button></div>';
    }).join('') : '<div class="rt-empty">제안 항로가 없습니다. 아래에서 항로를 그린 뒤 <b>제안 항로로 추가</b>를 누르세요.</div>') +
      (rs.removed.some(function (id) { return baseRoutes.some(function (r) { return r.id === id; }); })
        ? '<p class="rt-help"><button type="button" class="rt-link" id="rt-restore">삭제한 기본 항로 되살리기</button></p>' : '');
    var rb = document.getElementById('rt-restore');
    if (rb) rb.onclick = function () { rs.removed = []; saveRoutes(); renderRoutes(); };
  }
  sugSec.addEventListener('change', function (e) {
    var id = e.target.getAttribute && e.target.getAttribute('data-show'); if (!id) return;
    rs.hidden = rs.hidden.filter(function (x) { return x !== id; });
    if (!e.target.checked) rs.hidden.push(id);
    saveRoutes(); renderRoutes();
  });
  sugSec.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('[data-del]'); if (!b) return;
    var id = b.getAttribute('data-del'), r = allRoutes().filter(function (x) { return x.id === id; })[0]; if (!r) return;
    if (!window.confirm('제안 항로 ' + id + ' (' + r.name + ')를 삭제할까요?')) return;
    if (rs.user.some(function (x) { return x.id === id; })) rs.user = rs.user.filter(function (x) { return x.id !== id; });
    else rs.removed.push(id);
    delete rs.notes[id]; if (pop.rid === id) closePop();
    rs.hidden = rs.hidden.filter(function (x) { return x !== id; });
    saveRoutes(); renderRoutes();
  });
  document.getElementById('rt-save').onclick = function () {
    if (pts.length < 2) return;
    var id = nextLetter(), name = window.prompt('제안 항로 ' + id + '의 이름', '제안 항로 ' + id);
    if (name === null) return;                                     // 취소
    rs.user.push({ id: id, name: (name || '제안 항로 ' + id).slice(0, 40), color: colorOf(id), style: 'solid', smooth: true,
                   points: pts.map(function (p) { return [+p[0].toFixed(5), +p[1].toFixed(5)]; }) });
    rs.hidden = rs.hidden.filter(function (x) { return x !== id; });
    saveRoutes(); pts = []; renderAll(); renderRoutes();
    if (!sugOn.checked) { sugOn.checked = true; sugOn.dispatchEvent(new Event('change')); }
  };

  // ------------------------------------------------------------------ 3. 제안 항로 메모 (선 위의 점 + 이유 적는 칸)
  var pop = document.createElement('div');
  pop.className = 'rt-pop'; pop.hidden = true; pop.setAttribute('role', 'dialog'); pop.setAttribute('aria-label', '항로 메모');
  stack.appendChild(pop);
  ['pointerdown', 'wheel', 'dblclick'].forEach(function (t) { pop.addEventListener(t, function (e) { e.stopPropagation(); }); });
  function notesOf(id) { return Array.isArray(rs.notes[id]) ? rs.notes[id] : (rs.notes[id] = []); }
  function routeById(id) { return allRoutes().filter(function (r) { return r.id === id; })[0]; }
  function noteXY(r, n) {                                           // 항로 선이 이어 붙여진 쪽(±360°)에 맞춰 위치 계산
    var xy = toXY(n.lat, n.lon), line = pathXY(routeLL(r)), best = xy, bd = Infinity;
    [-PERIOD, 0, PERIOD].forEach(function (sh) {
      for (var i = 0; i < line.length; i += 4) {
        var d = Math.hypot(line[i][0] - xy[0] - sh, line[i][1] - xy[1]);
        if (d < bd) { bd = d; best = [xy[0] + sh, xy[1]]; }
      }
    });
    return best;
  }
  function noteMarkers(r) {
    var c = r.color || colorOf(r.id);
    return notesOf(r.id).map(function (n, k) {
      var xy = noteXY(r, n), tag = r.id + (k + 1), w = 10 + tag.length * 7.5;
      return '<g class="rt-note" data-r="' + esc(r.id) + '" data-k="' + k + '" style="' + at(xy[0], xy[1]) + '"><title>' + esc(tag + ': ' + (n.text || '(빈 메모)')) + '</title>' +
        '<circle r="4" fill="#fff" stroke="' + esc(c) + '" stroke-width="2.4"/>' +
        '<path d="M-5,-13 L0,-5 L5,-13 Z" fill="' + esc(c) + '"/>' +
        '<rect x="' + (-w / 2) + '" y="-31" width="' + w + '" height="19" rx="9.5" fill="' + esc(c) + '" stroke="#fff" stroke-width="1.5"/>' +
        '<text y="-17.5" text-anchor="middle" font-size="11.5" font-weight="700" fill="#fff">' + esc(tag) + '</text>' +
        (n.text ? '' : '<circle cx="' + (w / 2 - 1) + '" cy="-29" r="3.5" fill="#fff" stroke="' + esc(c) + '" stroke-width="1.5"/>') + '</g>';
    }).join('');
  }
  function noteList(r) {
    var ns = notesOf(r.id); if (!ns.length) return '';
    var c = r.color || colorOf(r.id);
    return '<ol class="rt-notes">' + ns.map(function (n, k) {
      return '<li><button type="button" class="rt-nbtn" style="background:' + esc(c) + '" data-note="' + esc(r.id) + '|' + k + '" title="지도에서 메모 열기">' + esc(r.id + (k + 1)) + '</button>' +
        (n.text ? '<span>' + esc(n.text) + '</span>' : '<span class="empty">(아직 이유를 적지 않음)</span>') + '</li>';
    }).join('') + '</ol>';
  }
  function setNoting(on) {
    noting = on;
    if (on && drawing) setDrawing(false);
    map.classList.toggle('rt-noting', on);
    noteBtn.setAttribute('aria-pressed', on);
    if (on && !sugOn.checked) { sugOn.checked = true; sugOn.dispatchEvent(new Event('change')); }
    updateStatus();
  }
  function flashStatus(msg) {
    status.innerHTML = msg; clearTimeout(flashStatus.t);
    flashStatus.t = setTimeout(updateStatus, 2200);
  }
  function addNoteAt(cx, cy) {
    if (!sugOn.checked) return;
    var m = clientToMap(cx, cy), sc = stack.getBoundingClientRect().width / P.W, best = null;
    allRoutes().filter(function (r) { return rs.hidden.indexOf(r.id) < 0; }).forEach(function (r) {
      var line = pathXY(routeLL(r));
      [-PERIOD, 0, PERIOD].forEach(function (sh) {
        for (var i = 0; i + 1 < line.length; i++) {                 // 선분에 수직으로 내린 점(선 위의 가장 가까운 점)
          var ax = line[i][0] + sh, ay = line[i][1], bx = line[i + 1][0] + sh, by = line[i + 1][1];
          var vx = bx - ax, vy = by - ay, L = vx * vx + vy * vy, t = L ? ((m[0] - ax) * vx + (m[1] - ay) * vy) / L : 0;
          t = Math.max(0, Math.min(1, t));
          var px = ax + t * vx, py = ay + t * vy, d = Math.hypot(m[0] - px, m[1] - py) * sc;
          if (!best || d < best.d) best = { d: d, r: r, x: px, y: py };
        }
      });
    });
    if (!best || best.d > 14) { flashStatus('<b>메모 달기</b> · 제안 항로 <b>선 가까이</b>를 눌러 주세요'); return; }
    var ll = toLL(best.x, best.y), ns = notesOf(best.r.id);
    ns.push({ lat: +ll[0].toFixed(5), lon: +ll[1].toFixed(5), text: '', ts: new Date().toISOString() });
    saveRoutes(); renderRoutes(); openPop(best.r.id, ns.length - 1, true);
  }
  function openPop(rid, k, isNew) {
    var r = routeById(rid), n = r && notesOf(rid)[k]; if (!n) return;
    if (rs.hidden.indexOf(rid) >= 0) { rs.hidden = rs.hidden.filter(function (x) { return x !== rid; }); saveRoutes(); renderRoutes(); }
    if (!sugOn.checked) { sugOn.checked = true; sugOn.dispatchEvent(new Event('change')); }
    var xy = noteXY(r, n), c = r.color || colorOf(rid);
    pop.rid = rid; pop.k = k; pop.isNew = !!isNew;
    pop.style.left = xy[0] + 'px'; pop.style.top = xy[1] + 'px';
    pop.innerHTML = '<b><span class="rt-badge" style="background:' + esc(c) + ';color:#fff">' + esc(rid + (k + 1)) + '</span>' + esc(r.name) +
      '<small>' + fmtLat(n.lat) + ' ' + fmtLon(n.lon) + '</small></b>' +
      '<label class="sr" for="rt-pop-text" style="position:absolute;left:-9999px">왜 이렇게 바꿨는지</label>' +
      '<textarea id="rt-pop-text" maxlength="500" placeholder="왜 이 구간을 이렇게 바꿨는지 적어 주세요 (예: 제트기류 뒷바람 활용, SIGMET 회피)">' + esc(n.text || '') + '</textarea>' +
      '<div class="rt-btns"><button type="button" class="pri" data-a="save">저장</button><button type="button" data-a="del">메모 삭제</button><button type="button" data-a="close">닫기</button></div>';
    pop.hidden = false;
    var ta = pop.querySelector('textarea');
    ta.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { e.preventDefault(); closePop(); }
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); savePop(); }
    });
    // 지도 클릭이 끝난 뒤(click 이벤트 이후)에 초점을 줘야 입력이 바로 들어간다
    setTimeout(function () { ta.focus({ preventScroll: true }); ta.setSelectionRange(ta.value.length, ta.value.length); }, 60);
  }
  function savePop() {
    var ns = notesOf(pop.rid), n = ns[pop.k]; if (!n) return closePop();
    n.text = pop.querySelector('textarea').value.trim();
    if (!n.text && pop.isNew) ns.splice(pop.k, 1);                  // 새 메모를 비워 두고 저장하면 만들지 않음
    pop.isNew = false; saveRoutes(); pop.hidden = true; renderRoutes();
  }
  function closePop() {
    if (pop.hidden) return;
    var ns = notesOf(pop.rid), n = ns[pop.k];
    if (n && pop.isNew && !n.text) { ns.splice(pop.k, 1); saveRoutes(); renderRoutes(); }
    pop.hidden = true;
  }
  pop.addEventListener('click', function (e) {
    var a = e.target.getAttribute && e.target.getAttribute('data-a'); if (!a) return;
    if (a === 'save') savePop();
    else if (a === 'close') closePop();
    else if (a === 'del') { notesOf(pop.rid).splice(pop.k, 1); saveRoutes(); pop.hidden = true; renderRoutes(); }
  });
  sugSvg.addEventListener('pointerdown', function (e) { if (e.target.closest('.rt-note')) { e.stopPropagation(); } });
  sugSvg.addEventListener('click', function (e) {
    var g = e.target.closest('.rt-note'); if (!g) return;
    e.stopPropagation(); openPop(g.getAttribute('data-r'), +g.getAttribute('data-k'), false);
  });
  sugSec.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('[data-note]'); if (!b) return;
    var v = b.getAttribute('data-note').split('|'); openPop(v[0], +v[1], false);
    if (window.matchMedia('(max-width:800px)').matches) { var h = document.getElementById('handle'); if (h && aside.classList.contains('open')) h.click(); }
  });
  sugOn.addEventListener('change', function () { if (!sugOn.checked) closePop(); });

  var noteBtn = document.createElement('button');
  noteBtn.id = 'rt-note-btn'; noteBtn.type = 'button'; noteBtn.setAttribute('aria-pressed', 'false');
  noteBtn.setAttribute('aria-label', '항로 메모 달기'); noteBtn.title = '제안 항로에 메모 달기 (N)';
  noteBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5h16v11H9l-5 4z"/><path d="M8 9h8M8 12.5h5"/></svg>';
  noteBtn.addEventListener('click', function () { setNoting(!noting); });
  if (zoom) zoom.appendChild(noteBtn);

  // ------------------------------------------------------------------ 4. 지도 캡처 (지금 보이는 화면을 PNG 로)
  var capBtn = document.createElement('button');
  capBtn.id = 'rt-cap-btn'; capBtn.type = 'button';
  capBtn.setAttribute('aria-label', '지도 캡처'); capBtn.title = '지금 보이는 지도를 PNG로 저장';
  capBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 8h3l1.6-2.5h6.8L17 8h3v11H4z"/><circle cx="12" cy="13.2" r="3.4"/></svg>';
  if (zoom) zoom.appendChild(capBtn);
  function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  function svgForCanvas(svg, sc) {                                  // 화면과 같은 굵기·글자 크기로 고정해서 이미지로 만든다
    var c = svg.cloneNode(true);
    c.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    c.setAttribute('width', P.W); c.setAttribute('height', P.H);
    c.setAttribute('style', 'font-family:"IBM Plex Sans KR","Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif;overflow:visible');
    var str = new XMLSerializer().serializeToString(c)
      .replace(/calc\(\s*([\d.]+)px\s*\/\s*var\(--s,\s*1\)\s*\)/g, function (_, v) { return (+v / sc).toFixed(3) + 'px'; })
      .replace(/scale\(\s*calc\(\s*1\s*\/\s*var\(--s,\s*1\)\s*\)\s*\)/g, 'scale(' + (1 / sc).toFixed(4) + ')')
      .replace(/var\((--[\w-]+)(?:,[^)]*)?\)/g, function (_, v) { return cssVar(v) || '#000'; })
      .replace(/font-family:\s*inherit;?/g, '');
    return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(str);
  }
  function loadImg(src) {
    return new Promise(function (ok, no) { var im = new Image(); im.onload = function () { ok(im); }; im.onerror = no; im.src = src; });
  }
  function capture() {
    var mr = map.getBoundingClientRect(), sr = stack.getBoundingClientRect(), sc = sr.width / P.W;
    var k = Math.max(2, window.devicePixelRatio || 1), foot = 44;
    var cv = document.createElement('canvas');
    cv.width = Math.round(mr.width * k); cv.height = Math.round((mr.height + foot) * k);
    var ctx = cv.getContext('2d');
    ctx.fillStyle = getComputedStyle(map).backgroundColor || '#6ACFE3';
    ctx.fillRect(0, 0, cv.width, cv.height);
    var layers = Array.prototype.filter.call(stack.children, function (el) {
      var cs = getComputedStyle(el);
      return cs.display !== 'none' && cs.visibility !== 'hidden' && (el.tagName === 'IMG' || (el.tagName.toLowerCase() === 'svg' && el.innerHTML.trim()));
    });
    var jobs = layers.map(function (el) { return el.tagName === 'IMG' ? Promise.resolve(el) : loadImg(svgForCanvas(el, sc)); });
    capBtn.disabled = true; capBtn.setAttribute('aria-busy', 'true');
    return Promise.all(jobs).then(function (imgs) {
      ctx.save();
      ctx.beginPath(); ctx.rect(0, 0, cv.width, mr.height * k); ctx.clip();
      ctx.setTransform(k * sc, 0, 0, k * sc, k * (sr.left - mr.left), k * (sr.top - mr.top));
      imgs.forEach(function (im, i) {
        ctx.globalAlpha = parseFloat(getComputedStyle(layers[i]).opacity) || 0;
        ctx.drawImage(im, 0, 0, P.W, P.H);
      });
      ctx.restore();
      // 아래 띠: 제목 · 표시 중인 제안 항로 · 캡처 시각
      ctx.setTransform(k, 0, 0, k, 0, mr.height * k);
      ctx.fillStyle = cssVar('--panel') || '#F7F9FA'; ctx.fillRect(0, 0, mr.width, foot);
      ctx.fillStyle = cssVar('--rule') || '#C9D2DB'; ctx.fillRect(0, 0, mr.width, 1);
      var h1 = (aside.querySelector('h1') || {}).textContent || document.title;
      var now = new Date(), p2 = function (v) { return String(v).padStart(2, '0'); };
      var stamp = now.getUTCFullYear() + '-' + p2(now.getUTCMonth() + 1) + '-' + p2(now.getUTCDate()) + ' ' + p2(now.getUTCHours()) + p2(now.getUTCMinutes()) + ' UTC';
      var shown = sugOn.checked ? allRoutes().filter(function (r) { return rs.hidden.indexOf(r.id) < 0; }) : [];
      ctx.textBaseline = 'middle';
      ctx.fillStyle = cssVar('--ink') || '#16283D'; ctx.font = '600 14px "IBM Plex Sans KR","Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif';
      ctx.fillText(h1, 12, 15, mr.width - 24);
      ctx.fillStyle = cssVar('--muted') || '#5B6B7C'; ctx.font = '12px "IBM Plex Sans KR","Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif';
      var sub = (shown.length ? '제안 항로 ' + shown.map(function (r) { return r.id + ' ' + r.name; }).join(', ') + ' · ' : '') + '캡처 ' + stamp;
      ctx.fillText(sub, 12, 32, mr.width - 24);
      return new Promise(function (ok) { cv.toBlob(ok, 'image/png'); }).then(function (blob) {
        var a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'map_' + String(entryId).replace(/[^\w-]+/g, '_') + '_' + stamp.replace(/[-: ]|UTC/g, '') + 'Z.png';
        document.body.appendChild(a); a.click();
        setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1500);
        flashStatus('<b>캡처</b> · ' + a.download + ' 저장');
        map.classList.add('rt-flash'); setTimeout(function () { map.classList.remove('rt-flash'); }, 250);
        return blob;
      });
    }).catch(function (err) { window.alert('지도 캡처에 실패했습니다: ' + (err && err.message || err)); })
      .then(function (r) { capBtn.disabled = false; capBtn.removeAttribute('aria-busy'); return r; });
  }
  capBtn.addEventListener('click', capture);

  fetch('routes.json', { cache: 'no-cache' }).then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) { baseRoutes = ((d && d.routes) || []).filter(function (r) { return r && r.id; }); renderRoutes(); })
    .catch(function () { renderRoutes(); });
  renderRoutes();

  renderAll();
  window.ROUTE_TOOLS = { distKm: distKm, bearing: bearing, gcPoints: gcPoints, totalKm: totalKm, pathXY: pathXY,
    get routes() { return allRoutes(); }, curveKm: function (p) { return curveKm(smoothCurve(p)); },
    get points() { return pts.slice(); }, set points(v) { pts = v.filter(validPt); renderAll(); }, setDrawing: setDrawing,
    setNoting: setNoting, addNoteAt: addNoteAt, get notes() { return JSON.parse(JSON.stringify(rs.notes)); }, capture: capture };
})();
