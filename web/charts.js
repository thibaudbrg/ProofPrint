// Proofprint - shared SVG charts for the analyst dashboard and the labs. No dependencies.
// Every function takes a container element and the signal object exactly as the server returns it.
window.PPCharts = (() => {
  const RGB = { grey:"#808080", black:"#000", red:"#f00", green:"#0f0", blue:"#00f" };
  const SHADE = { grey:"#F1F1F1", black:"#E6E6E6", red:"#FDE3E0", green:"#E1F5E7", blue:"#E3E8FC" };
  const C = { r:"#D23B2C", g:"#1E9A4B", b:"#2E44E6", video:"#0A5AD6", gyro:"#E2001A", ink:"#12203D", ink2:"#5B6B86", pass:"#1E7A47", warn:"#B26B0C", fail:"#E2001A" };
  const fmt = (v, d=2) => v == null || !isFinite(v) ? " - " : (+v).toFixed(d);

  // ---- generic line chart: series = [{y:[], color, width, dash}], shared x ----
  function line(el, x, series, {h=200, ylines=[], xlabel="ms", marker=null, sym=false, bands=[], xband=null, dots=null, W=900} = {}){
    const H = h, L = 48, R = 12, T = 10, B = 26;
    const xmin = Math.min(...x), xmax = Math.max(...x);
    let ymin = Infinity, ymax = -Infinity;
    series.forEach(s => s.y.forEach(v => { if(v != null && isFinite(v)){ ymin = Math.min(ymin, v); ymax = Math.max(ymax, v); } }));
    ylines.forEach(l => { ymin = Math.min(ymin, l.v); ymax = Math.max(ymax, l.v); });
    if(sym){ const m = Math.max(Math.abs(ymin), Math.abs(ymax)); ymin = -m; ymax = m; }
    if(!isFinite(ymin) || ymin === ymax){ ymin = (ymin || 0) - 1; ymax = (ymax || 0) + 1; }
    const pad = (ymax - ymin) * 0.06; ymin -= pad; ymax += pad;
    const X = v => L + (v - xmin) / (xmax - xmin || 1) * (W - L - R), Y = v => T + (ymax - v) / (ymax - ymin) * (H - T - B);
    let g = `<svg viewBox="0 0 ${W} ${H}" font-family="system-ui,sans-serif" style="width:100%;height:auto;display:block">`;
    bands.forEach(b => { const x0 = Math.max(X(b.x0), L), x1 = Math.min(X(b.x1), W - R); if(x1 > x0) g += `<rect x="${x0}" y="${T}" width="${x1 - x0}" height="${H - T - B}" fill="${b.color}"/>`; });
    if(xband) g += `<rect x="${X(xband[0])}" y="${T}" width="${Math.max(0, X(xband[1]) - X(xband[0]))}" height="${H - T - B}" fill="#E1F5E7" opacity=".7"/>`;
    for(let i = 0; i <= 4; i++){ const v = ymin + (ymax - ymin) * i / 4; g += `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" stroke="#E4E6E4"/><text x="${L - 6}" y="${Y(v) + 4}" text-anchor="end" font-size="10" fill="${C.ink2}">${v.toFixed(Math.abs(ymax - ymin) < 5 ? 2 : 0)}</text>`; }
    if(ymin < 0 && ymax > 0) g += `<line x1="${L}" x2="${W - R}" y1="${Y(0)}" y2="${Y(0)}" stroke="#8A8F8A" stroke-dasharray="4 3"/>`;
    for(let i = 0; i <= 6; i++){ const v = xmin + (xmax - xmin) * i / 6; g += `<text x="${X(v)}" y="${H - 8}" text-anchor="middle" font-size="10" fill="${C.ink2}">${v.toFixed(0)}${i === 6 ? " " + xlabel : ""}</text>`; }
    ylines.forEach(l => g += `<line x1="${L}" x2="${W - R}" y1="${Y(l.v)}" y2="${Y(l.v)}" stroke="${l.color || '#999'}" stroke-dasharray="5 4"/><text x="${W - R - 2}" y="${Y(l.v) - 3}" text-anchor="end" font-size="10" fill="${l.color || '#999'}">${l.label || ''}</text>`);
    if(marker != null) g += `<line x1="${X(marker)}" x2="${X(marker)}" y1="${T}" y2="${H - B}" stroke="${C.ink}" stroke-dasharray="3 3"/>`;
    series.forEach(s => {
      const pts = s.y.map((v, i) => v == null || !isFinite(v) ? null : `${X(x[i]).toFixed(1)},${Y(v).toFixed(1)}`).filter(Boolean).join(" ");
      g += `<polyline points="${pts}" fill="none" stroke="${s.color}" stroke-width="${s.width || 2}" ${s.dash ? `stroke-dasharray="${s.dash}"` : ""} stroke-linejoin="round" stroke-linecap="round"/>`;
      if(dots) s.y.forEach((v, i) => { if(dots[i] && v != null) g += `<circle cx="${X(x[i])}" cy="${Y(v)}" r="2.2" fill="#fff" stroke="${s.color}" stroke-width="1.2"/>`; });
    });
    el.innerHTML = g + "</svg>";
  }

  const z = a => { a = a.map(Number); const m = a.reduce((s, v) => s + v, 0) / a.length; const sd = Math.sqrt(a.reduce((s, v) => s + (v - m) ** 2, 0) / a.length) || 1; return a.map(v => (v - m) / sd); };

  // ---- check 5: one picture - screen sequence (row 1), skin answer (row 2), skin channels (chart) ----
  function lightPanel(el, light){
    const s = light && light.series;
    if(!s){ el.innerHTML = `<div class="pp-empty">no curves - ${(light && light.flags || []).join(", ") || "check did not run"}</div>`; return; }
    const segs = light.per_segment || [], shift = light.lag_fit_ms || 0;
    const W = 1000, L = 54, R = 16, ROW1 = 30, ROW2 = 40, GAP = 8, CH = 230, B = 28;
    const top1 = 8, top2 = top1 + ROW1 + GAP, ctop = top2 + ROW2 + GAP, H = ctop + CH + B;
    const edges = s.edges_ms.map(e => e + shift);
    const xmin = Math.min(edges[0], s.t_ms[0]), xmax = Math.max(edges[edges.length - 1], s.t_ms[s.t_ms.length - 1]);
    const X = v => L + (v - xmin) / (xmax - xmin || 1) * (W - L - R);
    const M = [0, 1, 2].map(c => s.z.map((zz, i) => zz[c] - s.base[i][c]));
    let ymax = 0.05; M.forEach(a => a.forEach(v => { if(isFinite(v)) ymax = Math.max(ymax, Math.abs(v)); })); ymax *= 1.08;
    const Y = v => ctop + (ymax - v) / (2 * ymax) * CH;
    const ink = c => (c === "grey" || c === "green") ? C.ink : "#fff";
    let g = `<svg viewBox="0 0 ${W} ${H}" font-family="system-ui,sans-serif" style="width:100%;height:auto;display:block">`;
    g += `<text x="${L - 8}" y="${top1 + ROW1 / 2 + 4}" text-anchor="end" font-size="11" fill="${C.ink2}">screen</text>`;
    g += `<text x="${L - 8}" y="${top2 + ROW2 / 2 + 4}" text-anchor="end" font-size="11" fill="${C.ink2}">skin</text>`;
    s.slots.forEach((name, i) => {
      const x0 = X(edges[i]), x1 = X(edges[i + 1]), w = Math.max(0, x1 - x0 - 2), dur = Math.round(s.edges_ms[i + 1] - s.edges_ms[i]);
      g += `<rect x="${x0 + 1}" y="${top1}" width="${w}" height="${ROW1}" rx="4" fill="${RGB[name]}"/>`;
      g += `<text x="${(x0 + x1) / 2}" y="${top1 + ROW1 / 2 + 4}" text-anchor="middle" font-size="11" fill="${ink(name)}">${dur} ms</text>`;
      const seg = segs.find(q => q.slot === i);
      if(!seg) g += `<rect x="${x0 + 1}" y="${top2}" width="${w}" height="${ROW2}" rx="4" fill="#EEF0EC"/>` +
                    (name === "grey" ? `<text x="${(x0 + x1) / 2}" y="${top2 + ROW2 / 2 + 4}" text-anchor="middle" font-size="10" fill="#9AA1AC">reference</text>` : "");
      else if(seg.ok == null) g += `<rect x="${x0 + 1}" y="${top2}" width="${w}" height="${ROW2}" rx="4" fill="#F8EEDA"/><text x="${(x0 + x1) / 2}" y="${top2 + ROW2 / 2 + 4}" text-anchor="middle" font-size="10" fill="${C.warn}">too few frames</text>`;
      else g += `<rect x="${x0 + 1}" y="${top2}" width="${w}" height="${ROW2}" rx="4" fill="${RGB[seg.pred]}"/>` +
                `<text x="${(x0 + x1) / 2}" y="${top2 + ROW2 / 2 + 7}" text-anchor="middle" font-size="22" font-weight="800" fill="${ink(seg.pred)}">${seg.ok ? "✓" : "✗"}</text>`;
      g += `<rect x="${x0}" y="${ctop}" width="${Math.max(0, x1 - x0)}" height="${CH}" fill="${SHADE[name] || "#eee"}"/>`;
    });
    [-ymax, -ymax / 2, 0, ymax / 2, ymax].forEach(v => g += `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" stroke="${v === 0 ? "#8A8F8A" : "#E4E6E4"}" ${v === 0 ? 'stroke-dasharray="4 3"' : ""}/><text x="${L - 8}" y="${Y(v) + 4}" text-anchor="end" font-size="10" fill="${C.ink2}">${v.toFixed(2)}</text>`);
    for(let i = 0; i <= 6; i++){ const v = xmin + (xmax - xmin) * i / 6; g += `<text x="${X(v)}" y="${H - 8}" text-anchor="middle" font-size="10" fill="${C.ink2}">${v.toFixed(0)}${i === 6 ? " ms" : ""}</text>`; }
    [[C.r, 0], [C.g, 1], [C.b, 2]].forEach(([col, c]) => {
      const pts = M[c].map((v, i) => isFinite(v) ? `${X(s.t_ms[i]).toFixed(1)},${Y(v).toFixed(1)}` : null).filter(Boolean).join(" ");
      g += `<polyline points="${pts}" fill="none" stroke="${col}" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/>`;
      M[c].forEach((v, i) => { if(!s.used[i] && isFinite(v)) g += `<circle cx="${X(s.t_ms[i])}" cy="${Y(v)}" r="2.2" fill="#fff" stroke="${col}" stroke-width="1.2"/>`; });
    });
    el.innerHTML = g + "</svg>";
  }

  // ---- check 4: video flow vs gyro, horizontal and vertical, z-scored at the fitted lag ----
  function motionPanel(elH, elV, motion){
    const s = motion && motion.series;
    if(!s || !s.t_ms || s.t_ms.length < 3){ const m = `<div class="pp-empty">no curves - ${motion && (motion.reason || (motion.flags || []).join(", ")) || "check did not run"}</div>`; elH.innerHTML = m; if(elV) elV.innerHTML = ""; return; }
    line(elH, s.t_ms, [{ y: z(s.flow_x), color: C.video }, { y: z(s.gyro_yaw), color: C.gyro, dash: "6 4" }], { h: 170, sym: true });
    if(elV) line(elV, s.t_ms, [{ y: z(s.flow_y), color: C.video }, { y: z(s.gyro_pitch), color: C.gyro, dash: "6 4" }], { h: 170, sym: true });
  }

  // ---- check 6: yaw proxy per frame, + = towards the requested side ----
  function profilePanel(el, profile, { turnMin = 0.12, turnFull = 0.22 } = {}){
    const ys = profile && profile.yaw_series;
    if(!ys || !ys.length){ el.innerHTML = `<div class="pp-empty">no yaw track - ${profile && (profile.reason || profile.verdict) || "check did not run"}</div>`; return; }
    const sign = profile.side === "right" ? -1 : 1;
    const x = ys.map((_, i) => i), y = ys.map(v => v == null ? null : v * sign);
    line(el, x, [{ y, color: C.gyro, width: 2.4 }], { h: 170, sym: true, xlabel: "frame",
      ylines: [{ v: turnMin, label: "turn starts " + turnMin, color: C.warn }, { v: turnFull, label: "full turn " + turnFull, color: C.pass }] });
  }

  // ---- a score on a bar with threshold marks (face cosine, or any 0..1 score) ----
  function gauge(el, score, marks, { min = -0.1, max = 1, colorOf = null } = {}){
    const W = 600, H = 44, L = 8, R = 8;
    const X = v => L + (Math.min(max, Math.max(min, v)) - min) / (max - min) * (W - L - R);
    let g = `<svg viewBox="0 0 ${W} ${H}" font-family="system-ui,sans-serif" style="width:100%;max-width:600px;height:auto;display:block">`;
    g += `<rect x="${L}" y="18" width="${W - L - R}" height="8" rx="4" fill="#EEF0EC"/>`;
    marks.forEach(m => { g += `<line x1="${X(m.v)}" x2="${X(m.v)}" y1="12" y2="32" stroke="${m.color || '#999'}" stroke-width="2"/><text x="${X(m.v)}" y="42" text-anchor="middle" font-size="10" fill="${m.color || '#999'}">${m.label}</text>`; });
    if(score != null && isFinite(score)){ const col = colorOf ? colorOf(score) : C.ink; g += `<circle cx="${X(score)}" cy="22" r="7" fill="${col}" stroke="#fff" stroke-width="2"/><text x="${X(score)}" y="9" text-anchor="middle" font-size="11" font-weight="700" fill="${col}">${fmt(score, 3)}</text>`; }
    el.innerHTML = g + "</svg>";
  }

  return { line, lightPanel, motionPanel, profilePanel, gauge, C, RGB, SHADE, fmt, z };
})();
