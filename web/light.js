// Proofprint — check 5 (light pulse), phone-side capture. Shared by index.html and /lab/light/phone.
//
// Paints a server-minted colour sequence over the whole screen while the front camera keeps
// recording. Two loops, both on the performance.now() clock:
//   painter  — requestAnimationFrame. Switches colour on the first frame at/after its planned
//              time and logs t_raf (the callback that set it) + t_painted (the NEXT callback:
//              the best web-visible "it is on the glass now"). Scheduled from the plan, so
//              jitter never accumulates.
//   grabber  — requestVideoFrameCallback: one grab per CAMERA frame (min gap 30 ms), 320 px,
//              JPEG q 0.9 (chroma quantisation at q 0.72 eats the few-DN signal). Falls back
//              to a 40 ms setTimeout loop on old browsers. Nothing is dropped client-side —
//              frames across a switch carry the timing evidence.
// The server judges against ITS copy of the sequence; the echoed names are for diagnostics.
window.LightPulse = (() => {
  const W = 320, Q = 0.9, GAP_MS = 30, FALLBACK_MS = 40, TAIL_MS = 300, LATE_MS = 3000;
  const css = rgb => `rgb(${rgb[0]},${rgb[1]},${rgb[2]})`;

  function grab(videoEl, list, metaList, untilFn, onTick){
    const c = document.createElement("canvas");
    const vw = videoEl.videoWidth || 640, vh = videoEl.videoHeight || 480;
    c.width = W; c.height = Math.round(vh * W / vw);
    const ctx = c.getContext("2d"), pending = [];
    let i = 0, last = -1e9, finished = false;
    const rvfc = !!videoEl.requestVideoFrameCallback;
    return new Promise(done => {
      const finish = () => { if(!finished){ finished = true; Promise.all(pending).then(done); } };
      const take = (t, md) => {
        last = t;
        const name = "l" + String(++i).padStart(4, "0") + ".jpg";
        ctx.drawImage(videoEl, 0, 0, c.width, c.height);            // raw (unmirrored) pixels
        metaList.push({ file:name, t, t_pres: md ? md.presentationTime : null, t_exp: md ? md.expectedDisplayTime : null,
                        t_cap: md && md.captureTime != null ? md.captureTime : null, media_t: md ? md.mediaTime : null,
                        n: md ? md.presentedFrames : null });
        pending.push(new Promise(res => c.toBlob(b => { list.push({ name, blob:b }); res(); }, "image/jpeg", Q)));
      };
      if(rvfc){
        const onFrame = (_now, md) => {
          const t = performance.now();
          if(finished) return;
          if(untilFn(t)) return finish();
          if(t - last >= GAP_MS) take(t, md);
          if(onTick) onTick(t);
          videoEl.requestVideoFrameCallback(onFrame);
        };
        videoEl.requestVideoFrameCallback(onFrame);
        const watchdog = setInterval(() => { if(untilFn(performance.now())){ clearInterval(watchdog); finish(); } }, 100);
      } else {
        const tick = () => {
          const t = performance.now();
          if(untilFn(t)) return finish();
          take(t, null);
          if(onTick) onTick(t);
          setTimeout(tick, FALLBACK_MS);
        };
        tick();
      }
    });
  }

  // rAF painter. `seq` = [{name, rgb, ms}]. Fills L.switches; draws the small mirrored self-view.
  function paint(flashEl, thumbEl, videoEl, seq, L){
    const tctx = thumbEl.getContext("2d");
    const theme = document.querySelector('meta[name="theme-color"]');
    const setColour = c => { flashEl.style.backgroundColor = c; document.documentElement.style.background = c; if(theme) theme.content = c; };
    return new Promise(done => {
      let i = -1, tNext = null, justSet = null, lastTs = 0; const iv = [];
      const step = ts => {
        if(justSet){
          justSet.t_painted = ts;
          if(justSet.name === "end"){
            iv.sort((a, b) => a - b);
            L.raf = { median_ms: iv.length ? +iv[iv.length >> 1].toFixed(2) : null, jank: L.raf_jank || 0 };
            return done();
          }
          justSet = null;
        }
        if(lastTs){ iv.push(ts - lastTs); if(ts - lastTs > 25) L.raf_jank = (L.raf_jank || 0) + 1; }
        lastTs = ts;
        if(tNext === null) tNext = ts;
        if(ts >= tNext - 4){
          const s = ++i < seq.length ? seq[i] : { name:"end", rgb:[0,0,0], ms:0 };
          setColour(css(s.rgb));
          justSet = { i, name:s.name, ms:s.ms, t_planned:tNext, t_raf:ts, t_painted:null };
          L.switches.push(justSet);
          tNext += s.ms;
        }
        if(videoEl.videoWidth){
          tctx.save(); tctx.scale(-1, 1);
          tctx.drawImage(videoEl, -thumbEl.width, 0, thumbEl.width, thumbEl.height); tctx.restore();
        }
        requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    });
  }

  // Advisory only (the client is untrusted): catches honest jank so the user can retry early.
  function clientChecks(L, seq){
    const sw = L.switches.filter(s => s.name !== "end"), end = L.switches.find(s => s.name === "end");
    const all_painted = sw.length === seq.length && !!end && L.switches.every(s => s.t_painted != null);
    const order_ok = sw.every((s, k) => s.name === seq[k].name);
    const max_switch_err_ms = L.switches.length ? Math.max(...L.switches.map(s => Math.abs(s.t_raf - s.t_planned))) : Infinity;
    let max_dur_err_ms = 0, min_frames_per_seg = Infinity;
    if(all_painted) sw.forEach((s, k) => {
      const next = k + 1 < sw.length ? sw[k + 1] : end;
      max_dur_err_ms = Math.max(max_dur_err_ms, Math.abs((next.t_painted - s.t_painted) - s.ms));
      const n = L.frames.filter(f => f.t >= s.t_painted + 150 && f.t <= next.t_painted + 50).length;
      min_frames_per_seg = Math.min(min_frames_per_seg, n);
    });
    const ok = all_painted && order_ok && !L.hidden && max_switch_err_ms <= 40 && max_dur_err_ms <= 60 && min_frames_per_seg >= 2;
    return { all_painted, order_ok, hidden: L.hidden, max_switch_err_ms: +max_switch_err_ms.toFixed(1),
             max_dur_err_ms: +max_dur_err_ms.toFixed(1), min_frames_per_seg, n_frames: L.frames.length, ok };
  }

  // Android bonus: freeze exposure / white balance for the flash. iOS exposes nothing → "none".
  async function lockCamera(track){
    const out = { exposure:"none", white_balance:"none" };
    if(!track) return out;
    try {
      const cap = track.getCapabilities ? track.getCapabilities() : {};
      if((cap.exposureMode || []).includes("manual")){ await track.applyConstraints({ advanced:[{ exposureMode:"manual" }] }); out.exposure = "manual"; }
      if((cap.whiteBalanceMode || []).includes("manual")){ await track.applyConstraints({ advanced:[{ whiteBalanceMode:"manual" }] }); out.white_balance = "manual"; }
    } catch(_){}
    return out;
  }
  async function unlockCamera(track, lock){
    if(!track || !lock) return;
    try {
      if(lock.exposure === "manual") await track.applyConstraints({ advanced:[{ exposureMode:"continuous" }] });
      if(lock.white_balance === "manual") await track.applyConstraints({ advanced:[{ whiteBalanceMode:"continuous" }] });
    } catch(_){}
  }

  // Run one challenge. ui = {flash, thumb, hint, fill}. Resolves {frames:[{name,blob}], light:<meta.light>}.
  async function run({ videoEl, stream, challenge, ui, events }){
    const seq = challenge.slots, total = seq.reduce((s, x) => s + x.ms, 0);
    const frames = [];
    const L = { enabled:true, opted_out:false, challenge_id:challenge.challenge_id, t_received:challenge._t_received || null,
                switches:[], frames:[], hidden:false,
                grab:{ mode: videoEl.requestVideoFrameCallback ? "rvfc" : "timeout", width:W, jpeg_q:Q, min_gap_ms:GAP_MS },
                display:{ dpr: devicePixelRatio, vw: innerWidth, vh: innerHeight,
                          reduced_motion: matchMedia("(prefers-reduced-motion: reduce)").matches } };
    const onVis = () => { if(document.visibilityState !== "visible") L.hidden = true; };
    document.addEventListener("visibilitychange", onVis);
    ui.thumb.width = 120; ui.thumb.height = Math.round(120 * (videoEl.videoHeight || 480) / (videoEl.videoWidth || 640));
    ui.flash.style.backgroundColor = css(seq[0].rgb); ui.flash.hidden = false;
    const r = ui.thumb.getBoundingClientRect(); L.thumb = { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
    const track = stream ? stream.getVideoTracks()[0] : null;
    L.cam_lock = await lockCamera(track);
    if(events) events.push({ name:"light_start", t:performance.now() });
    let tEnd = null;
    const painter = paint(ui.flash, ui.thumb, videoEl, seq, L).then(() => { tEnd = performance.now(); });
    const t0 = performance.now();
    await grab(videoEl, frames, L.frames,
      now => (tEnd !== null && now - tEnd >= TAIL_MS) || now - t0 > total + LATE_MS,
      now => { const left = total - (now - t0);
               ui.fill.style.width = (Math.min(1, (now - t0) / total) * 100).toFixed(0) + "%";
               ui.hint.textContent = left > 0 ? `Keep still · look at the screen · ${Math.ceil(left / 1000)}` : "Done ✓"; });
    await painter;
    if(events) events.push({ name:"light_end", t:performance.now() });
    document.removeEventListener("visibilitychange", onVis);
    await unlockCamera(track, L.cam_lock);
    ui.flash.hidden = true; document.documentElement.style.background = "";
    const theme = document.querySelector('meta[name="theme-color"]'); if(theme) theme.content = theme.dataset.default || "";
    L.client_checks = clientChecks(L, seq);
    return { frames, light: L };
  }

  return { run, W, Q };
})();
