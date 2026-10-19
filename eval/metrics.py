"""
评测指标。

核心指标是 Execution Accuracy（EX）：把模型生成的 SQL 与标注的 gold SQL
都在数据仓库上真实执行，比较两者的结果集是否一致。

为什么用它而不是常见的「字符串完全匹配」：
    字符串匹配会把「SELECT SUM(order_amount)」和
    「SELECT SUM(f.order_amount) FROM fact_order f」判成不同，
    尽管它们执行的语义完全一样。NL2SQL 的产出是要拿去查数的，
    用户关心的是数字对不对，不是 SQL 长得像不像。

口径说明：
    - 主指标 EX 用**无序**比较：结果集的行的先后顺序没有业务含义，
      「各省份销售额」先返回广东还是先返回上海，都是对的。
    - 同时报告 EX_ordered：要求行序也一致。TopN 类题目（"销售额最高的品类"）
      行序本身携带语义，这一项才有意义。
"""
from __future__ import annotations

import random
import statistics
from typing import Any, Sequence

# 浮点容差：金额累加会产生微小误差，AVG(x) 与 SUM(x)/COUNT(*) 也不完全相等
FLOAT_ABS_TOL = 1e-3
FLOAT_REL_TOL = 1e-6
# 比较前先保留 4 位小数，抹掉累加噪声
FLOAT_ROUND = 4


# --------------------------------------------------------------------------- #
# 结果集比较
# --------------------------------------------------------------------------- #
def _canon(value: Any) -> Any:
    """
    把单个单元格规范化成可比较形式。

    数字字符串会被归一成数值：模型常写出
    `CASE WHEN date_id BETWEEN 20240101 AND 20241231 THEN '2024' END`，
    产出字符串 '2024'；而 gold 直接 `GROUP BY d.year` 产出整数 2024。
    两者回答的是同一个问题，不该因为类型差异被判错。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return round(float(value), FLOAT_ROUND)
    text = str(value)
    try:
        return round(float(text), FLOAT_ROUND)
    except ValueError:
        return text


def _row_values(row: dict) -> tuple:
    """
    取出一行的值序列。

    只取值、不取列名：gold 与模型生成的列别名几乎不可能相同
    （一个写"销售额"，一个写 total_amount），但值的语义是一样的。
    """
    return tuple(_canon(v) for v in row.values())


def _values_close(a: Any, b: Any) -> bool:
    """单个值是否相等（数值带容差）。"""
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) <= max(FLOAT_ABS_TOL, abs(a) * FLOAT_REL_TOL)
    return a == b


def _rows_close(gold_row: tuple, pred_row: tuple) -> bool:
    """两行是否相等（按列位置比较）。"""
    return len(gold_row) == len(pred_row) and all(
        _values_close(x, y) for x, y in zip(gold_row, pred_row)
    )


def _sort_key(values: tuple) -> tuple:
    """给行生成可排序的 key：数值排在字符串前面，避免混合类型比较报错。"""
    return tuple(
        (0, v, "") if isinstance(v, (int, float)) else (1, 0.0, str(v))
        for v in values
    )


def result_sets_equal(
    gold_rows: Sequence[dict], pred_rows: Sequence[dict], ordered: bool = False
) -> bool:
    """
    比较两个结果集是否一致。

    :param gold_rows: gold SQL 的执行结果
    :param pred_rows: 模型生成 SQL 的执行结果
    :param ordered: 为 True 时要求行序也一致
    """
    if len(gold_rows) != len(pred_rows):
        return False
    if not gold_rows:
        # 两边都为空。正常题目不该出现，这里视为相等由调用方另行判定
        return True

    gold = [_row_values(r) for r in gold_rows]
    pred = [_row_values(r) for r in pred_rows]

    if len(gold[0]) != len(pred[0]):
        # 列数不同，语义必然不同
        return False

    if not ordered:
        gold = sorted(gold, key=_sort_key)
        pred = sorted(pred, key=_sort_key)

    return all(_rows_close(g, p) for g, p in zip(gold, pred))


# --------------------------------------------------------------------------- #
# 单题判定
# --------------------------------------------------------------------------- #
def classify_case(
    gold_rows: Sequence[dict] | None,
    pred_rows: Sequence[dict] | None,
    pred_error: str | None,
) -> tuple[int, int, str]:
    """
    判定单题结果。

    :param gold_rows: gold SQL 结果集
    :param pred_rows: 模型 SQL 结果集，执行失败时为 None
    :param pred_error: 模型侧的错误描述，没有则为 None
    :return: (ex, ex_ordered, 失败原因)。失败原因为空串表示通过
    """
    ex = 0
    ex_ordered = 0
    reason = ""

    if pred_error:
        reason = pred_error
    elif pred_rows is None:
        reason = "未拿到结果集"
    elif not pred_rows:
        reason = "结果集为空"
    else:
        ex = 1 if result_sets_equal(gold_rows, pred_rows) else 0
        ex_ordered = 1 if result_sets_equal(gold_rows, pred_rows, ordered=True) else 0
        if not ex:
            reason = "结果集不一致"

    return ex, ex_ordered, reason


def is_executable(reason: str) -> int:
    """
    SQL 是否成功执行。

    用于把失败拆成两类：
      - 执行失败：SQL 语法错、表/字段不存在 → 链路根本没产出结果集
      - 结果错误：SQL 跑通了，但是查出来的数不对
    前者靠 SQL 校验/纠错能救，后者只能靠检索质量。
    """
    return 0 if reason in ("SQL 执行失败", "链路执行异常", "未拿到结果集") else 1


# --------------------------------------------------------------------------- #
# 聚合
# --------------------------------------------------------------------------- #
def mean(values: Sequence[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def stdev(values: Sequence[float]) -> float:
    return statistics.stdev(values) if len(values) >= 2 else 0.0


# --------------------------------------------------------------------------- #
# 显著性检验（配对 bootstrap）
# --------------------------------------------------------------------------- #
def paired_bootstrap_p_value(
    values_a: Sequence[float],
    values_b: Sequence[float],
    n_boot: int = 2000,
    seed: int = 42,
) -> float:
    """
    配对 bootstrap 检验（双侧），H0：两条链路在该指标上的均值差为 0。

    做法：把逐题差值平移到以 0 为中心后重采样，看"极端程度不弱于观测值"的比例。
    相比 t 检验，它不假设差值服从正态分布 —— EX 是 0/1 变量，
    天然偏态，用 bootstrap 更稳妥。

    :param values_a: 基线链路逐题指标
    :param values_b: 对比链路逐题指标（顺序必须与 a 一一对应，即同一批问题）
    :param n_boot: 重采样次数
    :return: 双侧 p 值。p < 0.05 通常认为提升显著。
    """
    if len(values_a) != len(values_b) or not values_a:
        return 1.0
    diffs = [float(b) - float(a) for a, b in zip(values_a, values_b)]
    n = len(diffs)
    observed = statistics.fmean(diffs)

    rng = random.Random(seed)
    centered = [d - observed for d in diffs]
    extreme = 0
    for _ in range(n_boot):
        sample_mean = statistics.fmean(rng.choice(centered) for _ in range(n))
        if abs(sample_mean) >= abs(observed) - 1e-12:
            extreme += 1
    # +1 平滑，避免 p 值报 0
    return (extreme + 1) / (n_boot + 1)


# --------------------------------------------------------------------------- #
# 展示辅助
# --------------------------------------------------------------------------- #
def fmt_pct(value: float, digits: int = 2) -> str:
    """0.8231 -> '82.31%'"""
    return f"{value * 100:.{digits}f}%"


def fmt_delta(value: float, digits: int = 2) -> str:
    """带正负号的百分点增量：0.051 -> '+5.10pt'"""
    return f"{value * 100:+.{digits}f}pt"


def fmt_p(p: float) -> str:
    """p 值展示：小于 0.001 时报 <0.001"""
    return "<0.001" if p < 0.001 else f"{p:.3f}"
