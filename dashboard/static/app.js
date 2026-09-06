'use strict';

// ═══════════════════════════════════════════════════════
//  Constants
// ═══════════════════════════════════════════════════════

const BOT_COLORS = { TREND_PULLBACK: '#10b981', MACD: '#3b82f6', RSI_VWAP: '#a855f7', CVD: '#f59e0b' };
const BOT_LABELS = { TREND_PULLBACK: 'T', MACD: 'M', RSI_VWAP: 'R', CVD: 'C' };
const CARD_CLASS = { TREND_PULLBACK: 'trend-card', MACD: 'macd-card', RSI_VWAP: 'rsivwap-card', CVD: 'cvd-card' };

const BASE = '';  // same origin

function apiHeaders() {
  const key = sessionStorage.getItem('dashboard_api_key') || '';
  return key ? { 'X-API-Key': key } : {};
}

async function apiFetch(url, opts = {}) {
  const r = await fetch(url, {
    ...opts,
    headers: { ...apiHeaders(), ...(opts.headers || {}) },
  });
  if (r.status === 401 && !sessionStorage.getItem('dashboard_api_key')) {
    const key = prompt('Dashboard API key required:');
    if (key) {
      sessionStorage.setItem('dashboard_api_key', key);
      return apiFetch(url, opts);
    }
  }
  return r;
}

// ═══════════════════════════════════════════════════════
//  App State
// ═══════════════════════════════════════════════════════

const S = {
  symbol:    'BTC/USDT',
  timeframe: '5m',
  candles:   [],
  trades:    [],
  stats:     [],
  portfolio: null,
  universe:  null,
  signals:   [],
  livePrice:  null,
  livePrices: {},   // { 'BTC/USDT': 76000, 'SOL/USDT': 86 } — live price per symbol
  panels:    { macd: true, rsi: true, cvd: false },
  botVis:    { TREND_PULLBACK: true, MACD: true, RSI_VWAP: true, CVD: true },
  view:      { start: 0, count: 120 },
  hover:     { active: false, x: 0, y: 0, idx: -1 },
  drag:      { on: false, startX: 0, startView: 0 },
  wsConnected: false,
};

// ═══════════════════════════════════════════════════════
//  Math / Indicator helpers
// ═══════════════════════════════════════════════════════

function ema(vals, period) {
  const k   = 2 / (period + 1);
  const out = new Array(vals.length).fill(null);
  let sum = 0, cnt = 0, seed = -1;
  for (let i = 0; i < vals.length; i++) {
    if (vals[i] == null) { sum = 0; cnt = 0; continue; }
    sum += vals[i]; cnt++;
    if (cnt === period) { seed = i; out[i] = sum / period; break; }
  }
  if (seed < 0) return out;
  for (let i = seed + 1; i < vals.length; i++) {
    out[i] = vals[i] == null ? null : vals[i] * k + out[i - 1] * (1 - k);
  }
  return out;
}

function calcRSI(closes, period = 14) {
  const out = new Array(closes.length).fill(null);
  if (closes.length < period + 1) return out;
  let ag = 0, al = 0;
  for (let i = 1; i <= period; i++) {
    const d = closes[i] - closes[i - 1];
    if (d > 0) ag += d; else al -= d;
  }
  ag /= period; al /= period;
  out[period] = 100 - 100 / (1 + ag / (al || 1e-9));
  for (let i = period + 1; i < closes.length; i++) {
    const d = closes[i] - closes[i - 1];
    ag = (ag * (period - 1) + Math.max(d, 0))  / period;
    al = (al * (period - 1) + Math.max(-d, 0)) / period;
    out[i] = 100 - 100 / (1 + ag / (al || 1e-9));
  }
  return out;
}

function calcMACD(closes) {
  const e12  = ema(closes, 12);
  const e26  = ema(closes, 26);
  const line = e12.map((v, i) => v != null && e26[i] != null ? v - e26[i] : null);
  const sig  = ema(line, 9);
  const hist = line.map((v, i) => v != null && sig[i] != null ? v - sig[i] : null);
  return { line, sig, hist };
}

function calcCVD(candles) {
  let cum = 0;
  return candles.map(c => { cum += c.close >= c.open ? c.volume : -c.volume; return cum; });
}

// ═══════════════════════════════════════════════════════
//  Canvas layout
// ═══════════════════════════════════════════════════════

const canvas  = document.getElementById('chart');
const ctx     = canvas.getContext('2d');
const tooltip = document.getElementById('tooltip');

const PAD = { top: 12, right: 68, bottom: 24, left: 4 };
const PANEL_H = 130;
const VOL_H   = 72;

function activePanels() {
  return ['macd', 'rsi', 'cvd'].filter(p => S.panels[p]);
}

function calcCanvasHeight() {
  return 520 + activePanels().length * PANEL_H;
}

function resizeCanvas() {
  const cont = canvas.parentElement;
  canvas.width  = cont.clientWidth;
  canvas.height = calcCanvasHeight();
}

function layout() {
  const W  = canvas.width;
  const H  = canvas.height;
  const cL = PAD.left;
  const cR = W - PAD.right;
  const cW = cR - cL;

  const panels = activePanels();
  const mainH  = H - PAD.top - PAD.bottom - VOL_H - panels.length * PANEL_H;
  const mainT  = PAD.top;
  const mainB  = mainT + mainH;
  const volT   = mainB;
  const volB   = volT + VOL_H;

  const panelRects = panels.map((name, i) => ({
    name,
    top:    volB + i * PANEL_H + 2,
    bottom: volB + (i + 1) * PANEL_H - 2,
    height: PANEL_H - 4,
  }));

  return { W, H, cL, cR, cW,
    main: { top: mainT, bottom: mainB, height: mainH },
    vol:  { top: volT, bottom: volB, height: VOL_H },
    panels: panelRects,
  };
}

// ═══════════════════════════════════════════════════════
//  Coordinate helpers
// ═══════════════════════════════════════════════════════

function priceY(price, lo, hi, top, h) {
  if (hi === lo) return top + h / 2;
  return top + h - ((price - lo) / (hi - lo)) * h;
}

function idxX(idx, ly) {
  const { start, count } = S.view;
  return ly.cL + ((idx - start) / count) * ly.cW;
}

function xToIdx(x, ly) {
  const { start, count } = S.view;
  return Math.round(start + ((x - ly.cL) / ly.cW) * count);
}

function cw(ly) {
  return Math.max(1, (ly.cW / S.view.count) * 0.8);
}

function visRange(candles) {
  const { start, count } = S.view;
  const end = Math.min(start + count, candles.length);
  let lo = Infinity, hi = -Infinity;
  for (let i = start; i < end; i++) {
    if (i < 0 || i >= candles.length) continue;
    lo = Math.min(lo, candles[i].low);
    hi = Math.max(hi, candles[i].high);
  }
  if (!isFinite(lo)) { lo = 0; hi = 1; }
  const pad = (hi - lo) * 0.05 || 1;
  return { lo: lo - pad, hi: hi + pad };
}

// ═══════════════════════════════════════════════════════
//  Drawing functions
// ═══════════════════════════════════════════════════════

function clear(ly) {
  ctx.fillStyle = '#0a0e17';
  ctx.fillRect(0, 0, ly.W, ly.H);
}

function drawDividers(ly) {
  ctx.strokeStyle = 'rgba(255,255,255,0.06)';
  ctx.lineWidth = 1;
  // Between main and volume
  ctx.beginPath(); ctx.moveTo(ly.cL, ly.vol.top); ctx.lineTo(ly.cR, ly.vol.top); ctx.stroke();
  // Between volume and panels
  if (ly.panels.length) {
    ctx.beginPath(); ctx.moveTo(ly.cL, ly.vol.bottom); ctx.lineTo(ly.cR, ly.vol.bottom); ctx.stroke();
  }
  // Between panels
  for (const p of ly.panels) {
    ctx.beginPath(); ctx.moveTo(ly.cL, p.top - 2); ctx.lineTo(ly.cR, p.top - 2); ctx.stroke();
  }
}

function drawGrid(ly, lo, hi) {
  const { main, cL, cR, W } = ly;
  ctx.strokeStyle = 'rgba(255,255,255,0.045)';
  ctx.lineWidth = 1;
  ctx.setLineDash([]);

  // Horizontal lines + price labels
  const steps = 6;
  ctx.font = '10px JetBrains Mono, monospace';
  ctx.fillStyle = 'rgba(226,232,240,0.45)';
  ctx.textAlign = 'left';
  for (let i = 0; i <= steps; i++) {
    const price = lo + (hi - lo) * (i / steps);
    const y = priceY(price, lo, hi, main.top, main.height);
    ctx.beginPath(); ctx.moveTo(cL, y); ctx.lineTo(cR, y); ctx.stroke();
    const label = price >= 1000 ? price.toFixed(1) : price >= 1 ? price.toFixed(4) : price.toPrecision(4);
    ctx.fillText(label, cR + 4, y + 3);
  }

  // Vertical lines + time labels
  const { start, count } = S.view;
  const step = Math.max(1, Math.round(count / 7));
  ctx.textAlign = 'center';
  ctx.fillStyle = 'rgba(226,232,240,0.35)';
  for (let i = start; i < start + count; i += step) {
    if (i < 0 || i >= S.candles.length) continue;
    const x = idxX(i, ly);
    ctx.beginPath(); ctx.moveTo(x, main.top); ctx.lineTo(x, ly.vol.bottom); ctx.stroke();
    const d = new Date(S.candles[i].time * 1000);
    const label = S.timeframe === '1d'
      ? d.toISOString().slice(5, 10)
      : d.toISOString().slice(11, 16);
    ctx.fillText(label, x, ly.vol.bottom + 15);
  }
}

function drawCandles(ly, lo, hi) {
  const { start, count } = S.view;
  const { main } = ly;
  const w = cw(ly);
  const end = Math.min(start + count, S.candles.length);

  for (let i = start; i < end; i++) {
    if (i < 0) continue;
    const c = S.candles[i];
    const x = idxX(i, ly);
    const green = c.close >= c.open;
    const col   = green ? '#22c55e' : '#ef4444';

    const yH = priceY(c.high,  lo, hi, main.top, main.height);
    const yL = priceY(c.low,   lo, hi, main.top, main.height);
    const yO = priceY(c.open,  lo, hi, main.top, main.height);
    const yC = priceY(c.close, lo, hi, main.top, main.height);

    ctx.strokeStyle = col;
    ctx.lineWidth   = 1;
    ctx.beginPath(); ctx.moveTo(x, yH); ctx.lineTo(x, yL); ctx.stroke();

    ctx.fillStyle = col;
    const bTop = Math.min(yO, yC);
    const bH   = Math.max(1, Math.abs(yC - yO));
    ctx.fillRect(x - w / 2, bTop, w, bH);
  }
}

function drawEMAs(ly, lo, hi) {
  const { main } = ly;
  const { start, count } = S.view;
  const closes = S.candles.map(c => c.close);
  const e20 = ema(closes, 20);
  const e50 = ema(closes, 50);
  const end  = Math.min(start + count, S.candles.length);

  const drawLine = (vals, color, width) => {
    ctx.strokeStyle = color;
    ctx.lineWidth   = width;
    ctx.beginPath();
    let started = false;
    for (let i = Math.max(0, start - 1); i < end; i++) {
      if (vals[i] == null) { started = false; continue; }
      const x = idxX(i, ly);
      const y = priceY(vals[i], lo, hi, main.top, main.height);
      if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
    }
    ctx.stroke();
  };

  drawLine(e20, 'rgba(251,191,36,0.75)', 1.5);
  drawLine(e50, 'rgba(96,165,250,0.65)', 1.5);
}

function drawVolume(ly) {
  const { vol } = ly;
  const { start, count } = S.view;
  const end  = Math.min(start + count, S.candles.length);
  const w    = cw(ly);
  const vols = S.candles.slice(start, end).map(c => c.volume);
  const maxV = Math.max(...vols, 1);

  for (let i = start; i < end; i++) {
    if (i < 0) continue;
    const c  = S.candles[i];
    const x  = idxX(i, ly);
    const h  = (c.volume / maxV) * vol.height * 0.9;
    ctx.fillStyle = c.close >= c.open ? 'rgba(34,197,94,0.45)' : 'rgba(239,68,68,0.45)';
    ctx.fillRect(x - w / 2, vol.bottom - h, w, h);
  }

  ctx.fillStyle = 'rgba(100,116,139,0.6)';
  ctx.font = '9px JetBrains Mono, monospace';
  ctx.textAlign = 'left';
  ctx.fillText('VOL', ly.cL + 4, vol.top + 12);
}

function drawTradeMarkers(ly, lo, hi) {
  if (!S.trades.length) return;
  const { start, count } = S.view;
  const { main } = ly;
  const end = Math.min(start + count, S.candles.length);
  const w   = cw(ly);

  for (const tr of S.trades) {
    const bName = tr.bot_name;
    if (!S.botVis[bName]) continue;
    const color = BOT_COLORS[bName] || '#fff';
    const label = BOT_LABELS[bName] || bName[0];

    // Find candle nearest to trade timestamp (trade.timestamp = exit time)
    const ts  = Math.floor(new Date(tr.timestamp).getTime() / 1000);
    const idx = nearestCandleIdx(ts);
    if (idx < start || idx >= end) continue;

    const x      = idxX(idx, ly);
    const ep     = parseFloat(tr.exit_price);
    const y      = priceY(ep, lo, hi, main.top, main.height);
    const pnl    = parseFloat(tr.net_pnl);
    const isWin  = pnl >= 0;

    // Draw marker
    drawMarker(x, y, color, label, isWin);
  }
}

function drawMarker(x, y, color, label, isWin) {
  const offset = isWin ? 14 : -14;
  const my = y + offset;

  ctx.fillStyle = color;
  ctx.beginPath();
  if (isWin) {
    // Up triangle
    ctx.moveTo(x, my - 8); ctx.lineTo(x - 5, my + 2); ctx.lineTo(x + 5, my + 2);
  } else {
    // Down triangle
    ctx.moveTo(x, my + 8); ctx.lineTo(x - 5, my - 2); ctx.lineTo(x + 5, my - 2);
  }
  ctx.closePath(); ctx.fill();

  ctx.fillStyle = '#fff';
  ctx.font = 'bold 7px JetBrains Mono, monospace';
  ctx.textAlign = 'center';
  ctx.fillText(label, x, isWin ? my + 12 : my - 4);
}

function drawCrosshair(ly, lo, hi) {
  if (!S.hover.active) return;
  const { x, y, idx } = S.hover;
  const { main, cL, cR, vol } = ly;

  ctx.strokeStyle = 'rgba(226,232,240,0.3)';
  ctx.lineWidth   = 1;
  ctx.setLineDash([4, 4]);
  ctx.beginPath(); ctx.moveTo(x, main.top); ctx.lineTo(x, vol.bottom); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(cL, y); ctx.lineTo(cR, y); ctx.stroke();
  ctx.setLineDash([]);

  // Price label on y axis
  if (y >= main.top && y <= main.bottom) {
    const price = lo + (hi - lo) * (1 - (y - main.top) / main.height);
    const label = price >= 1000 ? price.toFixed(1) : price.toFixed(4);
    ctx.fillStyle = '#1e293b';
    ctx.fillRect(cR + 1, y - 8, 64, 16);
    ctx.fillStyle = '#e2e8f0';
    ctx.font = '10px JetBrains Mono, monospace';
    ctx.textAlign = 'left';
    ctx.fillText(label, cR + 4, y + 3);
  }
}

// ── MACD Panel ──────────────────────────────────────────

function drawMACD(ly) {
  const rect = ly.panels.find(p => p.name === 'macd');
  if (!rect) return;

  const { start, count } = S.view;
  const closes = S.candles.map(c => c.close);
  const { line, sig, hist } = calcMACD(closes);

  // Visible range for scaling
  const end   = Math.min(start + count, S.candles.length);
  const vals  = [...line, ...sig, ...hist].filter((v, i) => {
    const ci = i % S.candles.length;
    return v != null && ci >= start && ci < end;
  });
  if (!vals.length) return;
  const lo = Math.min(...vals.filter(Boolean));
  const hi = Math.max(...vals.filter(Boolean));
  const pad = Math.max(Math.abs(hi - lo) * 0.1, 0.001);

  const panelLo = lo - pad, panelHi = hi + pad;

  // Panel label
  ctx.fillStyle = 'rgba(100,116,139,0.6)';
  ctx.font = '9px JetBrains Mono, monospace';
  ctx.textAlign = 'left';
  ctx.fillText('MACD', ly.cL + 4, rect.top + 12);

  // Zero line
  const zy = priceY(0, panelLo, panelHi, rect.top, rect.height);
  ctx.strokeStyle = 'rgba(255,255,255,0.1)'; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(ly.cL, zy); ctx.lineTo(ly.cR, zy); ctx.stroke();

  // Histogram
  const w = cw(ly);
  for (let i = start; i < end; i++) {
    if (i < 0 || hist[i] == null) continue;
    const x  = idxX(i, ly);
    const y0 = priceY(0, panelLo, panelHi, rect.top, rect.height);
    const y1 = priceY(hist[i], panelLo, panelHi, rect.top, rect.height);
    ctx.fillStyle = hist[i] >= 0 ? 'rgba(34,197,94,0.6)' : 'rgba(239,68,68,0.6)';
    ctx.fillRect(x - w / 2, Math.min(y0, y1), w, Math.abs(y1 - y0) || 1);
  }

  // MACD line
  drawIndicatorLine(line, ly, rect, panelLo, panelHi, '#60a5fa', 1.5);
  // Signal line
  drawIndicatorLine(sig, ly, rect, panelLo, panelHi, '#f97316', 1.2);
}

// ── RSI Panel ───────────────────────────────────────────

function drawRSI(ly) {
  const rect = ly.panels.find(p => p.name === 'rsi');
  if (!rect) return;

  const closes = S.candles.map(c => c.close);
  const rsiVals = calcRSI(closes);
  const { start, count } = S.view;

  ctx.fillStyle = 'rgba(100,116,139,0.6)';
  ctx.font = '9px JetBrains Mono, monospace';
  ctx.textAlign = 'left';
  ctx.fillText('RSI(14)', ly.cL + 4, rect.top + 12);

  const panelLo = 0, panelHi = 100;

  // Overbought / oversold zones
  const y30 = priceY(30, panelLo, panelHi, rect.top, rect.height);
  const y70 = priceY(70, panelLo, panelHi, rect.top, rect.height);
  ctx.fillStyle = 'rgba(239,68,68,0.08)';
  ctx.fillRect(ly.cL, rect.top, ly.cW, y70 - rect.top);
  ctx.fillStyle = 'rgba(34,197,94,0.08)';
  ctx.fillRect(ly.cL, y30, ly.cW, rect.bottom - y30);

  // Level lines
  ctx.strokeStyle = 'rgba(255,255,255,0.12)'; ctx.lineWidth = 1;
  ctx.setLineDash([3, 3]);
  for (const lv of [30, 50, 70]) {
    const y = priceY(lv, panelLo, panelHi, rect.top, rect.height);
    ctx.beginPath(); ctx.moveTo(ly.cL, y); ctx.lineTo(ly.cR, y); ctx.stroke();
    ctx.fillStyle = 'rgba(226,232,240,0.35)';
    ctx.font = '9px JetBrains Mono, monospace';
    ctx.textAlign = 'left';
    ctx.fillText(lv, ly.cR + 4, y + 3);
  }
  ctx.setLineDash([]);

  drawIndicatorLine(rsiVals, ly, rect, panelLo, panelHi, '#a855f7', 1.5);
}

// ── CVD Panel ───────────────────────────────────────────

function drawCVD(ly) {
  const rect = ly.panels.find(p => p.name === 'cvd');
  if (!rect) return;

  const cvdVals = calcCVD(S.candles);
  const { start, count } = S.view;
  const end  = Math.min(start + count, S.candles.length);
  const vis  = cvdVals.slice(start, end).filter(v => v != null);
  if (!vis.length) return;

  const lo  = Math.min(...vis);
  const hi  = Math.max(...vis);
  const pad = Math.max(Math.abs(hi - lo) * 0.05, 1);

  ctx.fillStyle = 'rgba(100,116,139,0.6)';
  ctx.font = '9px JetBrains Mono, monospace';
  ctx.textAlign = 'left';
  ctx.fillText('CVD', ly.cL + 4, rect.top + 12);

  drawIndicatorLine(cvdVals, ly, rect, lo - pad, hi + pad, '#f59e0b', 1.5);
}

function drawIndicatorLine(vals, ly, rect, lo, hi, color, width) {
  const { start, count } = S.view;
  const end = Math.min(start + count, S.candles.length);
  ctx.strokeStyle = color;
  ctx.lineWidth   = width;
  ctx.beginPath();
  let started = false;
  for (let i = Math.max(0, start - 1); i < end; i++) {
    if (vals[i] == null) { started = false; continue; }
    const x = idxX(i, ly);
    const y = priceY(vals[i], lo, hi, rect.top, rect.height);
    if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
  }
  ctx.stroke();
}

// ═══════════════════════════════════════════════════════
//  Main render
// ═══════════════════════════════════════════════════════

function render() {
  if (!S.candles.length) return;
  const ly = layout();
  const { lo, hi } = visRange(S.candles);

  clear(ly);
  drawDividers(ly);
  drawGrid(ly, lo, hi);
  drawVolume(ly);
  drawCandles(ly, lo, hi);
  drawEMAs(ly, lo, hi);
  drawTradeMarkers(ly, lo, hi);
  drawMACD(ly);
  drawRSI(ly);
  drawCVD(ly);
  drawCrosshair(ly, lo, hi);
}

// ═══════════════════════════════════════════════════════
//  Tooltip helpers
// ═══════════════════════════════════════════════════════

function showTooltip(evt, html) {
  tooltip.innerHTML = html;
  tooltip.classList.remove('hidden');
  const x = evt.clientX + 14;
  const y = evt.clientY + 14;
  tooltip.style.left = Math.min(x, window.innerWidth - 180) + 'px';
  tooltip.style.top  = Math.min(y, window.innerHeight - 120) + 'px';
}

function hideTooltip() {
  tooltip.classList.add('hidden');
}

function candleTooltipHTML(c) {
  const d = new Date(c.time * 1000);
  const ts = d.toISOString().replace('T', ' ').slice(0, 19) + ' UTC';
  const pct = ((c.close - c.open) / c.open * 100).toFixed(2);
  const col = c.close >= c.open ? '#22c55e' : '#ef4444';
  return `
    <div class="tooltip-row"><span class="tooltip-label">${ts}</span></div>
    <div class="tooltip-row"><span class="tooltip-label">O</span><span class="tooltip-val">${fmt(c.open)}</span></div>
    <div class="tooltip-row"><span class="tooltip-label">H</span><span class="tooltip-val">${fmt(c.high)}</span></div>
    <div class="tooltip-row"><span class="tooltip-label">L</span><span class="tooltip-val">${fmt(c.low)}</span></div>
    <div class="tooltip-row"><span class="tooltip-label">C</span><span class="tooltip-val" style="color:${col}">${fmt(c.close)}</span></div>
    <div class="tooltip-row"><span class="tooltip-label">Chg</span><span class="tooltip-val" style="color:${col}">${pct}%</span></div>
    <div class="tooltip-row"><span class="tooltip-label">Vol</span><span class="tooltip-val">${fmtVol(c.volume)}</span></div>`;
}

function fmt(n) {
  if (n == null) return '—';
  return n >= 1000 ? n.toFixed(2) : n >= 1 ? n.toFixed(4) : n.toPrecision(5);
}

function fmtVol(n) {
  if (n >= 1e6) return (n / 1e6).toFixed(2) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(2) + 'K';
  return n.toFixed(2);
}

// ═══════════════════════════════════════════════════════
//  Events — mouse interaction
// ═══════════════════════════════════════════════════════

function setupCanvasEvents() {
  const cont = canvas.parentElement;

  cont.addEventListener('mousemove', e => {
    const rect  = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const x = (e.clientX - rect.left) * scaleX;
    const y = (e.clientY - rect.top)  * scaleX;
    const ly = layout();
    const idx = clamp(xToIdx(x, ly), 0, S.candles.length - 1);

    S.hover = { active: true, x: idxX(idx, ly), y, idx };

    if (S.drag.on) {
      const dx     = e.clientX - S.drag.startX;
      const dCandles = Math.round(-dx / (ly.cW / S.view.count));
      S.view.start = clamp(S.drag.startView + dCandles, 0, S.candles.length - S.view.count);
    }

    // Candle tooltip
    if (S.candles[idx]) {
      showTooltip(e, candleTooltipHTML(S.candles[idx]));
    }
    render();
  });

  cont.addEventListener('mouseleave', () => {
    S.hover.active = false;
    hideTooltip();
    render();
  });

  cont.addEventListener('mousedown', e => {
    S.drag = { on: true, startX: e.clientX, startView: S.view.start };
    cont.style.cursor = 'grabbing';
  });

  window.addEventListener('mouseup', () => {
    if (S.drag.on) { S.drag.on = false; canvas.parentElement.style.cursor = 'crosshair'; }
  });

  cont.addEventListener('wheel', e => {
    e.preventDefault();
    const ly    = layout();
    const delta = e.deltaY > 0 ? 1.15 : 0.87;
    const oldCount = S.view.count;
    S.view.count = clamp(Math.round(S.view.count * delta), 20, S.candles.length);

    // Zoom toward hover point
    const hoverFrac = S.hover.active ? (S.hover.x - ly.cL) / ly.cW : 0.5;
    const pivotIdx  = S.view.start + hoverFrac * oldCount;
    S.view.start    = clamp(Math.round(pivotIdx - hoverFrac * S.view.count), 0, S.candles.length - S.view.count);
    render();
  }, { passive: false });
}

// ═══════════════════════════════════════════════════════
//  Header / button wiring
// ═══════════════════════════════════════════════════════

function setupHeaderEvents() {
  document.getElementById('universe-list').addEventListener('click', e => {
    const item = e.target.closest('[data-symbol]');
    if (!item) return;
    selectSymbol(item.dataset.symbol);
  });

  document.getElementById('tf-selector').addEventListener('click', e => {
    const btn = e.target.closest('[data-tf]');
    if (!btn) return;
    document.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    S.timeframe = btn.dataset.tf;
    loadCandles();
  });

  document.getElementById('controls').addEventListener('click', e => {
    const panelBtn = e.target.closest('[data-panel]');
    if (panelBtn) {
      const p = panelBtn.dataset.panel;
      S.panels[p] = !S.panels[p];
      panelBtn.classList.toggle('active', S.panels[p]);
      resizeCanvas();
      render();
      return;
    }
    const legItem = e.target.closest('[data-bot]');
    if (legItem) {
      const bot = legItem.dataset.bot;
      S.botVis[bot] = !S.botVis[bot];
      legItem.classList.toggle('hidden', !S.botVis[bot]);
      render();
    }
  });
}

// ═══════════════════════════════════════════════════════
//  Data fetching
// ═══════════════════════════════════════════════════════

async function loadUniverse() {
  try {
    const r = await apiFetch(`${BASE}/api/universe`);
    S.universe = await r.json();
    renderUniverseList();
  } catch (err) {
    console.error('loadUniverse:', err);
  }
}

function selectSymbol(symbol) {
  if (!symbol || symbol === S.symbol) return;
  S.symbol = symbol;
  const pairEl = document.getElementById('header-pair');
  if (pairEl) pairEl.textContent = symbol;
  document.querySelectorAll('.uni-item').forEach(el => {
    el.classList.toggle('active', el.dataset.symbol === symbol);
  });
  loadAll();
}

function renderUniverseList() {
  const listEl = document.getElementById('universe-list');
  const countEl = document.getElementById('uni-count');
  const metaEl  = document.getElementById('universe-meta');
  if (!listEl || !S.universe) return;

  const u = S.universe;
  const rows = u.symbols || [];

  if (countEl) {
    countEl.textContent = u.active_count != null ? String(u.active_count) : rows.length;
  }

  if (metaEl) {
    if (u.dynamic_enabled) {
      const base = `${u.active_count} hareketli coin · ${u.pinned_count || 0} açık pozisyon`;
      if (u.scan_status === 'fallback') {
        metaEl.textContent = `⚠ fallback (4 statik) — tarama başarısız`;
        metaEl.className = 'uni-meta uni-meta--warn';
      } else if (u.scan_status === 'partial') {
        metaEl.textContent = `⚠ ${u.scan_message || base}`;
        metaEl.className = 'uni-meta uni-meta--warn';
      } else {
        metaEl.textContent = u.scan_message ? `${base} · ${u.scan_message}` : base;
        metaEl.className = 'uni-meta';
      }
    } else {
      metaEl.textContent = 'Statik evren (dinamik tarama kapalı)';
      metaEl.className = 'uni-meta';
    }
  }

  if (!rows.length) {
    listEl.innerHTML = '<div class="rp-empty">Coin bulunamadı</div>';
    return;
  }

  let html = '';
  for (const row of rows) {
    const activeCls = row.symbol === S.symbol ? ' active' : '';
    const tags = [];
    if (row.in_universe) tags.push('<span class="uni-tag uni-tag--active">aktif</span>');
    if (row.pinned) tags.push('<span class="uni-tag uni-tag--pinned">pos</span>');
    if (row.score != null) tags.push(`<span class="uni-tag uni-tag--score">${row.score.toFixed(1)}</span>`);

    const bots = (row.bots_with_position || []).join(', ');
    const sub = bots
      ? `<span class="uni-pos-bots">${bots}</span>`
      : (row.in_universe ? 'işlem için seçili' : 'whitelist');

    html += `<div class="uni-item${activeCls}" data-symbol="${row.symbol}">
      <div class="uni-row-top">
        <span class="uni-base">${row.base}</span>
        <span class="uni-quote">/USDT</span>
        <span class="uni-badges">${tags.join('')}</span>
      </div>
      <div class="uni-row-sub">${sub}</div>
    </div>`;
  }
  listEl.innerHTML = html;

  // İlk yüklemede seçili sembol listede yoksa ilk aktif coin'e geç
  const symbols = rows.map(r => r.symbol);
  if (!symbols.includes(S.symbol)) {
    const first = rows.find(r => r.in_universe) || rows[0];
    if (first) {
      S.symbol = first.symbol;
      const pairEl = document.getElementById('header-pair');
      if (pairEl) pairEl.textContent = S.symbol;
      document.querySelectorAll('.uni-item').forEach(el => {
        el.classList.toggle('active', el.dataset.symbol === S.symbol);
      });
      loadCandles();
      connectWS();
    }
  }
}

async function loadCandles() {
  try {
    const sym = S.symbol.replace('/', '-');
    const r   = await apiFetch(`${BASE}/api/ohlcv/${sym}?timeframe=${S.timeframe}&limit=300`);
    S.candles = await r.json();
    // Default view: show last 120 candles
    S.view.count = 120;
    S.view.start = Math.max(0, S.candles.length - S.view.count);
    resizeCanvas();
    render();
  } catch (err) {
    console.error('loadCandles:', err);
  }
}

async function loadTrades() {
  try {
    const r = await apiFetch(`${BASE}/api/trades`);
    S.trades = await r.json();
    render();
    renderRecentTrades();
    renderStatusBar();
  } catch (err) {
    console.error('loadTrades:', err);
  }
}

async function loadStats() {
  try {
    const r   = await apiFetch(`${BASE}/api/stats`);
    S.stats   = await r.json();
    renderPortfolioCards();
    renderPnLSummary();
    renderOpenPositions();
    renderStatusBar();
    renderUniverseList();
  } catch (err) {
    console.error('loadStats:', err);
  }
}

async function loadPortfolio() {
  try {
    const r = await apiFetch(`${BASE}/api/portfolio`);
    S.portfolio = await r.json();
    renderPortfolioCards();
    renderPnLSummary();
  } catch (err) {
    console.error('loadPortfolio:', err);
    renderPortfolioCards();
  }
}

function getPortfolioView() {
  if (S.portfolio) return S.portfolio;
  if (!S.stats.length) return null;

  const initial = S.stats.reduce((s, b) => s + (b.start_balance || 0), 0) || 1000;
  const total_balance    = S.stats.reduce((s, b) => s + b.balance, 0);
  const total_equity     = S.stats.reduce((s, b) => s + (b.equity ?? b.balance), 0);
  const total_unrealized = S.stats.reduce((s, b) => s + (b.unrealized_pnl || 0), 0);
  const daily_pnl        = S.stats.reduce((s, b) => s + (b.daily_pnl || 0), 0);
  const daily_realized   = S.stats.reduce((s, b) => s + (b.daily_realized_pnl || 0), 0);
  const total_pnl        = S.stats.reduce((s, b) => s + b.total_pnl, 0);
  const trades_today     = S.stats.reduce((s, b) => s + (b.trades_today || 0), 0);
  const open_positions   = S.stats.reduce((s, b) => s + (b.open_positions?.length || 0), 0);

  return {
    initial_balance:    initial,
    total_balance,
    total_equity,
    total_unrealized,
    daily_pnl,
    daily_pnl_pct:      initial ? (daily_pnl / initial * 100) : 0,
    daily_realized_pnl: daily_realized,
    total_pnl,
    total_return_pct:   initial ? ((total_equity - initial) / initial * 100) : 0,
    trades_today,
    open_positions,
    bots: S.stats,
  };
}

function renderPortfolioCards() {
  const p = getPortfolioView();
  const eqEl   = document.getElementById('wallet-equity');
  const dayEl  = document.getElementById('wallet-daily');
  const totEl  = document.getElementById('wallet-total-pnl');
  const balSub = document.getElementById('wallet-balance-sub');
  const daySub = document.getElementById('wallet-daily-sub');
  const retSub = document.getElementById('wallet-return-sub');
  if (!eqEl || !p) return;

  const fmtSigned = (v, dec = 2) => `${v >= 0 ? '+' : ''}${v.toFixed(dec)}`;
  const cls = (v) => (v >= 0 ? 'pos' : 'neg');

  eqEl.textContent = `${p.total_equity.toFixed(2)} USDT`;
  eqEl.className   = 'portfolio-value';

  if (balSub) {
    balSub.textContent = `nakit ${p.total_balance.toFixed(2)} · unrealized ${fmtSigned(p.total_unrealized)}`;
    balSub.className   = 'portfolio-sub';
  }

  dayEl.textContent = `${fmtSigned(p.daily_pnl)} USDT`;
  dayEl.className   = `portfolio-value ${cls(p.daily_pnl)}`;
  if (daySub) {
    daySub.textContent = `${fmtSigned(p.daily_pnl_pct)}% · ${p.trades_today} işlem bugün`;
    daySub.className   = `portfolio-sub ${cls(p.daily_pnl)}`;
  }

  totEl.textContent = `${fmtSigned(p.total_pnl)} USDT`;
  totEl.className   = `portfolio-value ${cls(p.total_pnl)}`;
  if (retSub) {
    retSub.textContent = `${fmtSigned(p.total_return_pct)}% · başlangıç ${p.initial_balance.toFixed(0)} USDT`;
    retSub.className   = `portfolio-sub ${cls(p.total_pnl)}`;
  }
}

async function loadSignals() {
  try {
    const sym = encodeURIComponent(S.symbol);
    const r   = await apiFetch(`${BASE}/api/signals?symbol=${sym}`);
    S.signals = await r.json();
    renderStatusBar();
  } catch (err) {
    console.error('loadSignals:', err);
  }
}

async function loadTicker() {
  try {
    const sym = S.symbol.replace('/', '-');
    const r   = await apiFetch(`${BASE}/api/ticker/${sym}`);
    const d   = await r.json();
    updateLivePrice(d, null);
  } catch (err) {
    console.error('loadTicker:', err);
  }
}

async function loadAll() {
  await loadUniverse();
  await loadCandles();
  await Promise.all([loadTrades(), loadStats(), loadPortfolio(), loadSignals(), loadTicker()]);
  connectWS();
}

// ═══════════════════════════════════════════════════════
//  WebSocket — live price
// ═══════════════════════════════════════════════════════

let _ws = null;
let _wsRetry = 0;

function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const sym   = S.symbol.replace('/', '-');
  const url   = `${proto}://${location.host}/ws/price/${sym}`;

  // Close any existing connection and reset backoff
  if (_ws) { _ws.onclose = null; try { _ws.close(); } catch (_) {} _ws = null; }
  _wsRetry = 0;

  _ws = new WebSocket(url);

  _ws.onopen = () => {
    S.wsConnected = true;
    _wsRetry = 0;
    updateWSStatus(true);
  };

  _ws.onmessage = e => {
    try {
      const d = JSON.parse(e.data);
      if (d.last) {
        const prev = S.livePrice;
        S.livePrice = d.last;
        S.livePrices[S.symbol] = d.last;
        updateLivePrice(d, prev);
        renderOpenPositions();
      }
    } catch (_) {}
  };

  _ws.onclose = () => {
    S.wsConnected = false;
    updateWSStatus(false);
    // Reconnect with backoff
    const delay = Math.min(1000 * 2 ** _wsRetry, 30000);
    _wsRetry++;
    setTimeout(connectWS, delay);
  };

  _ws.onerror = () => { if (_ws) _ws.close(); };
}

// ═══════════════════════════════════════════════════════
//  UI update helpers
// ═══════════════════════════════════════════════════════

function updateWSStatus(connected) {
  const dot   = document.getElementById('ws-dot');
  const label = document.getElementById('ws-label');
  dot.className   = 'ws-dot ' + (connected ? 'connected' : 'disconnected');
  label.textContent = connected ? 'live' : 'connecting…';
}

function updateLivePrice(d, prev) {
  const el     = document.getElementById('live-price');
  const chEl   = document.getElementById('price-change');
  const metaEl = document.getElementById('price-meta');

  el.textContent = fmt(d.last);

  // Flash animation
  el.className = '';
  if (prev && d.last > prev) el.className = 'flash-up';
  else if (prev && d.last < prev) el.className = 'flash-down';
  setTimeout(() => { el.className = ''; }, 400);

  const pct = d.change_pct;
  chEl.textContent = (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%';
  chEl.className   = 'price-change ' + (pct >= 0 ? 'pos' : 'neg');

  metaEl.innerHTML = `H: ${fmt(d.high_24h)} &nbsp; L: ${fmt(d.low_24h)}` +
    (d.volume_24h ? ` &nbsp; Vol: ${fmtVol(d.volume_24h)}` : '');

  const pairEl = document.getElementById('header-pair');
  if (pairEl) pairEl.textContent = S.symbol;
}

// ── Right panel Section 1: P&L overview ─────────────────

function renderPnLSummary() {
  const el = document.getElementById('pnl-content');
  if (!el || !S.stats.length) return;

  let totalPnl = 0, totalEquity = 0, totalDaily = 0;

  let html = `<div class="pnl-table">
    <div class="pnl-head"><span>Bot</span><span>Toplam</span><span>Bugün</span><span>Kasa</span></div>`;

  for (const s of S.stats) {
    const dayPnl = s.daily_pnl ?? 0;
    const equity = s.equity ?? s.balance;
    totalPnl    += s.total_pnl;
    totalEquity += equity;
    totalDaily  += dayPnl;
    const col = BOT_COLORS[s.bot] || '#fff';
    const paused = s.paused ? ' ⏸' : '';
    html += `<div class="pnl-row">
      <span class="pnl-bot-name" style="color:${col}">${s.bot}${paused}</span>
      <span class="${s.total_pnl >= 0 ? 'pos' : 'neg'}">${s.total_pnl >= 0 ? '+' : ''}${s.total_pnl.toFixed(2)}</span>
      <span class="${dayPnl >= 0 ? 'pos' : 'neg'}">${dayPnl >= 0 ? '+' : ''}${dayPnl.toFixed(2)}</span>
      <span class="pnl-bal">${equity.toFixed(2)}</span>
    </div>`;
  }

  const p = getPortfolioView();
  html += `<div class="pnl-total-row">
    <span>TOPLAM</span>
    <span class="${totalPnl >= 0 ? 'pos' : 'neg'}">${totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)}</span>
    <span class="${totalDaily >= 0 ? 'pos' : 'neg'}">${totalDaily >= 0 ? '+' : ''}${totalDaily.toFixed(2)}</span>
    <span class="pnl-bal">${(p?.total_equity ?? totalEquity).toFixed(2)}</span>
  </div></div>`;

  el.innerHTML = html;
}

// ── Right panel Section 2: Open positions ────────────────

function renderOpenPositions() {
  const el      = document.getElementById('positions-content');
  const countEl = document.getElementById('pos-count');
  if (!el) return;

  const positions = [];
  for (const s of S.stats) {
    for (const p of (s.open_positions || [])) {
      positions.push({ ...p, botName: s.bot });
    }
  }

  if (countEl) countEl.textContent = positions.length ? positions.length : '';

  if (!positions.length) {
    el.innerHTML = '<div class="rp-empty">No open positions</div>';
    return;
  }

  const now = Date.now();
  let html = '';
  for (const p of positions) {
    const live    = p.current_price || S.livePrices[p.symbol] || p.entry_price;
    const pnlPct  = p.unrealized_pct ?? ((live - p.entry_price) / p.entry_price * 100);
    const pnlUSDT = p.unrealized_pnl ?? ((live - p.entry_price) * p.size);
    const col     = BOT_COLORS[p.botName] || '#fff';
    const dur     = p.opened_at ? fmtDur(now - new Date(p.opened_at).getTime()) : '—';

    html += `<div class="pos-card">
      <div class="pos-header">
        <span class="pos-bot" style="color:${col}">${p.botName}</span>
        <span class="pos-sym">${p.symbol}</span>
        <span class="pos-side">${(p.side || 'long').toUpperCase()}</span>
        <span class="pos-dur">${dur}</span>
      </div>
      <div class="pos-prices">
        <span class="pos-lbl">Entry</span>
        <span class="pos-val">${fmt(p.entry_price)}</span>
        <span class="pos-arrow">→</span>
        <span class="pos-lbl">Now</span>
        <span class="pos-val">${fmt(live)}</span>
      </div>
      <div class="pos-pnl ${pnlPct >= 0 ? 'pos' : 'neg'}">
        ${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%
        <span class="pos-pnl-usdt">(${pnlUSDT >= 0 ? '+' : ''}${pnlUSDT.toFixed(4)} USDT)</span>
      </div>
    </div>`;
  }
  el.innerHTML = html;
}

// ── Right panel Section 3: Recent trades ─────────────────

function renderRecentTrades() {
  const el = document.getElementById('trades-content');
  if (!el) return;

  const recent = S.trades.slice().reverse().slice(0, 10);
  if (!recent.length) {
    el.innerHTML = '<div class="rp-empty">No trades yet</div>';
    return;
  }

  let html = '';
  for (const t of recent) {
    const pnl = parseFloat(t.net_pnl || 0);
    const col = BOT_COLORS[t.bot_name] || '#fff';
    const ts  = fmtTime(t.timestamp);

    html += `<div class="trade-row ${pnl >= 0 ? 'trade-win' : 'trade-loss'}">
      <div class="trade-top">
        <span class="trade-bot" style="color:${col}">${t.bot_name}</span>
        <span class="trade-sym">${t.symbol}</span>
        <span class="trade-ts">${ts}</span>
      </div>
      <div class="trade-bottom">
        <span class="trade-prices">${fmt(parseFloat(t.entry_price))} → ${fmt(parseFloat(t.exit_price))}</span>
        <span class="trade-pnl ${pnl >= 0 ? 'pos' : 'neg'}">${pnl >= 0 ? '+' : ''}${pnl.toFixed(4)}</span>
      </div>
    </div>`;
  }
  el.innerHTML = html;
}

// ── Status bar ────────────────────────────────────────────

function renderStatusBar() {
  const el = document.getElementById('status-bots');
  if (!el) return;

  let html = '';
  for (const s of S.stats) {
    const col   = BOT_COLORS[s.bot] || '#fff';
    const sig   = Array.isArray(S.signals) ? S.signals.find(sg => sg.bot === s.bot) : null;
    const last  = S.trades.slice().reverse().find(t => t.bot_name === s.bot);
    const lastT = last ? fmtTime(last.timestamp) : '—';
    const sigLbl   = sig?.signal ? sig.signal.toUpperCase() : '—';
    const sigColor = sig?.signal === 'buy' ? 'var(--green)' : sig?.signal ? 'var(--red)' : 'var(--text-dim)';

    html += `<div class="sb-bot">
      <span class="sb-dot ${s.paused ? 'paused' : 'active'}"></span>
      <span class="sb-name" style="color:${col}">${s.bot}</span>
      <span class="sb-stat">${s.equity?.toFixed(0) ?? s.balance.toFixed(0)} USDT</span>
      <span class="sb-stat ${(s.daily_pnl || 0) >= 0 ? 'pos' : 'neg'}">bugün ${(s.daily_pnl || 0) >= 0 ? '+' : ''}${(s.daily_pnl || 0).toFixed(2)}</span>
      <span class="sb-stat">${s.trades}t/${s.trades_today || 0}d</span>
      <span class="sb-stat">win ${s.win_rate.toFixed(0)}%</span>
      <span class="sb-stat">last ${lastT}</span>
      <span class="sb-sig" style="color:${sigColor}">${sigLbl}</span>
    </div>`;
  }
  el.innerHTML = html;
}

// ── Position price polling (non-chart-pair symbols) ───────

let _posPollTimer = null;

async function pollPositionPrices() {
  const needed = new Set();
  for (const s of S.stats) {
    for (const p of (s.open_positions || [])) {
      if (p.symbol !== S.symbol) needed.add(p.symbol);
    }
  }
  for (const sym of needed) {
    try {
      const r = await apiFetch(`${BASE}/api/ticker/${sym.replace('/', '-')}`);
      const d = await r.json();
      if (d.last) S.livePrices[sym] = d.last;
    } catch (_) {}
  }
  if (needed.size) renderOpenPositions();
}

function startPositionPricePolling() {
  clearInterval(_posPollTimer);
  _posPollTimer = setInterval(pollPositionPrices, 3000);
}

// ── Duration / time helpers ───────────────────────────────

function fmtDur(ms) {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  const d = Math.floor(h / 24);
  if (d > 0) return `${d}d ${h % 24}h`;
  if (h > 0) return `${h}h ${m % 60}m`;
  if (m > 0) return `${m}m`;
  return `${s}s`;
}

function fmtTime(isoStr) {
  if (!isoStr) return '—';
  try {
    return new Date(isoStr).toISOString().slice(11, 16);
  } catch (_) { return '—'; }
}

// ═══════════════════════════════════════════════════════
//  Utility
// ═══════════════════════════════════════════════════════

function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

function nearestCandleIdx(ts) {
  const candles = S.candles;
  if (!candles.length) return 0;
  let lo = 0, hi = candles.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (candles[mid].time < ts) lo = mid + 1; else hi = mid;
  }
  return lo;
}

// ═══════════════════════════════════════════════════════
//  Init
// ═══════════════════════════════════════════════════════

async function init() {
  // Resize on window change
  window.addEventListener('resize', () => { resizeCanvas(); render(); });

  setupCanvasEvents();
  setupHeaderEvents();
  updateWSStatus(false);
  const pairEl = document.getElementById('header-pair');
  if (pairEl) pairEl.textContent = S.symbol;

  // Initial data load (also calls connectWS internally)
  await loadAll();

  // Periodic refreshes — wallet/stats every 5s for live equity
  setInterval(loadPortfolio, 5_000);
  setInterval(loadStats,     5_000);
  setInterval(loadUniverse, 60_000);
  setInterval(loadTrades,  60_000);
  setInterval(loadSignals, 120_000);
}

init();
