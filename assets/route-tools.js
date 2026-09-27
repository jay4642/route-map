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
  function midpoint(a, b) { var g = gcPoints(a, b); return g[Math.floor(g.length / 2)]; }
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
    '.rt-drawing .rt-status{display:block}',
    '.rt-status b{color:#D9480F}',
    '.rt-sec{border-top:1px solid var(--rule);padding:12px 0}',
    '.rt-sec h2{font-size:14px;font-weight:600;margin:0 0 6px;display:flex;align-items:center;gap:8px}',
    '.rt-sec h2 label{flex:1;cursor:pointer;display:flex;align-items:center;gap:10px}',
    '.rt-sec input[type=checkbox]{width:16px;height:16px;accent-color:var(--magenta);margin:0}',
    '.rt-route{display:flex;gap:8px;align-items:flex-start;margin:6px 0 0 26px;font-size:12.5px}',
    '.rt-route .ln{flex:none;width:26px;height:0;margin-top:9px;border-top:3px solid}',
    '.rt-route .ln.dash{border-top-style:dashed}',
    '.rt-route b{font-weight:600}',
    '.rt-route span{color:var(--muted)}',
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

  function drawRoute(route) {
    var pts = route.points || route.great_circle || [];
    var line = pathXY(routeLine(pts));
    var dash = route.style === 'dash' ? ';stroke-dasharray:calc(10px / var(--s,1)) calc(6px / var(--s,1))' : '';
    var h = '<polyline points="' + pts2str(line) + '" fill="none" stroke="#fff" stroke-opacity=".75" stroke-linejoin="round" stroke-linecap="round" style="stroke-width:calc(5px / var(--s,1))' + dash + '"/>' +
            '<polyline points="' + pts2str(line) + '" fill="none" stroke="' + esc(route.color) + '" stroke-linejoin="round" stroke-linecap="round" style="stroke-width:calc(2.6px / var(--s,1))' + dash + '"/>';
    var last = line[line.length - 1], mid = line[Math.floor(line.length / 2)];
    h += '<g style="' + at(mid[0], mid[1]) + '"><text y="-8" text-anchor="middle" font-size="12" font-weight="700" fill="' + esc(route.color) +
         '" stroke="#fff" stroke-width="3" paint-order="stroke">' + esc(route.id) + '</text></g>';
    return h;
  }
  function setupSuggest(data) {
    var routes = (data && data.routes) || [];
    if (!routes.length) return;
    sugSvg.innerHTML = routes.map(drawRoute).join('');
    var sec = document.createElement('div');
    sec.className = 'rt-sec';
    sec.innerHTML = '<h2><label><input type="checkbox" id="rt-sug-on" checked>제안 항로</label></h2>' +
      routes.map(function (r) {
        var km = totalKm(r.points || r.great_circle);
        return '<div class="rt-route"><i class="ln' + (r.style === 'dash' ? ' dash' : '') + '" style="border-color:' + esc(r.color) + '"></i>' +
          '<div><b>' + esc(r.id) + ' ' + esc(r.name) + '</b><br>총 ' + fmtPair(km) +
          (r.note ? '<br><span>' + esc(r.note) + '</span>' : '') + '</div></div>';
      }).join('');
    var firstLayer = aside.querySelector('.layer');
    aside.insertBefore(sec, firstLayer || notes || null);
    var box = sec.querySelector('#rt-sug-on');
    var chips = document.getElementById('chips'), chip = null;
    if (chips) {
      chip = document.createElement('button');
      chip.type = 'button'; chip.setAttribute('aria-pressed', 'true'); chip.title = '제안 항로 켜기·끄기';
      chip.innerHTML = '<span class="d" style="background:' + esc(routes[0].color) + '"></span>제안 항로';
      chip.onclick = function () { box.checked = !box.checked; box.dispatchEvent(new Event('change')); };
      chips.insertBefore(chip, chips.firstChild);
    }
    box.addEventListener('change', function () {
      sugSvg.style.display = box.checked ? '' : 'none';
      if (chip) chip.setAttribute('aria-pressed', box.checked);
    });
  }
  fetch('routes.json', { cache: 'no-cache' }).then(function (r) { return r.ok ? r.json() : null; })
    .then(setupSuggest).catch(function () {});

  // ------------------------------------------------------------------ 2. 항로 그리기·거리 측정
  var pts = [];                                                    // [[lat, lon], ...]
  try { var saved = JSON.parse(localStorage.getItem(KEY) || 'null'); if (Array.isArray(saved)) pts = saved.filter(validPt); } catch (e) {}
  function validPt(p) { return Array.isArray(p) && p.length >= 2 && isFinite(p[0]) && isFinite(p[1]) && Math.abs(p[0]) <= 85; }
  function save() { try { localStorage.setItem(KEY, JSON.stringify(pts)); } catch (e) {} }

  var linesG = document.createElementNS('http://www.w3.org/2000/svg', 'g'), wpsG = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  drawSvg.appendChild(linesG); drawSvg.appendChild(wpsG);
  var DRAW = '#D9480F';

  function renderLines() {
    var h = '';
    if (pts.length > 1) {
      var line = pts2str(pathXY(routeLine(pts)));
      h += '<polyline points="' + line + '" fill="none" stroke="#fff" stroke-opacity=".85" stroke-linejoin="round" stroke-linecap="round" style="stroke-width:calc(5.5px / var(--s,1))"/>' +
           '<polyline points="' + line + '" fill="none" stroke="' + DRAW + '" stroke-linejoin="round" stroke-linecap="round" style="stroke-width:calc(2.8px / var(--s,1))"/>';
      for (var i = 0; i + 1 < pts.length; i++) {
        var seg = pathXY(gcPoints(pts[i], pts[i + 1])), m = seg[Math.floor(seg.length / 2)];   // 구간 중간
        h += '<g style="' + at(m[0], m[1]) + '"><text y="-7" text-anchor="middle" font-size="11.5" font-weight="700" fill="' + DRAW +
             '" stroke="#fff" stroke-width="3.2" paint-order="stroke">' + fmtPair(distKm(pts[i], pts[i + 1])) + '</text></g>';
      }
    }
    linesG.innerHTML = h;
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
    '<p class="rt-help">오른쪽 위 연필 버튼(또는 <kbd>M</kbd>)으로 켜고 지도를 눌러 점을 찍습니다. 점은 끌어서 옮기고, 우클릭(모바일은 길게 누르기)으로 지웁니다. <kbd>Backspace</kbd> 마지막 점 취소 · <kbd>Esc</kbd> 그리기 끝.</p>' +
    '<div class="rt-btns"><button type="button" id="rt-toggle2">그리기 켜기</button><button type="button" id="rt-clear">전체 지우기</button>' +
    '<button type="button" id="rt-json">JSON 내보내기</button><button type="button" id="rt-csv">CSV 내보내기</button>' +
    '<button type="button" id="rt-load">JSON 불러오기</button><input type="file" id="rt-file" accept=".json,application/json" hidden></div>' +
    '<div id="rt-table"></div>';
  insertSection(sec);

  function rows() {
    var out = [], cum = 0;
    pts.forEach(function (p, i) {
      var seg = i ? distKm(pts[i - 1], p) : null;
      if (seg != null) cum += seg;
      out.push({ n: i + 1, lat: p[0], lon: wrap180(p[1]), seg: seg, cum: i ? cum : null, brg: i ? bearing(pts[i - 1], p) : null });
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
      '</tbody><tfoot><tr><td colspan="3" style="text-align:left">총거리 (' + pts.length + '점)</td><td colspan="3">' + fmtPair(totalKm(pts)) + '</td></tr></tfoot></table>';
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
    '<kbd>M</kbd> 항로 그리기 · <kbd>Backspace</kbd> 마지막 점 취소 · <kbd>Esc</kbd> 그리기 끝');

  var drawing = false;
  function setDrawing(on) {
    drawing = on;
    map.classList.toggle('rt-drawing', on);
    btn.setAttribute('aria-pressed', on);
    syncButtons(); updateStatus();
  }
  function syncButtons() {
    var t = document.getElementById('rt-toggle2');
    if (t) { t.textContent = drawing ? '그리기 끄기' : '그리기 켜기'; t.classList.toggle('on', drawing); }
    ['rt-clear', 'rt-json', 'rt-csv'].forEach(function (id) { var b = document.getElementById(id); if (b) b.disabled = !pts.length; });
  }
  function updateStatus() {
    status.innerHTML = '<b>항로 그리기</b> · 지도를 눌러 점 추가 · ' + pts.length + '점' + (pts.length > 1 ? ' · 총 ' + fmtPair(totalKm(pts)) : '');
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
    if (!drawing || e.button > 0 || nActive > 1 || e.target.closest('button,.chips,.readout,.zoom,.rt-status,.wp')) { down = null; return; }
    down = { id: e.pointerId, x: e.clientX, y: e.clientY, moved: false };
  });
  map.addEventListener('pointermove', function (e) {
    if (down && e.pointerId === down.id && Math.hypot(e.clientX - down.x, e.clientY - down.y) >= 5) down.moved = true;
  });
  function release(e) {
    if (e.pointerId in active) { delete active[e.pointerId]; nActive--; }
    if (e.type === 'pointerup' && down && e.pointerId === down.id && !down.moved && drawing) {
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
    if (e.key === 'm' || e.key === 'M') { setDrawing(!drawing); }
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

  renderAll();
  window.ROUTE_TOOLS = { distKm: distKm, bearing: bearing, gcPoints: gcPoints, totalKm: totalKm, pathXY: pathXY,
    get points() { return pts.slice(); }, set points(v) { pts = v.filter(validPt); renderAll(); }, setDrawing: setDrawing };
})();
