"""把评测结果 JSON 渲染成一个自包含的 HTML 可视化面板。

设计取舍：
- **不引任何 CDN**（Chart.js / ECharts 之类都不用），图表全部用原生 SVG 手绘。
  理由是这个面板要能直接双击打开、能塞进仓库、能在没网的机器上复盘，
  一旦依赖 CDN，离线时就只剩一片空白。
- **数据内嵌进 HTML**，输出单个文件即可分发，不需要连带 JSON 一起拷。
- 明暗双主题跟随系统（`prefers-color-scheme`），截图和演示都不会刺眼。

用法：
    python -m eval.dashboard                      # 自动找最新的小/大 schema 两份结果
    python -m eval.dashboard --small a.json --large b.json -o out.html
    python -m eval.dashboard --single x.json      # 只看一轮
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import eval._boot as boot

boot.init()

from eval.metrics import (  # noqa: E402
    fmt_delta,
    fmt_p,
    fmt_pct,
    mean,
    paired_bootstrap_p_value,
)

REPORT_DIR = boot.PROJECT_ROOT / "eval" / "reports"

# 难度档的展示名与分组依据
LEVEL_GROUPS = [
    ("A-D", "简单题", ("A", "B", "C", "D")),
    ("E", "时间对比 / 指标", ("E",)),
    ("F", "多表交叉 / 子查询 / 窗口函数", ("F",)),
]

# 面板里只保留这些策略，顺序即图表顺序
STRATEGY_LABELS = {
    "llm_only": "裸 LLM 全量灌输",
    "col_recall": "+ 字段向量召回",
    "col_val_recall": "+ 取值全文召回",
    "full_recall": "+ 指标召回（纯召回）",
    "llm_filter": "+ LLM 精筛",
    "full": "+ SQL 校验纠错",
}

def _level_group(level: str) -> str:
    upper = (level or "").upper()
    for name, _desc, members in LEVEL_GROUPS:
        if upper in members:
            return name
    return "A-D"


def slim(payload: dict, sql_limit: int = 600) -> dict:
    """裁掉渲染用不到的字段，控制内嵌数据体积。

    原始结果里每条都带完整 SQL 与错误详情，两份 56x6 全量塞进去有 300KB+，
    截断后降到 1/3 且不影响看板结论（SQL 太长在卡片里也显示不下）。
    """
    strategies = {}
    for name, records in payload.get("strategies", {}).items():
        strategies[name] = [
            {
                "qid": r.get("qid", ""),
                "level": r.get("level", ""),
                "kind": r.get("kind", ""),
                "ex": int(bool(r.get("ex"))),
                "ex_ordered": int(bool(r.get("ex_ordered"))),
                "executable": int(bool(r.get("executable"))),
                "reason": r.get("reason") or "",
                "error_detail": (r.get("error_detail") or "")[:300],
                "elapsed_s": r.get("elapsed_s") or 0.0,
                "sql": (r.get("sql") or "")[:sql_limit],
            }
            for r in records
        ]
    out = {
        "stamp": payload.get("stamp", ""),
        "tag": payload.get("tag", ""),
        "meta_config": payload.get("meta_config", ""),
        "n_questions": payload.get("n_questions", 0),
        "schema": payload.get("schema", {}),
        # 前端统一读 scale：早期结果没有 schema 字段，直接读会全是 undefined
        "scale": scale_of(payload),
        "timings": payload.get("timings", {}),
        "strategies": strategies,
    }
    # 跨轮 p 值由 main() 算好后挂在这里，得跟着一起带走
    if "__pvals" in payload:
        out["__pvals"] = payload["__pvals"]
    return out


def scale_of(payload: dict) -> dict:
    """估算一轮评测所用的 schema 规模，统一输出 {tables, columns, metrics, tokens}。

    口径跟 compare_schema.py 保持一致：优先按 meta 配置文件算。
    不直接用结果 JSON 里的 schema 字段，一来早期结果没记这个字段，
    二来那个字段是从 meta 库读出来的、额外含了字段类型与取值样例，
    跟这里要衡量的「灌输给模型的 schema 体量」不是一回事。
    """
    meta = payload.get("meta_config")
    if not meta:
        # 早期结果没记录用的是哪套配置，而那时只有一套，就是小 schema 这份
        meta = str(boot.PROJECT_ROOT / "conf" / "meta_config.yaml")
    if Path(meta).exists():
        from eval.compare_schema import estimate_tokens

        d = estimate_tokens(Path(meta))
        return {
            "tables": d["tables"],
            "columns": d["columns"],
            "metrics": d["metrics"],
            "tokens": d["tokens"],
        }
    s = payload.get("schema") or {}
    return {
        "tables": s.get("tables", 0),
        "columns": s.get("columns", 0),
        "metrics": s.get("metrics", 0),
        "tokens": s.get("schema_tokens_est", 0),
    }


def _ex_mean(payload: dict, name: str, group: str | None = None) -> float:
    records = payload["strategies"].get(name, [])
    if group is not None:
        records = [r for r in records if _level_group(r["level"]) == group]
    return mean([r["ex"] for r in records]) if records else 0.0


def build_findings(payloads: list[dict]) -> list[dict]:
    """根据数据生成结论卡片，避免把写死的结论留在页面里。

    面板会跟着新结果重新渲染，结论如果写死就会出现「数据变了、结论没变」的尴尬。
    两种场景分开处理：两轮时讲跨轮对比，单轮时讲轮内各策略相对基线的增量。
    """
    cards: list[dict] = []
    if not payloads:
        return cards

    first, last = payloads[0], payloads[-1]
    multi = len(payloads) >= 2
    names = [n for n in last["strategies"] if n in first["strategies"]]

    def lst(payload: dict, name: str) -> list[int]:
        return [r["ex"] for r in payload["strategies"].get(name, [])]

    s_scale, l_scale = scale_of(first), scale_of(last)
    s_txt = f"{s_scale['tables']} 表 {s_scale['columns']} 字段（约 {s_scale['tokens']} tokens）"
    l_txt = f"{l_scale['tables']} 表 {l_scale['columns']} 字段（约 {l_scale['tokens']} tokens）"

    # 结论 1：schema 规模对全量灌输的影响
    if "llm_only" in names:
        if multi:
            s, l = _ex_mean(first, "llm_only"), _ex_mean(last, "llm_only")
            p = paired_bootstrap_p_value(lst(first, "llm_only"), lst(last, "llm_only"))
            mul = l_scale["tokens"] / s_scale["tokens"] if s_scale["tokens"] else 0
            cards.append({
                "tone": "warn" if l < s else "good",
                "title": "schema 规模是全量灌输的硬约束",
                "body": (
                    f"schema 从 {s_txt} 扩到 {l_txt}（{mul:.1f}x）后，裸 LLM 基线 "
                    f"{fmt_pct(s)} → {fmt_pct(l)}（{fmt_delta(l - s)}，p={fmt_p(p)}）。"
                    "上下文一膨胀，模型就难以在海量字段里定位正确的表与口径。"
                ),
            })
        else:
            base = _ex_mean(last, "llm_only")
            best = max(names, key=lambda n: _ex_mean(last, n))
            gap = _ex_mean(last, best) - base
            p = paired_bootstrap_p_value(lst(last, "llm_only"), lst(last, best))
            cards.append({
                "tone": "good" if gap > 0 else "warn",
                "title": f"最优策略：{best}",
                "body": (
                    f"相对裸 LLM 基线 {fmt_pct(base)}，最优策略 `{best}` 达到 "
                    f"{fmt_pct(_ex_mean(last, best))}（{fmt_delta(gap)}，p={fmt_p(p)}）。"
                    f"当前 schema 为 {l_txt}。"
                ),
            })

    # 结论 2：精筛收益（两轮时看是否随规模反转，这是本项目最关键的发现）
    if "llm_filter" in names and "full_recall" in names:
        gain_s = _ex_mean(first, "llm_filter") - _ex_mean(first, "full_recall")
        gain_l = _ex_mean(last, "llm_filter") - _ex_mean(last, "full_recall")
        if multi and gain_s > 0 and gain_l < 0:
            cards.append({
                "tone": "danger",
                "title": "精筛的收益随 schema 规模反转",
                "body": (
                    f"精筛在小 schema 上是全链路最优环节（{fmt_delta(gain_s)}），"
                    f"到大 schema 上反而变成最差一环（{fmt_delta(gain_l)}）。"
                    "根因是精筛建立在召回之上：schema 变大后召回带进来的无关表变多，"
                    "精筛把噪声误判为相关的概率随之上升，出现「越筛越错」。"
                    "推论：精筛不是无条件有效的，它的价值取决于上游召回的精度。"
                ),
            })
        else:
            label = "大 schema" if multi else "当前轮"
            cards.append({
                "tone": "good" if gain_l > 0 else "warn",
                "title": "精筛环节收益",
                "body": (
                    f"召回后接 LLM 精筛的增量：{label} {fmt_delta(gain_l)}"
                    + (f"（小 schema {fmt_delta(gain_s)}）" if multi else "")
                    + "。"
                ),
            })

    # 结论 3：取值召回（ES 全文）的增益变化
    if "col_val_recall" in names and "col_recall" in names:
        g_s = _ex_mean(first, "col_val_recall") - _ex_mean(first, "col_recall")
        g_l = _ex_mean(last, "col_val_recall") - _ex_mean(last, "col_recall")
        cards.append({
            "tone": "good" if g_l > g_s else "info",
            "title": "取值召回的增益随规模变大",
            "body": (
                f"取值召回相对纯字段召回的增益："
                + (f"小 schema {fmt_delta(g_s)} → " if multi else "")
                + f"大 schema {fmt_delta(g_l)}。"
                "它是唯一随规模变大而增值的组件——因为大 schema 下模型最典型的错误是"
                "「字段归属搞错」，而库内真实取值能直接把它纠正回来。"
                "这说明「向量管语义、全文管精确」的分工是对的。"
            ),
        })

    # 结论 4：延迟代价
    # 口径是「同一轮内完整链路相对裸 LLM 基线」——跨轮比没有意义，
    # 因为两轮的 schema 体量本来就不同，耗时差异混了规模因素
    def lat_of(payload: dict, name: str) -> float:
        rs = payload["strategies"].get(name) or []
        return mean([r.get("elapsed_s") or 0.0 for r in rs]) if rs else 0.0

    base_l, full_l = lat_of(last, "llm_only"), lat_of(last, "full")
    if base_l and full_l:
        mul = full_l / base_l
        gain = _ex_mean(last, "full") - _ex_mean(last, "llm_only")
        verdict = "正 ROI" if gain > 0 else ("负 ROI" if gain < 0 else "准确率持平")
        cards.append({
            "tone": "info",
            "title": f"延迟代价（{verdict}）",
            "body": (
                f"完整链路单题均耗时 {full_l:.1f}s，裸 LLM 基线 {base_l:.1f}s"
                f"（{mul:.2f}x）；对应准确率增益 {fmt_delta(gain)}。"
                + (
                    "多花的延迟没能换来准确率提升，这一档下属于负 ROI。"
                    if gain < 0 else
                    "延迟换来了准确率提升，值得保留。"
                    if gain > 0 else
                    "准确率与基线持平。"
                )
            ),
        })

    return cards


def _discover(dirpath: Path) -> list[Path]:
    """挑出跑满全量（6 组策略）的结果文件，返回 [小 schema, 大 schema]。

    难点在于早期的结果里没有记录 schema 规模字段（那是后来才加的），
    所以不能一律按 `schema.tables` 排：先在有记录的里面找两端，
    只有一份有记录时（目前的真实情况），把它当大 schema，
    再从剩余文件里按「时间新、体积大」挑小 schema 那份。
    体积是个不错的代理指标——题目齐全、字段完整的那轮文件明显更大。
    """
    cands: list[tuple[Path, dict]] = []
    for p in sorted(dirpath.glob("nl2sql_*.json")):
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if len(payload.get("strategies", {})) < 6:
            continue
        # 体积过小的通常是冒烟跑出来的，题目不全
        if p.stat().st_size < 50_000:
            continue
        cands.append((p, payload))

    if not cands:
        return []

    with_schema = [(p, pl) for p, pl in cands if pl.get("schema")]
    if len(with_schema) >= 2:
        with_schema.sort(key=lambda x: x[1]["schema"].get("tables", 0))
        return [with_schema[0][0], with_schema[-1][0]]

    if len(with_schema) == 1:
        large = with_schema[0][0]
        rest = [(p, pl) for p, pl in cands if p != large]
        rest.sort(key=lambda x: (x[0].stat().st_mtime, x[0].stat().st_size), reverse=True)
        return [rest[0][0], large] if rest else [large]

    cands.sort(key=lambda x: (x[0].stat().st_mtime, x[0].stat().st_size), reverse=True)
    return [p for p, _ in cands[:2]]


def build_html(payloads: list[dict]) -> str:
    data = [slim(p) for p in payloads]
    findings = build_findings(payloads)
    blob = json.dumps({"runs": data, "findings": findings}, ensure_ascii=False)
    # 内嵌 JSON 里若出现 </script> 会提前闭合脚本块
    blob = blob.replace("</", "<\\/")
    labels = json.dumps(STRATEGY_LABELS, ensure_ascii=False)
    return (
        _TEMPLATE
        .replace("/*__DATA__*/null", blob)
        .replace("/*__LABELS__*/null", labels)
    )


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NL2SQL 评测看板</title>
<style>
:root{
  --bg:#f4f6f9; --card:#ffffff; --card-2:#fafbfc; --line:#e3e7ed;
  --fg:#14181d; --fg-dim:#5c6672; --fg-mute:#8b95a1;
  --accent:#6366f1; --amber:#f59e0b; --red:#ef4444; --green:#22c55e;
  --shadow:0 1px 2px rgba(16,24,40,.05),0 4px 12px rgba(16,24,40,.06);
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#14171c; --card:#1b1f26; --card-2:#21262e; --line:#2c323c;
    --fg:#e8eaee; --fg-dim:#a4adb9; --fg-mute:#7c8593;
    --accent:#818cf8; --amber:#fbbf24; --red:#f87171; --green:#4ade80;
    --shadow:0 1px 2px rgba(0,0,0,.3),0 4px 14px rgba(0,0,0,.25);
  }
}
*{box-sizing:border-box}
body{
  margin:0; padding:32px 24px 64px; background:var(--bg); color:var(--fg);
  font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
  -webkit-font-smoothing:antialiased;
}
.wrap{max-width:1080px;margin:0 auto}
h1{font-size:26px;margin:0 0 6px;letter-spacing:-.01em}
.sub{color:var(--fg-mute);font-size:13px;margin-bottom:28px}
h2{font-size:17px;margin:38px 0 14px;display:flex;align-items:center;gap:9px}
h2::before{content:"";width:3px;height:15px;background:var(--accent);border-radius:2px}
.hint{color:var(--fg-mute);font-size:12.5px;margin:-6px 0 14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 20px;box-shadow:var(--shadow)}
.grid{display:grid;gap:14px}
.kpis{grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
.kpi .k{font-size:11.5px;color:var(--fg-mute);text-transform:uppercase;letter-spacing:.06em}
.kpi .v{font-size:24px;font-weight:650;margin-top:5px;letter-spacing:-.02em}
.kpi .d{font-size:12px;color:var(--fg-dim);margin-top:2px}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{padding:9px 11px;text-align:left;border-bottom:1px solid var(--line)}
th{font-size:11.5px;color:var(--fg-mute);text-transform:uppercase;letter-spacing:.05em;font-weight:600}
tbody tr:last-child td{border-bottom:none}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.up{color:var(--green);font-weight:600}
.down{color:var(--red);font-weight:600}
.flat{color:var(--fg-mute)}
.badge{display:inline-block;padding:1px 7px;border-radius:999px;font-size:11px;font-weight:600}
.badge.ok{background:rgba(34,197,94,.14);color:var(--green)}
.badge.warn{background:rgba(245,158,11,.16);color:var(--amber)}
.badge.bad{background:rgba(239,68,68,.14);color:var(--red)}
.legend{display:flex;gap:18px;flex-wrap:wrap;font-size:12.5px;color:var(--fg-dim);margin:12px 0 4px}
.legend i{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.tabs{display:flex;gap:6px;margin-bottom:14px;flex-wrap:wrap}
.tab{padding:5px 14px;border:1px solid var(--line);background:var(--card);color:var(--fg-dim);
     border-radius:8px;cursor:pointer;font-size:13px;user-select:none}
.tab.on{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
details{border:1px solid var(--line);border-radius:10px;margin-bottom:9px;background:var(--card-2);overflow:hidden}
summary{padding:11px 15px;cursor:pointer;font-size:13.5px;display:flex;justify-content:space-between;align-items:center;gap:10px}
summary::-webkit-details-marker{display:none}
summary::after{content:"▾";color:var(--fg-mute);transition:transform .15s;flex-shrink:0}
details[open] summary::after{transform:rotate(180deg)}
.fail{padding:2px 14px 13px;font-size:12.5px}
.fail-row{padding:8px 0;border-top:1px dashed var(--line)}
.fail-row:first-child{border-top:none}
.fail-q{color:var(--fg);font-weight:600}
.fail-r{color:var(--red)}
.fail-e{color:var(--fg-mute);margin-top:3px}
pre{margin:6px 0 0;padding:9px 11px;background:var(--card);border:1px solid var(--line);
    border-radius:7px;overflow-x:auto;font-size:12px;line-height:1.55;color:var(--fg-dim)}
.find{border-left:3px solid var(--accent)}
.find.danger{border-left-color:var(--red)}
.find.warn{border-left-color:var(--amber)}
.find.good{border-left-color:var(--green)}
.find h3{margin:0 0 6px;font-size:14.5px}
.find p{margin:0;color:var(--fg-dim);font-size:13.5px}
svg{display:block;width:100%;height:auto;overflow:visible}
.axis{stroke:var(--line);stroke-width:1}
.gl{stroke:var(--line);stroke-width:1;stroke-dasharray:3 4;opacity:.7}
.lbl{fill:var(--fg-mute);font-size:11px}
.vlbl{fill:var(--fg);font-size:11px;font-weight:600}
.secnote{color:var(--fg-mute);font-size:12px;margin-top:8px}
</style>
</head>
<body>
<div class="wrap">
  <h1>NL2SQL 评测看板</h1>
  <div class="sub" id="sub"></div>

  <h2>概览</h2>
  <div class="grid kpis" id="kpis"></div>

  <h2>各策略准确率（EX）</h2>
  <div class="hint">柱高为执行准确率；两组之间的数字为「大 schema − 小 schema」的差值，括号内为配对 bootstrap 的 p 值。</div>
  <div class="card"><div id="chart-ex"></div>
    <div class="legend">
      <span><i style="background:var(--accent)"></i>小 schema</span>
      <span><i style="background:var(--amber)"></i>大 schema</span>
    </div>
  </div>

  <h2>分难度档表现</h2>
  <div class="hint">点击切换难度档。差距几乎全部集中在 F 档难题上。</div>
  <div class="tabs" id="tabs"></div>
  <div class="card"><div id="chart-level"></div>
    <div class="legend">
      <span><i style="background:var(--accent)"></i>小 schema</span>
      <span><i style="background:var(--amber)"></i>大 schema</span>
    </div>
  </div>

  <h2>可执行率与延迟</h2>
  <div class="card">
    <table id="tbl-exec"><thead><tr>
      <th>策略</th><th class="num">可执行率（小）</th><th class="num">可执行率（大）</th>
      <th class="num">平均耗时（小）</th><th class="num">平均耗时（大）</th><th class="num">延迟倍数</th>
    </tr></thead><tbody></tbody></table>
    <div class="secnote">耗时为一道题走完整条链路的墙钟时间，含 LLM 调用与数据库执行。</div>
  </div>

  <h2>明细</h2>
  <div class="hint">下表为最后一轮（大 schema）各策略的完整指标，ΔEX 一列相对裸 LLM 基线计算。</div>
  <div class="card">
    <table id="tbl-detail"><thead><tr>
      <th>策略</th><th class="num">EX</th><th class="num">ΔEX</th><th class="num">EX（含行序）</th>
      <th class="num">可执行率</th><th class="num">平均耗时</th>
    </tr></thead><tbody></tbody></table>
  </div>

  <h2>结论</h2>
  <div class="grid" id="findings"></div>

  <h2>失败题清单</h2>
  <div class="hint">展开看具体生成的 SQL 与报错。同一道题在不同策略下失败原因往往不同。</div>
  <div id="failures"></div>
</div>

<script>
const DATA = /*__DATA__*/null;
const STRATEGY_LABELS = /*__LABELS__*/null;
const RUNS = DATA.runs;

const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const pct = v => (v * 100).toFixed(2) + "%";
const pct1 = v => (v * 100).toFixed(1) + "%";
const avg = a => a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0;

// 策略顺序：沿用 STRATEGY_LABELS 的定义顺序
const STRATS = Object.keys(RUNS[0].strategies)
  .sort((a, b) => Object.keys(STRATEGY_LABELS).indexOf(a) - Object.keys(STRATEGY_LABELS).indexOf(b));

const LEVELS = [
  { key: "A-D", name: "A–D 简单题", members: ["A", "B", "C", "D"] },
  { key: "E", name: "E 时间对比 / 指标", members: ["E"] },
  { key: "F", name: "F 多表交叉 / 子查询 / 窗口函数", members: ["F"] },
];
const grp = lv => {
  const u = String(lv || "").toUpperCase();
  const hit = LEVELS.find(l => l.members.includes(u));
  return hit ? hit.key : "A-D";
};

const exOf = (run, s, g) => {
  let rs = run.strategies[s] || [];
  if (g) rs = rs.filter(r => grp(r.level) === g);
  return avg(rs.map(r => r.ex));
};

/* ---------- 概览 ---------- */
(function () {
  const small = RUNS[0], large = RUNS[RUNS.length - 1];
  document.getElementById("sub").textContent =
    `${RUNS.length} 轮对照 · ${large.n_questions} 题 × ${STRATS.length} 组策略 · ` +
    `生成于 ${new Date().toLocaleString("zh-CN")}`;

  const s = small.scale, l = large.scale;
  const ratio = (a, b) => b ? (b / a).toFixed(1) + "x" : "—";
  const items = [
    { k: "表数量", v: `${s.tables} → ${l.tables}`, d: ratio(s.tables, l.tables) },
    { k: "字段数量", v: `${s.columns} → ${l.columns}`, d: ratio(s.columns, l.columns) },
    { k: "全量 schema tokens", v: `${s.tokens} → ${l.tokens}`, d: `${ratio(s.tokens, l.tokens)} 膨胀` },
    { k: "最优策略（小）", v: pct1(Math.max(...STRATS.map(x => exOf(small, x)))), d: STRATS.reduce((a, b) => exOf(small, a) >= exOf(small, b) ? a : b) },
    { k: "最优策略（大）", v: pct1(Math.max(...STRATS.map(x => exOf(large, x)))), d: STRATS.reduce((a, b) => exOf(large, a) >= exOf(large, b) ? a : b) },
    { k: "总调用次数", v: String(large.n_questions * STRATS.length * RUNS.length), d: "链路端到端执行" },
  ];
  document.getElementById("kpis").innerHTML = items.map(i =>
    `<div class="card kpi"><div class="k">${esc(i.k)}</div><div class="v mono">${esc(i.v)}</div><div class="d">${esc(i.d)}</div></div>`
  ).join("");
})();

/* ---------- 分组柱状图 ---------- */
function groupBar(el, rows, opts) {
  // rows: [{label, small, large, delta, p}]
  const W = 980, H = opts.height || 340, pad = { t: 26, r: 16, b: 62, l: 46 };
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  const maxV = opts.max || 1.0;
  const y = v => pad.t + ih - (v / maxV) * ih;
  const n = rows.length, gw = iw / n, bw = Math.min(34, gw * 0.3), gap = 7;

  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opts.title || "")}">`;
  // 网格与刻度
  for (let i = 0; i <= 4; i++) {
    const v = maxV * i / 4, yy = y(v);
    s += `<line class="gl" x1="${pad.l}" y1="${yy}" x2="${W - pad.r}" y2="${yy}"/>`;
    s += `<text class="lbl" x="${pad.l - 8}" y="${yy + 4}" text-anchor="end">${(v * 100).toFixed(0)}%</text>`;
  }
  rows.forEach((r, i) => {
    const cx = pad.l + gw * i + gw / 2;
    const two = opts.two !== false;
    const bars = two
      ? [[cx - bw - gap / 2, r.small, "var(--accent)"],
         [cx + gap / 2, r.large, "var(--amber)"]]
      : [[cx - bw / 2, r.small, "var(--accent)"]];
    bars.forEach(([x, v, c]) => {
      const yy = y(v), h = Math.max(2, pad.t + ih - yy);
      s += `<rect x="${x}" y="${yy}" width="${bw}" height="${h}" rx="4" fill="${c}"/>`;
      s += `<text class="vlbl" x="${x + bw / 2}" y="${yy - 6}" text-anchor="middle">${(v * 100).toFixed(1)}</text>`;
    });
    // 差值与显著性
    const cls = r.delta > 0.001 ? "up" : (r.delta < -0.001 ? "down" : "flat");
    const sign = r.delta > 0 ? "+" : "";
    const pTxt = opts.showP ? ` p=${esc(r.p)}` : "";
    s += `<text class="lbl" x="${cx}" y="${pad.t + ih + 16}" text-anchor="middle" fill="currentColor" style="fill:var(--${cls === "down" ? "red" : cls === "up" ? "green" : "fg-mute"})">${sign}${(r.delta * 100).toFixed(1)}pt${pTxt}</text>`;
    // X 轴标签
    const lines = String(r.label).split("|");
    lines.forEach((ln, k) => {
      s += `<text class="lbl" x="${cx}" y="${pad.t + ih + 34 + k * 14}" text-anchor="middle">${esc(ln)}</text>`;
    });
  });
  s += `<line class="axis" x1="${pad.l}" y1="${pad.t + ih}" x2="${W - pad.r}" y2="${pad.t + ih}"/></svg>`;
  el.innerHTML = s;
}

/* ---------- 主图：各策略 EX ---------- */
(function () {
  const small = RUNS[0], large = RUNS[RUNS.length - 1];
  const two = RUNS.length >= 2;
  // p 值在 Python 侧算好（bootstrap 2000 次），塞进 large.__pvals 带过来
  const pvals = large.__pvals || {};
  const rows = STRATS.map(name => {
    const sa = avg((small.strategies[name] || []).map(r => r.ex));
    const sb = avg((large.strategies[name] || []).map(r => r.ex));
    return {
      label: (STRATEGY_LABELS[name] || name) + "|`" + name + "`",
      small: sa,
      large: sb,
      delta: sb - sa,
      p: pvals[name] || "",
    };
  });
  groupBar(
    document.getElementById("chart-ex"), rows,
    { max: 1.0, showP: two, two: two, height: 340, title: "各策略执行准确率对比" }
  );
})();

/* ---------- 分档图 ---------- */
let curLevel = "F";
function renderLevel() {
  const small = RUNS[0], large = RUNS[RUNS.length - 1];
  const two = RUNS.length >= 2;
  const rows = STRATS.map(name => {
    const sa = exOf(small, name, curLevel);
    const sb = exOf(large, name, curLevel);
    return { label: name, small: sa, large: sb, delta: sb - sa, p: "" };
  });
  const meta = LEVELS.find(l => l.key === curLevel);
  const n = (large.strategies[STRATS[0]] || []).filter(r => grp(r.level) === curLevel).length;
  groupBar(
    document.getElementById("chart-level"), rows,
    { max: 1.0, showP: false, two: two, height: 300, title: `${meta.name}（${n} 题）` }
  );
}
(function () {
  document.getElementById("tabs").innerHTML = LEVELS.map(l =>
    `<div class="tab${l.key === curLevel ? " on" : ""}" data-k="${l.key}">${esc(l.name)}</div>`
  ).join("");
  document.getElementById("tabs").addEventListener("click", e => {
    const t = e.target.closest(".tab");
    if (!t) return;
    curLevel = t.dataset.k;
    document.querySelectorAll(".tab").forEach(x => x.classList.toggle("on", x === t));
    renderLevel();
  });
  renderLevel();
})();

/* ---------- 表格 ---------- */
(function () {
  const small = RUNS[0], large = RUNS[RUNS.length - 1];
  const cls = d => d > 0.001 ? "up" : (d < -0.001 ? "down" : "flat");

  document.querySelector("#tbl-exec tbody").innerHTML = STRATS.map(name => {
    const es = avg((small.strategies[name] || []).map(r => r.executable));
    const el = avg((large.strategies[name] || []).map(r => r.executable));
    const ts = avg((small.strategies[name] || []).map(r => r.elapsed_s));
    const tl = avg((large.strategies[name] || []).map(r => r.elapsed_s));
    const mul = ts ? tl / ts : 0;
    // 早期结果没有逐题耗时字段，缺失就留空，别显示成 0.0s 误导
    const fmtT = v => v ? v.toFixed(1) + "s" : "—";
    return `<tr><td class="mono">${esc(name)}</td>
      <td class="num">${pct1(es)}</td><td class="num">${pct1(el)}</td>
      <td class="num mono">${fmtT(ts)}</td><td class="num mono">${fmtT(tl)}</td>
      <td class="num mono ${cls(mul - 1)}">${mul ? mul.toFixed(2) + "x" : "—"}</td></tr>`;
  }).join("");

  document.querySelector("#tbl-detail tbody").innerHTML = STRATS.map(name => {
    const rs = large.strategies[name] || [];
    const base = exOf(large, "llm_only");
    const ex = avg(rs.map(r => r.ex));
    const exo = avg(rs.map(r => r.ex_ordered));
    const exec = avg(rs.map(r => r.executable));
    const lat = avg(rs.map(r => r.elapsed_s));
    const d = ex - base;
    const dTxt = name === "llm_only" ? "—" : (d > 0 ? "+" : "") + (d * 100).toFixed(2) + "pt";
    return `<tr><td class="mono">${esc(name)}</td>
      <td class="num">${pct(ex)}</td>
      <td class="num mono ${cls(d)}">${dTxt}</td>
      <td class="num mono">${pct(exo)}</td>
      <td class="num mono">${pct1(exec)}</td>
      <td class="num mono">${lat.toFixed(1)}s</td></tr>`;
  }).join("");
})();

/* ---------- 结论 ---------- */
(function () {
  document.getElementById("findings").innerHTML = (DATA.findings || []).map(f =>
    `<div class="card find ${esc(f.tone)}"><h3>${esc(f.title)}</h3><p>${esc(f.body)}</p></div>`
  ).join("");
})();

/* ---------- 失败题清单 ---------- */
(function () {
  const large = RUNS[RUNS.length - 1];
  document.getElementById("failures").innerHTML = STRATS.map(name => {
    const fails = (large.strategies[name] || []).filter(r => !r.ex);
    const rows = fails.map(r =>
      `<div class="fail-row">
         <div><span class="fail-q mono">${esc(r.qid)}</span>
              <span class="lbl"> · ${esc(r.level)} 档 · ${esc(r.kind)}</span></div>
         <div class="fail-r">${esc(r.reason || "不一致")}</div>
         ${r.error_detail ? `<div class="fail-e mono">${esc(r.error_detail)}</div>` : ""}
         ${r.sql ? `<pre>${esc(r.sql)}</pre>` : ""}
       </div>`).join("");
    return `<details${fails.length ? "" : " "}>
      <summary><span class="mono">${esc(name)}</span>
        <span class="badge ${fails.length ? "bad" : "ok"}">${fails.length} 题失败</span></summary>
      <div class="fail">${rows || '<div class="fail-row lbl">全部通过</div>'}</div>
    </details>`;
  }).join("");
})();
</script>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 NL2SQL 评测可视化面板")
    parser.add_argument("--small", type=Path, help="小 schema 轮次的结果 JSON")
    parser.add_argument("--large", type=Path, help="大 schema 轮次的结果 JSON")
    parser.add_argument("--single", type=Path, help="只渲染一轮")
    parser.add_argument(
        "-o", "--out", type=Path, default=REPORT_DIR / "dashboard.html", help="输出 HTML 路径"
    )
    args = parser.parse_args()

    if args.single:
        paths = [args.single]
    elif args.small and args.large:
        paths = [args.small, args.large]
    else:
        # 自动发现：按 schema 规模升序排列，小的在前
        found = _discover(REPORT_DIR)
        found.sort(key=lambda p: json.loads(p.read_text(encoding="utf-8")).get("schema", {}).get("tables", 0))
        paths = found[:2] if len(found) >= 2 else found
        if paths:
            print("自动选取：")
            for p in paths:
                payload = json.loads(p.read_text(encoding="utf-8"))
                print(f"  {p.name}  ({payload.get('schema', {}).get('tables')} 表, tag={payload.get('tag') or '—'})")

    if not paths:
        print(f"未在 {REPORT_DIR} 找到评测结果，先跑 python -m eval.run_eval")
        return 2

    payloads = [json.loads(p.read_text(encoding="utf-8")) for p in paths]

    # p 值在 Python 侧算好（bootstrap 2000 次，放前端会卡）
    if len(payloads) >= 2:
        small, large = payloads[0], payloads[-1]
        pvals = {}
        for name in large["strategies"]:
            if name not in small["strategies"]:
                continue
            a = [r["ex"] for r in small["strategies"][name]]
            b = [r["ex"] for r in large["strategies"][name]]
            pvals[name] = fmt_p(paired_bootstrap_p_value(a, b))
        large["__pvals"] = pvals

    html = build_html(payloads)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")
    print(f"\n面板已写入 {args.out}  ({len(html) / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
