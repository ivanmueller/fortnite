import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ChartSpec } from '../types';

type Track = { id: number; team: number; name: string; bot: boolean; final: number | null; death: number | null; mine: boolean; t: number[]; x: number[]; y: number[] };
type Storm = { zone: number; appear: number | null; start: number; finish: number; cur: number[] | null; next: number[]; estimated?: boolean };
type Plan = { zone: number; t: number; start: number; finish: number; out_m: number; travel_s: number; leave_by: number | null; left: number | null;
  you: number[]; entry: number[] | null; alt_entry: { x: number; y: number; traffic: number; extra_m: number } | null;
  lanes: number[][]; surge: { x: number; y: number; lanes_in_range: number; height_m: number; distance_m: number } | null; reasons: string[] };
type Calib = { image_to_game: number[][]; game_to_image: number[][]; error_m?: number; image?: string; mismatch?: boolean } | null;

const C = { mine: '#0F766E', other: '#8A97A6', storm: 'rgba(103, 74, 160, 0.22)', next: '#F4F7FA', zoneLine: '#0F3B5F',
  lane: '#B4535F', surge: '#0F3B5F', entry: '#0F766E', alt: '#D08A12', land: '#DDE4EA', bg: '#EEF2F6', poi: '#1B2A3A' };

const fmt = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

function at(tr: { t: number[]; x: number[]; y: number[] }, t: number): [number, number] | null {
  const ts = tr.t;
  if (!ts.length || t < ts[0]) return null;
  let lo = 0, hi = ts.length - 1;
  if (t >= ts[hi]) return [tr.x[hi], tr.y[hi]];
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (ts[m] <= t) lo = m; else hi = m; }
  const f = (t - ts[lo]) / Math.max(1e-6, ts[hi] - ts[lo]);
  return [tr.x[lo] + f * (tr.x[hi] - tr.x[lo]), tr.y[lo] + f * (tr.y[hi] - tr.y[lo])];
}

function stormAt(storm: Storm[], t: number): { circle: number[] | null; next: number[] | null; label: string } {
  for (const z of storm) {
    if (z.appear !== null && t < z.appear) continue;
    if (t >= z.finish) continue;          // at the moment a zone finishes closing, the next one is showing
    const est = z.estimated ? ' (starting circle estimated)' : '';
    if (t < z.start) return { circle: z.cur, next: z.next, label: `Zone ${z.zone} showing · closes ${fmt(z.start)}–${fmt(z.finish)}${est}` };
    const f = (t - z.start) / Math.max(1e-6, z.finish - z.start);
    const c = z.cur ? [z.cur[0] + f * (z.next[0] - z.cur[0]), z.cur[1] + f * (z.next[1] - z.cur[1]), z.cur[2] + f * (z.next[2] - z.cur[2])] : null;
    return { circle: c, next: z.next, label: `Zone ${z.zone} closing · until ${fmt(z.finish)}${est}` };
  }
  const last = storm[storm.length - 1];
  return { circle: last ? last.next : null, next: null, label: 'Final zone closed' };
}

export function MatchMap({ spec }: { spec: ChartSpec }) {
  const o = spec.options as unknown as {
    tracks: Track[]; storm: Storm[]; plans: Plan[]; hp: { id: number; t: number[]; hp: number[] }[]; land: { cell: number; x: number[]; y: number[] } | [];
    pois: { name: string; x: number; y: number }[]; kills: { t: number; x: number; y: number; victim: number; killer: number }[];
    team: number; team_name: string; t0: number; t1: number; calibration: Calib;
    engine?: { t: number; engine: string; you: string; stake: number; followed: boolean; options: string[] }[];
  };
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [width, setWidth] = useState(900);
  const [t, setT] = useState(o.plans[0]?.t ?? o.t0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(4);
  const [follow, setFollow] = useState(true);
  const [useImage, setUseImage] = useState(!!o.calibration && !o.calibration.mismatch);
  const [calib, setCalib] = useState<Calib>(o.calibration);
  const [imgInfo, setImgInfo] = useState<{ plain: boolean; labelled: boolean; custom: boolean } | null>(null);
  const [uploadMsg, setUploadMsg] = useState<string | null>(null);
  const [calibrating, setCalibrating] = useState(false);
  const [view, setView] = useState<{ cx: number; cy: number; s: number } | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const H = 640;

  const mineTracks = useMemo(() => o.tracks.filter((tr) => tr.mine), [o.tracks]);
  const bounds = useMemo(() => {
    const xs: number[] = [], ys: number[] = [];
    if (!Array.isArray(o.land)) { xs.push(...o.land.x); ys.push(...o.land.y); }
    if (!xs.length) o.tracks.forEach((tr) => { xs.push(...tr.x); ys.push(...tr.y); });
    return { x0: Math.min(...xs), x1: Math.max(...xs), y0: Math.min(...ys), y1: Math.max(...ys) };
  }, [o]);

  useEffect(() => {
    const el = wrapRef.current; if (!el) return;
    const ro = new ResizeObserver(() => setWidth(Math.max(400, el.clientWidth - 330)));
    ro.observe(el); return () => ro.disconnect();
  }, []);
  useEffect(() => { fetch('/api/map/info').then((r) => r.json()).then(setImgInfo).catch(() => setImgInfo(null)); }, []);
  useEffect(() => {
    if (!useImage || !calib) return;
    const img = new Image(); img.src = `/api/map/image?kind=${calib.image === 'custom' ? 'custom' : 'plain'}&v=${Date.now()}`;
    img.onload = () => { imgRef.current = img; draw(); };
  }, [useImage, calib]); // eslint-disable-line react-hooks/exhaustive-deps

  // playback
  useEffect(() => {
    if (!playing) return;
    let last = performance.now(), raf = 0;
    const step = (now: number) => {
      const dt = (now - last) / 1000; last = now;
      setT((v) => { const n = v + dt * speed; if (n >= o.t1) { setPlaying(false); return o.t1; } return n; });
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [playing, speed, o.t1]);

  const plan = useMemo(() => [...o.plans].reverse().find((p) => p.t <= t + 0.5) ?? null, [o.plans, t]);
  const call = useMemo(() => [...(o.engine ?? [])].reverse().find((c) => c.t <= t + 0.5 && t - c.t < 20) ?? null, [o.engine, t]);
  const misses = useMemo(() => (o.engine ?? []).filter((c) => !c.followed && c.stake >= 1), [o.engine]);
  const st = useMemo(() => stormAt(o.storm, t), [o.storm, t]);

  // the view: follow the team (zoomed to the current zone) or free pan/zoom
  const currentView = useMemo(() => {
    if (view && !follow) return view;
    const fit = (w: number, h: number, cx: number, cy: number) => ({ cx, cy, s: Math.min(width / w, H / h) * 0.9 });
    const me = mineTracks.map((tr) => at(tr, t)).filter(Boolean) as [number, number][];
    if (follow && me.length) {
      const r = (st.next ?? st.circle)?.[2] ?? 400;
      const [mx, my] = [me.reduce((a, p) => a + p[0], 0) / me.length, me.reduce((a, p) => a + p[1], 0) / me.length];
      return fit(Math.max(r * 3, 600), Math.max(r * 3, 600), mx, my);
    }
    return fit(bounds.x1 - bounds.x0, bounds.y1 - bounds.y0, (bounds.x0 + bounds.x1) / 2, (bounds.y0 + bounds.y1) / 2);
  }, [view, follow, mineTracks, t, st, bounds, width]);

  // Drawn as the in-game map shows it. With a calibrated map image, the orientation comes from the image itself (it IS the
  // in-game view); otherwise game +X points right and game +Y points down.
  const orient = useMemo(() => {
    const g = calib?.game_to_image;
    if (g && Math.abs(g[0][0]) >= Math.abs(g[0][1]) && Math.abs(g[1][1]) >= Math.abs(g[1][0])) {
      return { fx: Math.sign(g[0][0]) || 1, fy: Math.sign(g[1][1]) || 1 };
    }
    return { fx: 1, fy: 1 };
  }, [calib]);
  const toScreen = useCallback((x: number, y: number): [number, number] =>
    [orient.fx * (x - currentView.cx) * currentView.s + width / 2, orient.fy * (y - currentView.cy) * currentView.s + H / 2],
  [currentView, width, orient]);

  const draw = useCallback(() => {
    const cv = canvasRef.current; if (!cv) return;
    const ctx = cv.getContext('2d'); if (!ctx) return;
    const dpr = window.devicePixelRatio || 1;
    cv.width = width * dpr; cv.height = H * dpr; cv.style.width = `${width}px`; cv.style.height = `${H}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = C.bg; ctx.fillRect(0, 0, width, H);
    const s = currentView.s;
    // map: the real image (calibrated) or the playable ground
    if (useImage && calib && imgRef.current) {
      const m = calib.image_to_game;   // [[a, b, c], [d, e, f]] px -> game cm
      const a = m[0][0] / 100, b = m[0][1] / 100, c = m[0][2] / 100, d = m[1][0] / 100, e = m[1][1] / 100, f = m[1][2] / 100;
      // screen = V(game): sx = fx(gx - cx)s + W/2, sy = fy(gy - cy)s + H/2
      const { fx, fy } = orient;
      ctx.save();
      ctx.setTransform(dpr * fx * a * s, dpr * fy * d * s, dpr * fx * b * s, dpr * fy * e * s,
        dpr * (fx * (c - currentView.cx) * s + width / 2), dpr * (fy * (f - currentView.cy) * s + H / 2));
      ctx.drawImage(imgRef.current, 0, 0);
      ctx.restore();
    } else if (!Array.isArray(o.land)) {
      ctx.fillStyle = C.land;
      const w = o.land.cell * s + 0.6;
      for (let i = 0; i < o.land.x.length; i++) {
        // the cell's top-left corner on screen, whichever way the axes point
        const [sx, sy] = toScreen(o.land.x[i] + (orient.fx < 0 ? o.land.cell : 0), o.land.y[i] + (orient.fy < 0 ? o.land.cell : 0));
        ctx.fillRect(sx, sy, w, w);
      }
    }
    // storm outside the current circle
    if (st.circle) {
      const [cx, cy] = toScreen(st.circle[0], st.circle[1]);
      ctx.beginPath(); ctx.rect(0, 0, width, H); ctx.arc(cx, cy, st.circle[2] * s, 0, Math.PI * 2, true);
      ctx.fillStyle = C.storm; ctx.fill('evenodd');
      ctx.strokeStyle = 'rgba(103, 74, 160, 0.8)'; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(cx, cy, st.circle[2] * s, 0, Math.PI * 2); ctx.stroke();
    }
    if (st.next) {
      const [nx, ny] = toScreen(st.next[0], st.next[1]);
      ctx.setLineDash([6, 4]); ctx.strokeStyle = C.zoneLine; ctx.lineWidth = 1.6;
      ctx.beginPath(); ctx.arc(nx, ny, st.next[2] * s, 0, Math.PI * 2); ctx.stroke(); ctx.setLineDash([]);
    }
    // places
    ctx.font = '11px Inter, system-ui, sans-serif'; ctx.textAlign = 'center';
    for (const p of o.pois) {
      const [px, py] = toScreen(p.x, p.y);
      ctx.fillStyle = C.poi; ctx.fillRect(px - 2, py - 2, 4, 4);
      ctx.fillStyle = 'rgba(27,42,58,0.8)'; ctx.fillText(p.name, px, py - 6);
    }
    // the plan for the zone on screen: routes in, entry points, surge base, where you were
    if (plan && t < plan.finish + 5) {
      ctx.setLineDash([4, 4]); ctx.strokeStyle = C.lane; ctx.lineWidth = 1.2;
      for (const l of plan.lanes) { const [a1, b1] = toScreen(l[0], l[1]); const [a2, b2] = toScreen(l[2], l[3]); ctx.beginPath(); ctx.moveTo(a1, b1); ctx.lineTo(a2, b2); ctx.stroke(); }
      ctx.setLineDash([]);
      const mark = (x: number, y: number, color: string, label: string, shape: 'diamond' | 'ring' | 'dot') => {
        const [mx, my] = toScreen(x, y);
        ctx.fillStyle = color; ctx.strokeStyle = color; ctx.lineWidth = 2.5;
        if (shape === 'diamond') { ctx.beginPath(); ctx.moveTo(mx, my - 9); ctx.lineTo(mx + 9, my); ctx.lineTo(mx, my + 9); ctx.lineTo(mx - 9, my); ctx.closePath(); ctx.fill(); }
        else if (shape === 'ring') { ctx.beginPath(); ctx.arc(mx, my, 8, 0, Math.PI * 2); ctx.stroke(); }
        else { ctx.beginPath(); ctx.arc(mx, my, 5, 0, Math.PI * 2); ctx.fill(); }
        ctx.font = '600 11px Inter, system-ui, sans-serif'; ctx.textAlign = 'left'; ctx.fillStyle = color; ctx.fillText(label, mx + 11, my + 4);
      };
      if (plan.entry) mark(plan.entry[0], plan.entry[1], C.entry, 'Entry', 'dot');
      if (plan.alt_entry) mark(plan.alt_entry.x, plan.alt_entry.y, C.alt, 'Less crowded entry', 'ring');
      if (plan.surge) mark(plan.surge.x, plan.surge.y, C.surge, 'Surge base', 'diamond');
    }
    // eliminations in the last 4 s
    for (const k of o.kills) {
      if (k.t > t || k.t < t - 4) continue;
      const [kx, ky] = toScreen(k.x, k.y);
      ctx.strokeStyle = k.victim === o.team ? C.alt : k.killer === o.team ? C.mine : '#B4535F'; ctx.lineWidth = 3;
      ctx.beginPath(); ctx.moveTo(kx - 7, ky - 7); ctx.lineTo(kx + 7, ky + 7); ctx.moveTo(kx + 7, ky - 7); ctx.lineTo(kx - 7, ky + 7); ctx.stroke();
    }
    // players: everyone else, then the team with a trail
    for (const tr of o.tracks) {
      if (tr.mine || (tr.death !== null && t > tr.death)) continue;
      const p = at(tr, t); if (!p) continue;
      const [px, py] = toScreen(p[0], p[1]);
      ctx.fillStyle = tr.bot ? '#C4CCD5' : C.other; ctx.beginPath(); ctx.arc(px, py, 3.5, 0, Math.PI * 2); ctx.fill();
    }
    for (const tr of mineTracks) {
      ctx.strokeStyle = 'rgba(15,118,110,0.45)'; ctx.lineWidth = 2; ctx.beginPath();
      let started = false;
      for (let i = 0; i < tr.t.length; i++) {
        if (tr.t[i] < t - 60 || tr.t[i] > t) continue;
        const [px, py] = toScreen(tr.x[i], tr.y[i]);
        if (!started) { ctx.moveTo(px, py); started = true; } else ctx.lineTo(px, py);
      }
      ctx.stroke();
      if (tr.death !== null && t > tr.death) continue;
      const p = at(tr, t); if (!p) continue;
      const [px, py] = toScreen(p[0], p[1]);
      ctx.fillStyle = C.mine; ctx.strokeStyle = '#fff'; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(px, py, 7, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    }
  }, [width, currentView, useImage, calib, o, st, plan, t, mineTracks, toScreen, orient]);
  useEffect(() => { draw(); }, [draw]);

  // interaction: wheel zoom, drag pan, hover names
  const drag = useRef<{ x: number; y: number; v: { cx: number; cy: number; s: number } } | null>(null);
  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    const v = currentView, k = e.deltaY < 0 ? 1.2 : 1 / 1.2;
    const rect = (e.target as HTMLCanvasElement).getBoundingClientRect();
    const { fx, fy } = orient;
    const gx = fx * (e.clientX - rect.left - width / 2) / v.s + v.cx, gy = fy * (e.clientY - rect.top - H / 2) / v.s + v.cy;
    const s = v.s * k;
    setFollow(false);
    setView({ s, cx: gx - fx * (e.clientX - rect.left - width / 2) / s, cy: gy - fy * (e.clientY - rect.top - H / 2) / s });
  };
  const onDown = (e: React.MouseEvent) => { drag.current = { x: e.clientX, y: e.clientY, v: currentView }; setFollow(false); };
  const onMove = (e: React.MouseEvent) => {
    if (drag.current) {
      const d = drag.current;
      setView({ s: d.v.s, cx: d.v.cx - orient.fx * (e.clientX - d.x) / d.v.s, cy: d.v.cy - orient.fy * (e.clientY - d.y) / d.v.s });
      return;
    }
    const rect = (e.target as HTMLCanvasElement).getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    let best: string | null = null, bd = 10;
    for (const tr of o.tracks) {
      if (tr.death !== null && t > tr.death) continue;
      const p = at(tr, t); if (!p) continue;
      const [px, py] = toScreen(p[0], p[1]);
      const d = Math.hypot(px - mx, py - my);
      if (d < bd) { bd = d; best = `${tr.name}${tr.final ? ` · placed ${tr.final}` : ''}${tr.mine ? ' · analysed team' : ''}`; }
    }
    setHover(best);
  };
  const onUp = () => { drag.current = null; };

  const hpNow = o.hp.map((h) => { let v: number | null = null; for (let i = 0; i < h.t.length && h.t[i] <= t; i++) v = h.hp[i]; return v; });
  const alive = new Set(o.tracks.filter((tr) => !tr.bot && (tr.death === null || t <= tr.death) && (tr.t[0] ?? 1e9) <= t).map((tr) => tr.team)).size;
  const meNow = mineTracks.map((tr) => at(tr, t)).filter(Boolean) as [number, number][];
  const outside = st.next && meNow.length ? Math.max(0, Math.hypot(meNow[0][0] - st.next[0], meNow[0][1] - st.next[1]) - st.next[2]) : null;
  const zoneTicks = o.plans.map((p) => ({ zone: p.zone, f: (p.t - o.t0) / (o.t1 - o.t0) }));

  return (
    <div className="matchmap" ref={wrapRef}>
      <div className="matchmap__main">
        <canvas ref={canvasRef} onWheel={onWheel} onMouseDown={onDown} onMouseMove={onMove} onMouseUp={onUp} onMouseLeave={() => { onUp(); setHover(null); }} />
        {hover && <div className="matchmap__hover">{hover}</div>}
        <div className="matchmap__controls">
          <button className="btn btn--sm" onClick={() => setPlaying((p) => !p)}>{playing ? 'Pause' : 'Play'}</button>
          <select className="input" value={speed} onChange={(e) => setSpeed(+e.target.value)}>{[1, 2, 4, 8, 16].map((v) => <option key={v} value={v}>{v}×</option>)}</select>
          <span className="matchmap__clock">{fmt(t)}</span>
          <div className="matchmap__timeline">
            <input type="range" min={o.t0} max={o.t1} step={0.5} value={t} onChange={(e) => setT(+e.target.value)} />
            {misses.map((c) => (
              <button key={`e${c.t}`} className="matchmap__miss" style={{ left: `${((c.t - o.t0) / (o.t1 - o.t0)) * 100}%` }}
                      title={`${fmt(c.t)} · engine: ${c.engine} · you: ${c.you} · ${c.stake.toFixed(1)} pts`}
                      onClick={() => { setT(c.t); setPlaying(false); }} />
            ))}
            {zoneTicks.map((z) => (
              <button key={z.zone} className="matchmap__tick" style={{ left: `${z.f * 100}%` }} title={`Zone ${z.zone} appears`}
                      onClick={() => { setT(o.plans.find((p) => p.zone === z.zone)!.t); setPlaying(false); }}>{z.zone}</button>
            ))}
          </div>
          <label className="check"><input type="checkbox" checked={follow} onChange={(e) => { setFollow(e.target.checked); if (e.target.checked) setView(null); }} /> Follow team</label>
          <button className="btn btn--ghost btn--sm" onClick={() => { setFollow(false); setView(null); }}>Whole map</button>
        </div>
      </div>
      <aside className="matchmap__panel">
        <div className="matchmap__legend">
          <span><i style={{ background: C.mine }} /> {o.team_name}</span><span><i style={{ background: C.other }} /> Other players</span>
          <span><i style={{ background: C.lane }} /> Routes in</span><span><i style={{ background: C.surge }} /> Surge base</span>
        </div>
        <p className="matchmap__status"><strong>{st.label}</strong><br />{alive} teams alive
          {outside !== null && <> · you are {outside < 1 ? 'inside the next zone' : `${outside.toFixed(0)} m outside it`}</>}
          {hpNow.some((v) => v !== null) && <> · health {hpNow.map((v) => (v === null ? '–' : v)).join(' / ')}</>}</p>
        {call && (
          <div className={`matchmap__engine ${call.followed ? 'is-ok' : 'is-miss'}`}>
            <h4>Engine at {fmt(call.t)} <span className="muted small">(live knowledge only)</span></h4>
            <p><strong>Engine:</strong> {call.engine}<br /><strong>You:</strong> {call.you}
              {!call.followed && <> · <strong>{call.stake.toFixed(1)} points at stake</strong></>}</p>
            <p className="muted small">{call.options.join(' · ')}</p>
          </div>
        )}
        {plan ? (
          <div className="matchmap__plan">
            <h4>Zone {plan.zone}: the rotation plan</h4>
            <ul>{plan.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
            <div className="row">
              <button className="btn btn--ghost btn--sm" onClick={() => { setT(plan.t); setPlaying(false); }}>Back to the reveal</button>
              {plan.leave_by && <button className="btn btn--ghost btn--sm" onClick={() => { setT(plan.leave_by!); setPlaying(false); }}>Leave-by moment</button>}
            </div>
          </div>
        ) : <p className="muted small">Press Play, or click a zone number on the timeline.</p>}
        <div className="matchmap__map">
          {calib && !calib.mismatch && <label className="check"><input type="checkbox" checked={useImage} onChange={(e) => setUseImage(e.target.checked)} /> Real map image
            <span className="muted small"> (lines up within {Math.max(1, Math.round(calib.error_m ?? 0))} m)</span></label>}
          {calib?.mismatch && (
            <p className="notice notice--warn small">The places on this image are off by about {Math.round(calib.error_m ?? 0)} m: it's probably a
              different island from these games. Upload the map for the version these games were played on.</p>
          )}
          {(imgInfo?.labelled || imgInfo?.custom) && (
            <button className="link" onClick={() => setCalibrating(true)}>{calib ? 'Recalibrate the map image' : 'Use the real map image (one-time setup)'}</button>
          )}
          <label className="link upload">Upload the right map image
            <input type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={async (e) => {
              const f = e.target.files?.[0]; if (!f) return;
              setUploadMsg('Uploading…');
              const res = await fetch('/api/map/upload', { method: 'PUT', body: f });
              if (!res.ok) { setUploadMsg((await res.json()).detail ?? 'Upload failed'); return; }
              setUploadMsg(null); setImgInfo((i) => ({ plain: !!i?.plain, labelled: !!i?.labelled, custom: true })); setCalib(null); setUseImage(false);
              setCalibrating(true);
            }} />
          </label>
          {uploadMsg && <p className="muted small">{uploadMsg}</p>}
          <p className="muted small">The downloaded image is fortnite-api's current map, which can be a different island from your games. If so,
            upload that version's map (for example from fortnite.gg's map archive or the Fortnite wiki).</p>
        </div>
      </aside>
      {calibrating && <Calibrator storm={o.storm} existing={calib} image={imgInfo?.custom ? 'custom' : 'pois'}
                                  onDone={(c) => { setCalib(c); setUseImage(!!c && !c.mismatch); setCalibrating(false); }} onCancel={() => setCalibrating(false)} />}
    </div>
  );
}

type Ref = { key: string; name: string; x: number; y: number; kind: string };      // game coordinates in cm
type Pick = Ref & { px: number; py: number };

/** Least-squares affine fit image px -> game cm from 3+ picks, with each pick's error in metres. */
function fitAffine(picks: Pick[]): { err: number[]; toImage: (x: number, y: number) => [number, number] } | null {
  if (picks.length < 3) return null;
  const S = [[0, 0, 0], [0, 0, 0], [0, 0, 0]], Bx = [0, 0, 0], By = [0, 0, 0];
  for (const p of picks) {
    const v = [p.px, p.py, 1];
    for (let i = 0; i < 3; i++) { for (let j = 0; j < 3; j++) S[i][j] += v[i] * v[j]; Bx[i] += v[i] * p.x; By[i] += v[i] * p.y; }
  }
  const det = (M: number[][]) => M[0][0] * (M[1][1] * M[2][2] - M[1][2] * M[2][1]) - M[0][1] * (M[1][0] * M[2][2] - M[1][2] * M[2][0])
    + M[0][2] * (M[1][0] * M[2][1] - M[1][1] * M[2][0]);
  const D = det(S);
  if (Math.abs(D) < 1e-6) return null;
  const solve = (b: number[]) => [0, 1, 2].map((k) => det(S.map((row, i) => row.map((v, j) => (j === k ? b[i] : v)))) / D);
  const ax = solve(Bx), ay = solve(By);                     // gx = ax0 px + ax1 py + ax2 ; gy = ay0 px + ay1 py + ay2
  const d2 = ax[0] * ay[1] - ax[1] * ay[0];
  if (Math.abs(d2) < 1e-12) return null;
  const err = picks.map((p) => Math.hypot(ax[0] * p.px + ax[1] * p.py + ax[2] - p.x, ay[0] * p.px + ay[1] * p.py + ay[2] - p.y) / 100);
  const toImage = (x: number, y: number): [number, number] => {
    const gx = x - ax[2], gy = y - ay[2];
    return [(ay[1] * gx - ax[1] * gy) / d2, (-ay[0] * gx + ax[0] * gy) / d2];
  };
  return { err, toImage };
}

/** Line the map image up with game coordinates: click any named places, landmarks or real zone centres, as many as you like. */
function Calibrator({ storm, existing, image, onDone, onCancel }: {
  storm: Storm[]; existing: Calib; image: 'custom' | 'pois'; onDone: (c: Calib) => void; onCancel: () => void;
}) {
  const [places, setPlaces] = useState<Ref[]>([]);
  const [tab, setTab] = useState<'places' | 'zones'>('places');
  const [q, setQ] = useState('');
  const [target, setTarget] = useState<Ref | null>(null);
  const [picks, setPicks] = useState<Pick[]>([]);
  const [labelled, setLabelled] = useState(image !== 'custom');
  const [view, setView] = useState({ s: 1, tx: 0, ty: 0 });
  const [error, setError] = useState<string | null>(null);
  const [nat, setNat] = useState({ w: 1, h: 1 });
  const boxRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const drag = useRef<{ x: number; y: number; tx: number; ty: number; moved: boolean } | null>(null);
  const kind = image === 'custom' ? 'custom' : 'plain';

  useEffect(() => {
    fetch('/api/map/places').then((r) => r.json()).then((rows: { name: string; x: number; y: number; kind: string }[]) => {
      // two landmarks can share a name (e.g. two car washes), so the key includes the position
      const refs = rows.map((r) => ({ key: `${r.kind}:${r.name}:${Math.round(r.x)}:${Math.round(r.y)}`, ...r }));
      setPlaces(refs);
      const prev = existing as unknown as { image?: string; points?: { px: number; py: number; x: number; y: number }[] } | null;
      if (prev && (prev.image ?? 'plain') === kind) {               // keep the saved points for this image, to refine them
        setPicks((prev.points ?? []).map((p, i) => {
          const ref = refs.find((r) => Math.hypot(r.x - p.x, r.y - p.y) < 1);
          return { key: ref?.key ?? `saved:${i}`, name: ref?.name ?? `Saved point ${i + 1}`, kind: ref?.kind ?? 'saved', ...p };
        }));
      }
    }).catch(() => setPlaces([]));
  }, [existing, kind]);

  const zones: Ref[] = storm.map((z) => ({ key: `zone:${z.zone}`, name: `Zone ${z.zone} centre`, x: z.next[0] * 100, y: z.next[1] * 100, kind: 'zone' }));
  const fit = useMemo(() => fitAffine(picks), [picks]);
  const list = (tab === 'places' ? places : zones).filter((r) => r.name.toLowerCase().includes(q.toLowerCase()));
  const picked = new Set(picks.map((p) => p.key));
  const src = image === 'custom' ? '/api/map/image?kind=custom' : `/api/map/image?kind=${labelled ? 'pois' : 'plain'}`;

  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    const box = boxRef.current!.getBoundingClientRect();
    const mx = e.clientX - box.left, my = e.clientY - box.top;
    const k = e.deltaY < 0 ? 1.25 : 0.8;
    setView((v) => { const s = Math.min(12, Math.max(1, v.s * k)); const f = s / v.s; return { s, tx: mx - (mx - v.tx) * f, ty: my - (my - v.ty) * f }; });
  };
  const onDown = (e: React.MouseEvent) => { drag.current = { x: e.clientX, y: e.clientY, tx: view.tx, ty: view.ty, moved: false }; };
  const onMove = (e: React.MouseEvent) => {
    const d = drag.current; if (!d) return;
    if (Math.hypot(e.clientX - d.x, e.clientY - d.y) > 4) d.moved = true;
    if (d.moved) setView((v) => ({ ...v, tx: d.tx + e.clientX - d.x, ty: d.ty + e.clientY - d.y }));
  };
  const onUp = (e: React.MouseEvent) => {
    const d = drag.current; drag.current = null;
    if (!d || d.moved || !target || !imgRef.current) return;
    const r = imgRef.current.getBoundingClientRect();         // includes the zoom, so this is exact at any zoom level
    const px = (e.clientX - r.left) * (nat.w / r.width), py = (e.clientY - r.top) * (nat.h / r.height);
    if (px < 0 || py < 0 || px > nat.w || py > nat.h) return;
    setPicks((ps) => [...ps.filter((p) => p.key !== target.key), { ...target, px, py }]);
    setTarget(null);
  };
  const save = async () => {
    setError(null);
    const res = await fetch('/api/map/calibration', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image: kind, points: picks.map((p) => ({ px: p.px, py: p.py, x: p.x, y: p.y })) }) });
    if (res.ok) onDone(await res.json()); else setError((await res.json()).detail ?? 'Calibration failed');
  };
  const onScreen = (px: number, py: number) => [px * (imgRef.current?.clientWidth ?? 1) / nat.w, py * (imgRef.current?.clientHeight ?? 1) / nat.h];

  return (
    <div className="matchmap__calib">
      <div className="calib">
        <div className="calib__image" ref={boxRef} onWheel={onWheel} onMouseDown={onDown} onMouseMove={onMove} onMouseUp={onUp}
             onMouseLeave={() => { drag.current = null; }} style={{ cursor: target ? 'crosshair' : 'grab' }}>
          <div className="calib__layer" style={{ transform: `translate(${view.tx}px, ${view.ty}px) scale(${view.s})` }}>
            <img ref={imgRef} src={src} alt="Fortnite map" draggable={false}
                 onLoad={(e) => setNat({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })} />
            {fit && [...places, ...zones].map((r) => {
              const [ix, iy] = onScreen(...fit.toImage(r.x, r.y));
              return <span key={`f${r.key}`} className={`calib__ghost ${r.kind === 'zone' ? 'is-zone' : ''}`} style={{ left: ix, top: iy }} title={r.name} />;
            })}
            {picks.map((p, i) => {
              const [ix, iy] = onScreen(p.px, p.py);
              return <span key={p.key} className={`calib__pin ${fit && fit.err[i] > 25 ? 'is-bad' : ''}`} style={{ left: ix, top: iy }} title={p.name}>{i + 1}</span>;
            })}
          </div>
        </div>
        <div className="calib__side">
          <h4>Calibrate the map image</h4>
          <p className="muted small">{target ? <>Now click <strong>{target.name}</strong> on the image. Scroll to zoom in for a precise click; drag to move.</> :
            'Choose a place, landmark or zone centre, then click exactly where it is on the image. Use at least 3, spread across the map; 6 or more is better.'}</p>
          <div className="row">
            <button className={`btn btn--sm ${tab === 'places' ? '' : 'btn--ghost'}`} onClick={() => setTab('places')}>Places and landmarks</button>
            <button className={`btn btn--sm ${tab === 'zones' ? '' : 'btn--ghost'}`} onClick={() => setTab('zones')}>Zones in this game</button>
          </div>
          {tab === 'zones' && <p className="muted small">Open this game in Fortnite's replay viewer, note where a zone's centre is on the in-game map, and click the same spot here.
            Zones are exact positions, so they make excellent calibration points.</p>}
          <input className="input" placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} />
          <ul className="calib__list">
            {list.map((r) => (
              <li key={r.key}>
                <button className={`calib__ref ${target?.key === r.key ? 'is-on' : ''}`} onClick={() => setTarget(r)}>
                  {picked.has(r.key) ? '✓ ' : ''}{r.name} <span className="muted small">{r.kind === 'landmark' ? 'landmark' : r.kind === 'zone' ? '' : 'place'}</span>
                </button>
              </li>
            ))}
            {list.length === 0 && <li className="muted small" style={{ padding: '0.4rem 0.6rem' }}>
              {tab === 'places' ? 'No places yet: run Data → Update map names.' : 'No zones in this game.'}</li>}
          </ul>
          <h4>Your points</h4>
          {picks.length === 0 && <p className="muted small">None yet.</p>}
          <ol className="calib__picks">
            {picks.map((p, i) => (
              <li key={p.key}>
                {p.name}{fit && <span className={`calib__err ${fit.err[i] > 25 ? 'is-bad' : ''}`}>{fit.err[i].toFixed(0)} m</span>}
                <button className="link" onClick={() => setTarget(p)}>re-click</button>{' · '}
                <button className="link" onClick={() => setPicks((ps) => ps.filter((x) => x.key !== p.key))}>remove</button>
              </li>
            ))}
          </ol>
          {fit && <p className="muted small">Largest error {Math.max(...fit.err).toFixed(0)} m. The white dots show where every place and zone centre lands with
            this fit; a point in red is probably clicked in the wrong spot (re-click or remove it).</p>}
          {image !== 'custom' && <label className="check"><input type="checkbox" checked={labelled} onChange={(e) => setLabelled(e.target.checked)} /> Show place names on the image</label>}
          {error && <p className="notice notice--error">{error}</p>}
          <div className="row">
            <button className="btn" disabled={picks.length < 3} onClick={save}>Save calibration</button>
            <button className="btn btn--ghost" onClick={() => setPicks([])}>Clear</button>
            <button className="btn btn--ghost" onClick={onCancel}>Cancel</button>
          </div>
        </div>
      </div>
    </div>
  );
}
