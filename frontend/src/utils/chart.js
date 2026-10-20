// 结果集 → 图表的纯函数层：判定维度/度量、分组聚合、生成原生 SVG。
// 不碰 DOM，所以可以脱离浏览器做离线自检（web/chart.test.mjs）
import { isNum, numOf, fmtNum, fmtAxis, clip, esc } from "./format.js";

export const PALETTE = [
  "#5b60ee", "#e08700", "#16a34a", "#dc2626", "#0ea5e9",
  "#a855f7", "#14b8a6", "#f43f5e", "#ca8a04", "#64748b",
];

const TIME_COL_RE = /(date|month|year|quarter|week|day|hour|time|日期|月份|年月|年|月|日|季度|周|小时)/i;
const TIME_VAL_RE = /^\d{4}[-\/年Q]?/;
// 编号类列：值常常是数字，但求和毫无意义，只能当维度
const ID_COL_RE = /(^|_)(id|code|no|seq)$|编号|序号|编码|单号/i;

export const MAX_GROUPS = 40;   // 分组数上限，再多画出来就是一坨毛刺
export const PIE_LIMIT = 10;    // 饼图扇区上限

// 一列是否可当度量：允许少量脏值（NULL / 空串），但绝大多数必须能解析成数字
export function isMeasureCol(rows, k) {
  const vals = rows.map((r) => r[k]).filter((v) => v !== null && v !== undefined && String(v).trim() !== "");
  if (!vals.length) return false;
  return vals.filter((v) => isNum(v)).length / vals.length >= 0.8;
}

export function isTimeCol(k, rows) {
  if (TIME_COL_RE.test(k)) return true;
  const vals = rows.map((r) => String(r[k] ?? "").trim()).filter(Boolean);
  return vals.length > 0 && vals.every((v) => TIME_VAL_RE.test(v));
}

// 数值列能不能当"时间维度"：月份 1~12、年份 2025 这种可以，
// 但"月均出库货值"这种只是名字里带月、值是小数且取值散乱的不能
function isTimeSeqCol(rows, k) {
  if (!TIME_COL_RE.test(k)) return false;
  const vals = rows.map((r) => numOf(r[k]));
  if (vals.some((v) => v === null)) return false;
  const uniq = new Set(vals);
  return uniq.size <= 31 && vals.every((v) => Number.isInteger(v));
}

// 从结果集推断：维度候选、度量列、是否时间维度
export function chartPlan(rows) {
  if (!rows || !rows.length) return null;
  const cols = Object.keys(rows[0]);
  const numeric = cols.filter((k) => isMeasureCol(rows, k));

  // 先挑维度：非数值列优先，其次是"值虽然是数字但语义是时间/编号"的列
  // （月份 1~12、年份 2025 这类，不加这条会被当成度量跑去求和）
  const likeDim = (k) => isTimeSeqCol(rows, k) || ID_COL_RE.test(k);
  const seen = new Set();
  const dims = [...cols.filter((k) => !numeric.includes(k)), ...numeric.filter(likeDim)]
    .filter((k) => (seen.has(k) ? false : (seen.add(k), true)))
    .filter((k) => new Set(rows.map((r) => String(r[k] ?? "—"))).size <= MAX_GROUPS);
  if (!dims.length) return null;

  // 度量从剩下的数值列里选；编号类永远排除，时间列只在已被选作维度时排除
  const measures = numeric.filter((k) => !dims.includes(k) && !ID_COL_RE.test(k));
  if (!measures.length) return null;

  const dim = dims[0];
  const keys = [...new Set(rows.map((r) => String(r[dim] ?? "—")))];
  if (keys.length < 2) return null;   // 只有一个分组，图没有信息量
  return { dim, dims, measures, time: isTimeCol(dim, rows) };
}

// 按维度分组求和。SQL 已 GROUP BY 时每组一行，等价于原样透传；
// 结果集是明细（含重复维度值）时这里补一次聚合，避免同一类别画成多根柱子
export function groupRows(rows, dim, measures) {
  const map = new Map();
  for (const r of rows) {
    const key = String(r[dim] ?? "—");
    if (!map.has(key)) map.set(key, { key, vals: measures.map(() => 0), n: 0 });
    const e = map.get(key);
    measures.forEach((c, i) => {
      const v = numOf(r[c]);
      if (v !== null) e.vals[i] += v;
    });
    e.n++;
  }
  return [...map.values()];
}

// 分组 → 画图用的序列（含排序）。时间维度必须自己排：
// 月份 1~12 走字符串序会把 10 排到 2 前面；其它情况保留 SQL 自己的 ORDER BY
export function buildSeries(rows, dim, measures, mi = 0, isTime = false) {
  const groups = groupRows(rows, dim, measures);
  if (isTime) {
    const asNum = groups.every((g) => isNum(g.key));
    groups.sort((a, b) =>
      asNum ? Number(a.key) - Number(b.key) : a.key < b.key ? -1 : a.key > b.key ? 1 : 0
    );
  }
  return groups.map((g) => ({ key: g.key, v: g.vals[mi] }));
}

function niceMax(v) {
  if (!(v > 0)) return 1;
  const base = Math.pow(10, Math.floor(Math.log10(v)));
  const f = v / base;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * base;
}

function svgOpen(w, h) {
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="xMidYMid meet" `
    + `xmlns="http://www.w3.org/2000/svg" font-family="inherit">`;
}

// 柱状 / 横向条形：类别多或标签长时自动转横向，中文标签竖排会糊成一团
export function svgBar(items, dimLabel, msLabel) {
  const n = items.length;
  const maxV = Math.max(...items.map((d) => d.v), 0);
  const minV = Math.min(...items.map((d) => d.v), 0);
  const hi = niceMax(maxV), lo = minV < 0 ? -niceMax(-minV) : 0;
  const span = hi - lo || 1;
  const W = 860;
  const tt = (d) => `${dimLabel} ${d.key} · ${msLabel} ${fmtNum(d.v)}`;

  if (n > 10 || items.some((d) => d.key.length > 8)) {
    const labW = Math.min(180, 40 + Math.max(...items.map((d) => d.key.length)) * 12);
    const padR = 92, rowH = Math.max(20, Math.min(34, Math.floor(420 / n)));
    const H = n * rowH + 34;
    const plotW = W - labW - padR;
    const x = (v) => labW + ((v - lo) / span) * plotW;
    const zero = x(0);
    let s = svgOpen(W, H);
    for (let i = 0; i <= 4; i++) {
      const v = lo + (span * i) / 4, px = x(v);
      s += `<line class="grid" x1="${px}" y1="8" x2="${px}" y2="${H - 26}"/>`
        + `<text x="${px}" y="${H - 10}" text-anchor="middle">${fmtAxis(v)}</text>`;
    }
    items.forEach((d, i) => {
      const y = 8 + i * rowH, h = Math.max(12, rowH - 8);
      const w = Math.abs(x(d.v) - zero), bx = d.v >= 0 ? zero : zero - w;
      const tx = d.v >= 0 ? bx + w + 6 : bx - 6;
      s += `<text class="lab" x="${labW - 8}" y="${y + h / 2 + 4}" text-anchor="end">${esc(clip(d.key, 14))}</text>`
        + `<rect class="bar" x="${bx}" y="${y}" width="${Math.max(w, 1)}" height="${h}" rx="3" fill="${PALETTE[i % PALETTE.length]}">`
        + `<title>${esc(tt(d))}</title></rect>`
        + `<text class="val" x="${tx}" y="${y + h / 2 + 4}" text-anchor="${d.v >= 0 ? "start" : "end"}">${fmtNum(d.v)}</text>`;
    });
    return s + `<line class="base" x1="${zero}" y1="8" x2="${zero}" y2="${H - 26}"/></svg>`;
  }

  const padL = 60, padR = 16, padT = 12, padB = 38, H = 300;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const y = (v) => padT + ((hi - v) / span) * plotH;
  const bw = Math.min(56, (plotW / n) * 0.62);
  const step = Math.ceil(n / 12);
  let s = svgOpen(W, H);
  for (let i = 0; i <= 4; i++) {
    const v = hi - (span * i) / 4, py = y(v);
    s += `<line class="grid" x1="${padL}" y1="${py}" x2="${W - padR}" y2="${py}"/>`
      + `<text x="${padL - 8}" y="${py + 4}" text-anchor="end">${fmtAxis(v)}</text>`;
  }
  items.forEach((d, i) => {
    const cx = padL + (i + 0.5) * (plotW / n);
    const top = y(Math.max(d.v, 0)), bot = y(Math.min(d.v, 0));
    s += `<rect class="bar" x="${cx - bw / 2}" y="${top}" width="${bw}" height="${Math.max(bot - top, 1)}" rx="3" fill="${PALETTE[i % PALETTE.length]}">`
      + `<title>${esc(tt(d))}</title></rect>`;
    if (n <= 12 || i % step === 0) {
      s += `<text class="lab" x="${cx}" y="${H - padB + 18}" text-anchor="middle">${esc(clip(d.key, 8))}</text>`;
    }
  });
  return s + `<line class="base" x1="${padL}" y1="${y(0)}" x2="${W - padR}" y2="${y(0)}"/></svg>`;
}

export function svgLine(items, dimLabel, msLabel) {
  const n = items.length, W = 860, H = 300;
  const padL = 60, padR = 20, padT = 14, padB = 38;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const hi = niceMax(Math.max(...items.map((d) => d.v), 0));
  const lo = Math.min(...items.map((d) => d.v), 0);
  const lo2 = lo < 0 ? -niceMax(-lo) : 0;
  const span = hi - lo2 || 1;
  const y = (v) => padT + ((hi - v) / span) * plotH;
  const x = (i) => padL + (i / (n - 1)) * plotW;
  let s = svgOpen(W, H);
  for (let i = 0; i <= 4; i++) {
    const v = hi - (span * i) / 4, py = y(v);
    s += `<line class="grid" x1="${padL}" y1="${py}" x2="${W - padR}" y2="${py}"/>`
      + `<text x="${padL - 8}" y="${py + 4}" text-anchor="end">${fmtAxis(v)}</text>`;
  }
  const pts = items.map((d, i) => [x(i), y(d.v)]);
  const line = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join("");
  const base = y(Math.max(lo2, 0));
  s += `<path d="${line}L${pts[n - 1][0].toFixed(1)} ${base}L${pts[0][0].toFixed(1)} ${base}Z" fill="var(--accent)" fill-opacity="0.10"/>`
    + `<path d="${line}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round"/>`;
  items.forEach((d, i) => {
    s += `<circle cx="${pts[i][0].toFixed(1)}" cy="${pts[i][1].toFixed(1)}" r="${n > 30 ? 2 : 3.2}" fill="var(--accent)">`
      + `<title>${esc(`${dimLabel} ${d.key} · ${msLabel} ${fmtNum(d.v)}`)}</title></circle>`;
  });
  const step = Math.ceil(n / 12);
  items.forEach((d, i) => {
    if (i % step === 0 || i === n - 1) {
      s += `<text class="lab" x="${pts[i][0].toFixed(1)}" y="${H - padB + 18}" text-anchor="middle">${esc(clip(d.key, 8))}</text>`;
    }
  });
  return s + `<line class="base" x1="${padL}" y1="${base}" x2="${W - padR}" y2="${base}"/></svg>`;
}

export function svgPie(items, dimLabel, msLabel) {
  const W = 860, H = 300, cx = 165, cy = H / 2;
  const R = Math.min(H / 2 - 20, 120), r = R * 0.56;
  const total = items.reduce((a, d) => a + Math.abs(d.v), 0) || 1;
  const p = (rad, a) => [cx + rad * Math.cos(a), cy + rad * Math.sin(a)];
  let a0 = -Math.PI / 2, s = svgOpen(W, H);
  items.forEach((d, i) => {
    const sweep = (Math.abs(d.v) / total) * Math.PI * 2;
    const a1 = a0 + sweep;
    const large = sweep > Math.PI ? 1 : 0;
    const [x1, y1] = p(R, a0), [x2, y2] = p(R, a1), [x3, y3] = p(r, a1), [x4, y4] = p(r, a0);
    s += `<path class="slice" d="M${x1.toFixed(1)} ${y1.toFixed(1)}A${R} ${R} 0 ${large} 1 ${x2.toFixed(1)} ${y2.toFixed(1)}`
      + `L${x3.toFixed(1)} ${y3.toFixed(1)}A${r} ${r} 0 ${large} 0 ${x4.toFixed(1)} ${y4.toFixed(1)}Z"`
      + ` fill="${PALETTE[i % PALETTE.length]}">`
      + `<title>${esc(`${dimLabel} ${d.key} · ${msLabel} ${fmtNum(d.v)} · 占比 ${((Math.abs(d.v) / total) * 100).toFixed(1)}%`)}</title></path>`;
    a0 = a1;
  });
  s += `<text class="lg" x="${cx}" y="${cy - 2}" text-anchor="middle" style="font-size:11px;fill:var(--mute)">合计</text>`
    + `<text class="lg" x="${cx}" y="${cy + 18}" text-anchor="middle">${esc(fmtAxis(total))}</text>`;
  const per = Math.ceil(items.length / 2);
  items.forEach((d, i) => {
    const col = Math.floor(i / per), row = i % per;
    const lx = 350 + col * 250, ly = 40 + row * 26;
    s += `<rect x="${lx}" y="${ly - 9}" width="10" height="10" rx="2" fill="${PALETTE[i % PALETTE.length]}"/>`
      + `<text class="lg" x="${lx + 16}" y="${ly}">${esc(clip(d.key, 12))}</text>`
      + `<text class="val" x="${lx + 242}" y="${ly}" text-anchor="end">${fmtNum(d.v)}</text>`;
  });
  return s + "</svg>";
}

export function renderChart(type, items, dimLabel, msLabel) {
  if (type === "line") return svgLine(items, dimLabel, msLabel);
  if (type === "pie") return svgPie(items, dimLabel, msLabel);
  return svgBar(items, dimLabel, msLabel);
}
