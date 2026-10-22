# NL2SQL 评测报告

- 评测场景：**32表大schema**
- 元数据规模：**32 张表 / 163 个字段 / 7 个指标**
- 标注集：nl2sql_gold.jsonl，共 56 题，按 A–F 六档难度分组
- 主指标：**Execution Accuracy（执行准确率）**
- 生成时间：20261019-012808

## 1. 逐层累加消融

| 策略 | 相对上一层新增 | EX | EX（含行序） | 可执行率 | ΔEX | p 值 |
|---|---|---:|---:|---:|---:|---:|
| `llm_only` | 基线：全量元数据直接灌输 | 69.64% | 64.29% | 89.29% | — | — |
| `col_recall` | 改为按语义召回字段 | 66.07% | 58.93% | 94.64% | -3.57pt | 0.683 |
| `col_val_recall` | 补充字段真实取值 | 71.43% | 64.29% | 94.64% | +5.36pt | 0.116 |
| `full_recall` | 补充指标口径定义 | 73.21% | 66.07% | 94.64% | +1.79pt | 0.754 |
| `llm_filter` | 加 LLM 精筛表与指标 | 62.50% | 60.71% | 80.36% | -10.71pt | 0.089 |
| `full` | 加 SQL 校验与纠错 | 67.86% | 60.71% | 85.71% | +5.36pt | 0.336 |

> Δ 与 p 值只对**相邻两层**计算，保证每一分提升都能干净归因到一个组件。p 值用配对 bootstrap 检验（同一批问题逐题配对、2000 次重采样、双侧），p < 0.05 才认为提升不太可能来自随机波动。

## 2. 各层相对裸 LLM 基线

| 策略 | EX | 相对基线 | p 值 |
|---|---:|---:|---:|
| `col_recall` | 66.07% | -3.57pt | 0.683 |
| `col_val_recall` | 71.43% | +1.79pt | 0.882 |
| `full_recall` | 73.21% | +3.57pt | 0.683 |
| `llm_filter` | 62.50% | -7.14pt | 0.437 |
| `full` | 67.86% | -1.79pt | 0.916 |

## 3. 分难度档拆解

| 难度档 | 题数 | `llm_only` | `col_recall` | `col_val_recall` | `full_recall` | `llm_filter` | `full` |
|---|---:|---:|---:|---:|---:|---:|---:|
| A 单表聚合 | 6 | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 83.33% |
| B 单维分组 | 10 | 90.00% | 70.00% | 70.00% | 60.00% | 30.00% | 40.00% |
| C TopN 排序 | 8 | 50.00% | 62.50% | 75.00% | 75.00% | 62.50% | 75.00% |
| D 精确取值 | 8 | 87.50% | 62.50% | 75.00% | 87.50% | 75.00% | 62.50% |
| E 时间对比/指标 | 8 | 62.50% | 75.00% | 87.50% | 87.50% | 100.00% | 100.00% |
| F 多表交叉/子查询/窗口函数 | 16 | 50.00% | 50.00% | 50.00% | 56.25% | 43.75% | 62.50% |

## 5. 延迟代价

| 策略 | 平均单题耗时 | 相对基线 | 总耗时 |
|---|---:|---:|---:|
| `llm_only` | 7.3s | 1.00x | 6.8 分钟 |
| `col_recall` | 7.1s | 0.97x | 6.6 分钟 |
| `col_val_recall` | 7.2s | 0.99x | 6.7 分钟 |
| `full_recall` | 8.4s | 1.15x | 7.8 分钟 |
| `llm_filter` | 9.3s | 1.28x | 8.7 分钟 |
| `full` | 9.5s | 1.30x | 8.9 分钟 |

## 6. 完整链路失败归因

| 失败原因 | 题数 |
|---|---:|
| 结果集不一致 | 9 |
| 链路执行异常：OperationalError | 8 |
| 结果集为空 | 1 |

失败题清单：

- `a02`（单表聚合 档）：链路执行异常：OperationalError — (asyncmy.errors.OperationalError) (1054, "Unknown column 'date' in 'where clause'") [SQL: SELECT SUM(daily_orders) AS ORDER_COUNT FROM dws_order_daily WHERE date <= '2026-10-19';] (Background on this 
- `b02`（单维分组 档）：结果集不一致
- `b03`（单维分组 档）：结果集不一致
- `b06`（单维分组 档）：结果集不一致
- `b08`（单维分组 档）：链路执行异常：OperationalError — (asyncmy.errors.OperationalError) (1267, "Illegal mix of collations (utf8mb4_0900_ai_ci,IMPLICIT) and (utf8mb4_general_ci,IMPLICIT) for operation '='") [SQL: SELECT    dc.gender AS 性别,   SUM(fp.paid_a
- `b09`（单维分组 档）：结果集不一致
- `b10`（单维分组 档）：结果集不一致
- `c06`（TopN 排序 档）：结果集为空
- `c07`（TopN 排序 档）：链路执行异常：OperationalError — (asyncmy.errors.OperationalError) (1267, "Illegal mix of collations (utf8mb4_0900_ai_ci,IMPLICIT) and (utf8mb4_general_ci,IMPLICIT) for operation '='") [SQL: SELECT dc.customer_name FROM fact_payment 
- `d01`（精确取值 档）：链路执行异常：OperationalError — (asyncmy.errors.OperationalError) (1267, "Illegal mix of collations (utf8mb4_0900_ai_ci,IMPLICIT) and (utf8mb4_general_ci,IMPLICIT) for operation '='") [SQL: SELECT SUM(daily_sales) AS sales FROM dws_
- `d06`（精确取值 档）：链路执行异常：OperationalError — (asyncmy.errors.OperationalError) (1267, "Illegal mix of collations (utf8mb4_0900_ai_ci,IMPLICIT) and (utf8mb4_general_ci,IMPLICIT) for operation '='") [SQL: SELECT SUM(fp.paid_amount) AS consumption_
- `d08`（精确取值 档）：结果集不一致
- `f01`（多表交叉/子查询/窗口函数 档）：链路执行异常：OperationalError — (asyncmy.errors.OperationalError) (1267, "Illegal mix of collations (utf8mb4_0900_ai_ci,IMPLICIT) and (utf8mb4_general_ci,IMPLICIT) for operation '='") [SQL: SELECT    t1.category AS 品类,   SUM(t1.dail
- `f04`（多表交叉/子查询/窗口函数 档）：链路执行异常：OperationalError — (asyncmy.errors.OperationalError) (1267, "Illegal mix of collations (utf8mb4_0900_ai_ci,IMPLICIT) and (utf8mb4_general_ci,IMPLICIT) for operation '='") [SQL: SELECT    dr.region_name AS 地区,   SUM(fp.p
- `f05`（多表交叉/子查询/窗口函数 档）：结果集不一致
- `f08`（多表交叉/子查询/窗口函数 档）：结果集不一致
- `f14`（多表交叉/子查询/窗口函数 档）：结果集不一致
- `f15`（多表交叉/子查询/窗口函数 档）：链路执行异常：OperationalError — (asyncmy.errors.OperationalError) (1267, "Illegal mix of collations (utf8mb4_0900_ai_ci,IMPLICIT) and (utf8mb4_general_ci,IMPLICIT) for operation '='") [SQL: SELECT    dr.province AS 省份,   SUM(dod.dai

## 7. 结论

- 本次评测中没有任何一层带来统计上显著的提升。
- 差距主要来自 F 档难题：`llm_only` 50.00%，`full` 62.50%。查询越复杂，交给模型的 schema 就越需要精准——这正是精筛环节的用武之地。
