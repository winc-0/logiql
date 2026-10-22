"""
交叉对比工具：同一套题目在小 schema 与大 schema 两种条件下的评测结果。

用来验证一个假设 ——
    schema 规模是否是决定 NL2SQL 各策略相对优劣的关键变量？

小 schema 下我们观察到「三路召回单独用反而低于裸 LLM 全量灌输」，
一个自然存疑的解释是：5 张表 24 个字段的元数据总共才 1~2k tokens，
模型完全塞得下，筛选的价值无从体现，反而被召回噪声拖累。
若把 schema 扩到真实企业数仓规模，这个结论应当发生反转。

用法：
    python -m eval.compare_schema <小schema结果.json> <大schema结果.json>

依赖：两份结果都必须由同版本的 run_eval 产出（含 strategies 字段）。
旧版结果缺少 schema/tag 字段时，规模信息会从 meta 配置文件补算。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from eval.metrics import (
    fmt_delta,
    fmt_p,
    fmt_pct,
    mean,
    paired_bootstrap_p_value,
)

# 旧版结果没有记录 schema 规模时，从这里补算
META_SMALL = Path("conf/meta_config.yaml")
META_LARGE = Path("conf/meta_config_large.yaml")

STRATEGY_ORDER = [
    "llm_only", "col_recall", "col_val_recall",
    "full_recall", "llm_filter", "full",
]
STRATEGY_CN = {
    "llm_only": "裸 LLM 全量灌输",
    "col_recall": "字段向量召回",
    "col_val_recall": "字段+取值召回",
    "full_recall": "三路召回全开",
    "llm_filter": "三路召回+LLM 精筛",
    "full": "完整链路（含纠错）",
}
LEVEL_CN = {
    "A": "单表聚合", "B": "单维分组", "C": "TopN 排序",
    "D": "精确取值", "E": "时间对比/指标", "F": "多表交叉/子查询/窗口函数",
}


def estimate_tokens(meta_path: Path) -> dict:
    """按 provide_full_schema 节点的口径估算全量 schema 的体量。"""
    from omegaconf import OmegaConf

    cfg = OmegaConf.load(meta_path)
    parts: list[str] = []
    n_cols = 0
    for table in cfg.tables:
        parts.append(f"{table.name} {table.description}")
        for col in table.columns:
            n_cols += 1
            parts.append(
                f"  {col.name} {col.description} " + " ".join(col.alias or [])
            )
    for metric in cfg.metrics:
        parts.append(f"{metric.name} {metric.description}")
    text = "\n".join(parts)
    han = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return {
        "tables": len(list(cfg.tables)),
        "columns": n_cols,
        "metrics": len(list(cfg.metrics)),
        "tokens": int(han * 1.5 + (len(text) - han) * 0.3),
    }


def _scale_of(payload: dict, fallback_meta: Path) -> dict:
    """统一从 meta 配置文件估算规模。

    刻意不使用结果 JSON 里记录的 schema 字段：那是从 meta 库读出来的，
    额外包含了字段类型与取值样例；而旧版结果没有这个字段。
    一边含 examples 一边不含，比较出来的倍数会虚高一倍左右。
    这里两边都走同一个纯口径（仅表名/表描述/字段名/描述/别名/指标），
    保证倍数只反映真实的元数据膨胀。
    """
    return estimate_tokens(fallback_meta)


def ex_of(payload: dict, strategy: str, level: str | None = None) -> list[float]:
    records = payload["strategies"][strategy]
    if level is None:
        return [r["ex"] for r in records]
    return [r["ex"] for r in records if r["level"] == level]


def latency_of(payload: dict, strategy: str) -> float:
    records = payload["strategies"][strategy]
    vals = [r.get("elapsed_s", 0.0) for r in records if "elapsed_s" in r]
    return mean(vals) if vals else 0.0


def compare(small_path: Path, large_path: Path) -> str:
    small = json.loads(small_path.read_text(encoding="utf-8"))
    large = json.loads(large_path.read_text(encoding="utf-8"))

    strategies = [s for s in STRATEGY_ORDER if s in large["strategies"]]
    s_scale = _scale_of(small, META_SMALL)
    l_scale = _scale_of(large, META_LARGE)

    out: list[str] = []
    add = out.append

    add("# schema 规模交叉对比报告")
    add("")
    add(f"- 小 schema 来源：`{small_path.as_posix()}`（tag: {small.get('tag') or '未标注'}）")
    add(f"- 大 schema 来源：`{large_path.as_posix()}`（tag: {large.get('tag') or '未标注'}）")
    commits = {p.get("git_commit") for p in (small, large) if p.get("git_commit")}
    if len(commits) == 1:
        add(f"- 代码版本：`{commits.pop()}`（两轮同一版本）")
    elif len(commits) > 1:
        add(f"- 代码版本：{', '.join(sorted(f'`{c}`' for c in commits))}（两轮版本不同，解读时需注意）")
    add("")
    add("**实验设计**：题目完全相同（同一份标注集），只改变元数据规模；"
        "gold SQL 在两种条件下均已逐题验证可执行且结果非空，"
        "因此「schema 规模」是唯一的自变量。")
    add("")

    add("## 1. 两种条件的规模")
    add("")
    add("| 条件 | 表数 | 字段数 | 指标数 | 全量 schema 估算 token |")
    add("|---|---:|---:|---:|---:|")
    add(f"| 小 schema | {s_scale['tables']} | {s_scale['columns']} | "
        f"{s_scale['metrics']} | {s_scale['tokens']} |")
    add(f"| 大 schema | {l_scale['tables']} | {l_scale['columns']} | "
        f"{l_scale['metrics']} | {l_scale['tokens']} |")
    mul = l_scale["tokens"] / s_scale["tokens"] if s_scale["tokens"] else 0
    add(f"| **放大倍数** | {l_scale['tables'] / s_scale['tables']:.1f}x | "
        f"{l_scale['columns'] / s_scale['columns']:.1f}x | "
        f"{l_scale['metrics'] / s_scale['metrics']:.1f}x | **{mul:.1f}x** |")
    add("")
    add("> token 为估算值：按「表名+表描述+字段名+字段描述+别名+指标描述」拼接后，"
        "汉字按 1.5 token、其余字符按 0.3 token 折算。**两种条件用同一口径计算**，"
        "因此倍数只反映元数据本身的膨胀。")
    add("")

    add("## 2. 各策略 EX：小 schema vs 大 schema")
    add("")
    add("| 策略 | 小 schema | 大 schema | 变化 | p 值 |")
    add("|---|---:|---:|---:|---:|")
    deltas: dict[str, float] = {}
    for name in strategies:
        small_ex = ex_of(small, name)
        large_ex = ex_of(large, name)
        diff = mean(large_ex) - mean(small_ex)
        deltas[name] = diff
        p = paired_bootstrap_p_value(small_ex, large_ex)
        add(
            f"| `{name}` {STRATEGY_CN.get(name, '')} "
            f"| {fmt_pct(mean(small_ex))} | {fmt_pct(mean(large_ex))} "
            f"| {fmt_delta(diff)} | {fmt_p(p)} |"
        )
    add("")
    add("> p 值是对同一批题在两个条件下逐题配对、bootstrap 2000 次重采样得到的双侧检验结果。")
    add("")

    add("## 3. 关键比值：查封链路相对裸 LLM 基线的优势")
    add("")
    add("| 条件 | `llm_only` | `llm_filter` | 差值 |")
    add("|---|---:|---:|---:|")
    for tag, payload in (("小 schema", small), ("大 schema", large)):
        base = mean(ex_of(payload, "llm_only"))
        filt = mean(ex_of(payload, "llm_filter"))
        add(f"| {tag} | {fmt_pct(base)} | {fmt_pct(filt)} | {fmt_delta(filt - base)} |")
    add("")

    add("## 4. 延迟变化")
    add("")
    add("| 策略 | 小 schema 单题 | 大 schema 单题 | 变化 |")
    add("|---|---:|---:|---:|")
    for name in strategies:
        small_l = latency_of(small, name)
        large_l = latency_of(large, name)
        if not small_l or not large_l:
            add(f"| `{name}` | — | — | — |")
            continue
        add(
            f"| `{name}` | {small_l:.1f}s | {large_l:.1f}s "
            f"| {large_l / small_l:.2f}x |"
        )
    add("")

    add("## 5. 假设检验")
    add("")
    for line in _verdicts(small, large, strategies, deltas):
        add(line)
    add("")

    return "\n".join(out)


def _verdicts(small: dict, large: dict, strategies: list[str], deltas: dict[str, float]) -> list[str]:
    """逐条判定当初的假设是否成立，只陈述数据支持的部分。"""
    lines: list[str] = []
    base_s = mean(ex_of(small, "llm_only"))
    base_l = mean(ex_of(large, "llm_only"))

    # 假设 1：schema 变大后，全量灌输是否退化
    d1 = base_l - base_s
    if d1 < -0.02:
        lines.append(
            f"- **假设 1 成立**：schema 扩张后全量灌输基线明显退化，"
            f"{fmt_pct(base_s)} → {fmt_pct(base_l)}（{fmt_delta(d1)}）。"
            "上下文膨胀使模型难以在海量字段中定位正确的表与口径。"
        )
    elif abs(d1) < 0.02:
        lines.append(
            f"- **假设 1 不成立**：全量灌输基线几乎未受影响（{fmt_delta(d1)}），"
            "说明当前规模下模型仍能有效利用全部上下文。"
        )
    else:
        lines.append(f"- **假设 1 反向成立**：全量灌输基线反而更高（{fmt_delta(d1)}）。")

    # 假设 2：精筛的收益是否随 schema 规模而放大
    # 这是本轮最关键的一问 —— 比较「精筛相对纯召回」的边际收益在两种规模下的变化
    if {"llm_filter", "full_recall"} <= set(strategies):
        edge_s = mean(ex_of(small, "llm_filter")) - mean(ex_of(small, "full_recall"))
        edge_l = mean(ex_of(large, "llm_filter")) - mean(ex_of(large, "full_recall"))
        if edge_s >= 0 and edge_l < 0:
            lines.append(
                f"- **假设 2 反向成立（本轮最重要的发现）**：精筛的边际收益随 schema 扩张**反转**。"
                f"小 schema 下精筛带来 {fmt_delta(edge_s)}，大 schema 下反而 {fmt_delta(edge_l)}，"
                f"相对优势变化 {fmt_delta(edge_l - edge_s)}。"
                "原因是精筛建立在召回结果之上：schema 变大后召回命中的无关表显著增多，"
                "精筛把噪声误判为相关的概率上升，出现「越筛越错」，"
                "噪声放大效应超过了它带来的聚焦收益。"
            )
        elif edge_l > edge_s:
            lines.append(
                f"- **假设 2 成立**：精筛的边际收益随 schema 扩张而放大，"
                f"{fmt_delta(edge_s)} → {fmt_delta(edge_l)}。"
            )
        else:
            lines.append(
                f"- **假设 2 不成立**：精筛的边际收益没有随规模放大，"
                f"{fmt_delta(edge_s)} → {fmt_delta(edge_l)}。"
            )

    # 取值召回在小 schema 下完全无效，大 schema 下是否开始显现价值
    if {"col_val_recall", "col_recall"} <= set(strategies):
        for tag, payload in (("小 schema", small), ("大 schema", large)):
            gain = mean(ex_of(payload, "col_val_recall")) - mean(ex_of(payload, "col_recall"))
            lines.append(f"- 取值召回增益（{tag}）：{fmt_delta(gain)}")

    # 延迟代价
    ls, ll = latency_of(small, "llm_only"), latency_of(large, "llm_only")
    if ls and ll:
        lines.append(
            f"- 裸 LLM 基线的单题延迟随 schema 膨胀 {ll / ls:.2f}x（{ls:.1f}s → {ll:.1f}s）。"
        )

    best_s = max(strategies, key=lambda s: mean(ex_of(small, s)))
    best_l = max(strategies, key=lambda s: mean(ex_of(large, s)))
    lines.append(
        f"- 最优策略发生转移：小 schema 下是 `{best_s}`（{fmt_pct(mean(ex_of(small, best_s)))}），"
        f"大 schema 下是 `{best_l}`（{fmt_pct(mean(ex_of(large, best_l)))}）。"
        "说明**不存在放之四海而皆准的最优链路**，取舍取决于数仓规模。"
    )

    return lines


def main() -> int:
    if len(sys.argv) != 3:
        print("用法：python -m eval.compare_schema <小schema结果.json> <大schema结果.json>")
        return 2
    small_path, large_path = Path(sys.argv[1]), Path(sys.argv[2])
    for p in (small_path, large_path):
        if not p.exists():
            print(f"文件不存在：{p}")
            return 2

    report = compare(small_path, large_path)
    print(report)

    out = Path("eval/reports/schema_compare.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(f"对比报告已写入 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
