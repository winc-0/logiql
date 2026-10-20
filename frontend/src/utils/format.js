// 后端用 default=str 序列化，DECIMAL/金额会以字符串形式到达；
// 同时 MySQL 对 DOUBLE 做 SUM 会带出浮点噪声（如 2018854.1689453125），
// 直接展示既不专业也难读，这里统一排版

const NUM_RE = /^-?\d+(\.\d+)?$/;

export function isNum(v) {
  if (typeof v === "number") return isFinite(v);
  if (v === null || v === undefined) return false;
  const s = String(v).trim();
  return s !== "" && NUM_RE.test(s) && !isNaN(Number(s));
}

export function numOf(v) {
  return isNum(v) ? Number(String(v).trim()) : null;
}

export function fmtCell(v) {
  if (v === null || v === undefined || v === "") return "—";
  if (!isNum(v)) return v;
  const n = Number(String(v).trim());
  // 整数原样输出，避免给 product_id 之类的标识位加千分位造成误读
  if (Number.isInteger(n)) return String(n);
  return n.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export const fmtNum = (v) =>
  Number(v).toLocaleString("zh-CN", { maximumFractionDigits: 2 });

// 轴刻度压缩成中文习惯的量级，避免 1.2e7 这种原始科学计数
export function fmtAxis(v) {
  const a = Math.abs(v);
  if (a >= 1e8) return (v / 1e8).toFixed(1).replace(/\.0$/, "") + "亿";
  if (a >= 1e4) return (v / 1e4).toFixed(1).replace(/\.0$/, "") + "万";
  if (Number.isInteger(v)) return String(v);
  return v.toFixed(a < 1 ? 2 : 1);
}

export const clip = (s, n = 10) => (s.length > n ? s.slice(0, n - 1) + "…" : s);

// 图表是拼好 SVG 字符串后交给 v-html 渲染的，文本必须自己转义
export function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
