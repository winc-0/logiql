// 图表模块离线自检：直接 import 前端源码里的纯函数层，不需要浏览器。
// 运行：node frontend/tests/chart.test.mjs
import {
  chartPlan,
  groupRows,
  buildSeries,
  renderChart,
  svgBar,
  svgLine,
  svgPie,
  isMeasureCol,
  isTimeCol,
  MAX_GROUPS,
} from "../src/utils/chart.js";
import { isNum, fmtCell, fmtAxis } from "../src/utils/format.js";

let pass = 0, fail = 0;
function check(name, cond, extra = "") {
  if (cond) { pass++; console.log("  ok  " + name); }
  else { fail++; console.log("FAIL  " + name + (extra ? "  → " + extra : "")); }
}

const cats = [
  { category: "家用电器", total: "1234567.891" },
  { category: "手机数码", total: "987654.32" },
  { category: "服装鞋帽", total: "456789.1" },
  { category: "食品生鲜", total: "321098.7" },
  { category: "图书文娱", total: "120000" },
  { category: "母婴玩具", total: "98888.55" },
];
const months = Array.from({ length: 12 }, (_, i) => ({
  month: `2025-${String(i + 1).padStart(2, "0")}`,
  sales: String(80000 + i * 7300 + (i % 3) * 1500),
}));
const detail = Array.from({ length: 100 }, (_, i) => ({ order_id: "A" + i, amount: String(100 + i) }));
const single = [{ repurchase_rate: "0.4213" }];
const multi = [
  { city: "上海", order_cnt: 12, gmv: "12345.6" },
  { city: "北京", order_cnt: 9, gmv: "9987.2" },
  { city: "杭州", order_cnt: 7, gmv: "7654.0" },
];
const withNull = [
  { region: "华东", amount: "1000" }, { region: "华南", amount: null },
  { region: "华北", amount: "2500" }, { region: "西南", amount: "" },
];
const withNeg = [
  { channel: "线上", profit: "1000" }, { channel: "线下", profit: "-400" },
  { channel: "分销", profit: "600" },
];
const longLabel = [
  { product_name: "索尼 WH-1000XM5 无线降噪耳机头戴式", cnt: 30 },
  { product_name: "苹果 AirPods Pro 2 主动降噪蓝牙耳机", cnt: 25 },
  { product_name: "小米 米家智能空气净化器 4 Pro 家用除甲醛", cnt: 18 },
];

console.log("\n[1] 判定：能否图表化");
check("品类聚合 → 可画图", !!chartPlan(cats));
check("月度趋势 → 识别为时间维度", chartPlan(months)?.time === true);
check("明细表（每行唯一 id）→ 不画图", chartPlan(detail) === null);
check("单行单值 → 不画图", chartPlan(single) === null);
check("多度量 → measures=2", chartPlan(multi)?.measures.length === 2);
check("含 NULL 的度量列仍算数值列", isMeasureCol(withNull, "amount"));
check("长中文标签 → 可画图", !!chartPlan(longLabel));
check("分组数超过上限 → 不画图",
  chartPlan(Array.from({ length: MAX_GROUPS + 5 }, (_, i) => ({ k: "K" + i, v: i }))) === null);

console.log("\n[2] 分组与聚合");
const g = groupRows(cats, "category", ["total"]);
check("分组数正确", g.length === 6, String(g.length));
check("求和不丢精度", Math.abs(g[0].vals[0] - 1234567.891) < 1e-6);
const gd = groupRows([{ c: "A", v: 1 }, { c: "A", v: 2 }, { c: "B", v: 5 }], "c", ["v"]);
check("重复维度值会合并", gd.length === 2 && gd[0].vals[0] === 3);

console.log("\n[3] SVG 生成");
for (const [name, rows] of [["品类柱状", cats], ["月度折线", months], ["负值", withNeg], ["长标签横向", longLabel]]) {
  const plan = chartPlan(rows);
  const items = buildSeries(rows, plan.dim, plan.measures, 0, plan.time);
  const svg = plan.time ? svgLine(items, plan.dim, plan.measures[0]) : svgBar(items, plan.dim, plan.measures[0]);
  check(name + " 无 NaN/undefined", !/NaN|undefined|Infinity/.test(svg), svg.slice(0, 120));
  check(name + " 是合法 svg", svg.startsWith("<svg") && svg.trim().endsWith("</svg>"));
}
const pie = svgPie([{ key: "A", v: 30 }, { key: "B", v: 20 }, { key: "C", v: 50 }], "ch", "gmv");
check("饼图生成", pie.startsWith("<svg") && !/NaN|undefined/.test(pie));
check("饼图含占比 tooltip", pie.includes("占比"));
check("renderChart 三种类型都出图",
  ["bar", "line", "pie"].every((t) => renderChart(t, [{ key: "A", v: 1 }, { key: "B", v: 2 }], "d", "m").startsWith("<svg")));

console.log("\n[4] 真实后端结果集（2026-10-19 实跑取回）");
const real1 = JSON.parse('[{"category":"手机数码","total_sales":"4245714.41"},{"category":"家用电器","total_sales":"1417936.72"},{"category":"鞋靴","total_sales":"557169.93"},{"category":"服饰","total_sales":"240989.34"},{"category":"食品饮料","total_sales":"74739.04"},{"category":"休闲零食","total_sales":"25334.20"}]');
const real2 = JSON.parse('[{"月":1,"销售额":363784.0975074768},{"月":2,"销售额":163237.0191116333},{"月":3,"销售额":273907.72228240967},{"月":4,"销售额":235584.49324035645},{"月":5,"销售额":306127.6627922058},{"月":6,"销售额":383212.6611442566},{"月":7,"销售额":251866.37886810303},{"月":8,"销售额":349470.7812538147},{"月":9,"销售额":274137.1014099121},{"月":10,"销售额":510830.92726135254},{"月":11,"销售额":582965.8206710815},{"月":12,"销售额":512588.0181350708}]');
const real3 = JSON.parse('[{"year":1970,"month":8,"ORDER_COUNT":1407}]');

const p1 = chartPlan(real1);
check("品类：维度=category 度量=total_sales",
  p1 && p1.dim === "category" && p1.measures.length === 1 && p1.measures[0] === "total_sales",
  JSON.stringify(p1));
check("品类：非时间 → 默认柱状", p1 && p1.time === false);
const p2 = chartPlan(real2);
check("月度：月份是数字但仍识别为维度", p2 && p2.dim === "月" && p2.measures[0] === "销售额", JSON.stringify(p2));
check("月度：识别为时间 → 默认折线", p2 && p2.time === true);
check("单值结果（1970/8 一行）→ 不画图", chartPlan(real3) === null);

const s2 = buildSeries(real2, p2.dim, p2.measures, 0, p2.time);
check("月份按数值排序（不是 1,10,11,12,2…）",
  s2.map((d) => d.key).join(",") === "1,2,3,4,5,6,7,8,9,10,11,12", s2.map((d) => d.key).join(","));
check("时间列判定：月 / month / 下单日期",
  isTimeCol("月", real2) && isTimeCol("month", months) && !isTimeCol("category", cats));

console.log("\n[5] 单元格排版");
check("浮点噪声被排版", fmtCell(2018854.1689453125) === "2,018,854.17", fmtCell(2018854.1689453125));
check("整数不加千分位", fmtCell(20250101) === "20250101", fmtCell(20250101));
check("空值显示占位符", fmtCell(null) === "—" && fmtCell("") === "—");
check("金额轴刻度走中文量级", fmtAxis(4245714) === "424.6万", fmtAxis(4245714));
check("字符串数字可判别", isNum("12.5") && !isNum("abc") && !isNum(""));

console.log(`\n通过 ${pass} / 失败 ${fail}`);
process.exit(fail ? 1 : 0);
