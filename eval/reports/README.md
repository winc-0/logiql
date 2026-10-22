# 评测结果索引

这个目录是**唯一可信的评测证据**。`*.md` 是 `*.json` 的渲染结果，`*.json` 才是原始数据。

规矩只有一条：**任何出现在 README 里的数字，都必须能在这里找到一个 JSON 对得上。**

## 现行结论用哪几份

| 文件 | 来源 JSON | 元数据规模 | 用途 |
|---|---|---|---|
| [`nl2sql_report_small_6table.md`](nl2sql_report_small_6table.md) | `nl2sql_20261021-191836.json` | 6 表 / 38 字段 / 3 指标 | README「结果 A」 |
| [`nl2sql_report_large_32table.md`](nl2sql_report_large_32table.md) | `nl2sql_20261021-190409.json` | 32 表 / 190 字段 / 8 指标 | README「结果 B」 |
| [`schema_compare.md`](schema_compare.md) | 上面两份 | 两者对照 | README「交叉对比」 |

每份报告头部都写了：生成时间、来源 JSON、代码版本、生成命令。缺了这几行就说明它是早期产物。

## 归档（已废弃，不要引用数字）

[`archive/`](archive/) 里是元数据扩充之前的轮次，文件头有醒目的废弃说明。**保留而不删**，是因为它们是「重复性」这条结论的原始证据：

- `archive/nl2sql_report_small_20261018.md` ← `nl2sql_20261018-222231.json`（6 表 / 24 字段）
- `archive/nl2sql_report_large_20261019.md` ← `nl2sql_20261019-012808.json`（32 表 / 163 字段）

## 全部原始结果

| JSON | 题数 | 策略数 | 元数据 | 说明 |
|---|---:|---:|---|---|
| `nl2sql_20261018-205424.json` | 2 | 1 | 未记录 | 首次冒烟 |
| `nl2sql_20261018-213052.json` | 40 | 6 | 未记录 | 40 题全量消融（扩题到 56 之前） |
| `nl2sql_20261018-222231.json` | 56 | 6 | 6 表 / 24 字段 | **旧**小 schema 全量，见归档 |
| `nl2sql_20261019-001312.json` | 2 | 1 | 5 表 / 24 字段 | 冒烟 |
| `nl2sql_20261019-001816.json` | 3 | 2 | 32 表 / 163 字段 | 大 schema 冒烟 |
| `nl2sql_20261019-012808.json` | 56 | 6 | 32 表 / 163 字段 | **旧**大 schema 全量（串行），见归档 |
| `nl2sql_20261019-120252.json` | 3 | 2 | 32 表 / 163 字段 | 冒烟 |
| `nl2sql_20261019-120329.json` | 2 | 1 | 32 表 / 163 字段 | 冒烟 |
| `nl2sql_20261019-120404.json` | 4 | 2 | 32 表 / 163 字段 | 冒烟 |
| `nl2sql_20261019-122423.json` | 56 | 6 | 32 表 / 163 字段 | **旧**大 schema 全量（8 并发）——与上一行构成「串行 vs 并发」对照 |
| `nl2sql_20261019-122648.json` | 12 | 1 | 32 表 / 163 字段 | 重复性实验第 1 次 |
| `nl2sql_20261019-122734.json` | 12 | 1 | 32 表 / 163 字段 | 重复性实验第 2 次 |
| `nl2sql_20261019-123012.json` | 12 | 1 | 32 表 / 163 字段 | 重复性实验第 3 次 |
| `nl2sql_20261021-190409.json` | 56 | 6 | 32 表 / 190 字段 | **现行**大 schema，README 结果 B |
| `nl2sql_20261021-191836.json` | 56 | 6 | 6 表 / 38 字段 | **现行**小 schema，README 结果 A |

「重复性实验」三份是同一配置（`--strategies full_recall --limit 12`）连跑三次，
用来量化「同一套代码、同一批题、同样 temperature=0 时结果能差多少」。结论见 README
「重复性」一节。

## 怎么重新生成

```bash
# 只重渲染报告，不重跑评测（改报告格式时用）
python -m eval.run_eval --from-json eval/reports/nl2sql_20261021-191836.json \
                        --report eval/reports/nl2sql_report_small_6table.md

# 重跑评测（会产生新的 JSON，并自动写进报告头部）
# 注意并发度要跟现行两轮保持一致（按耗时反推约 4）。改并发度会改变结果，见根目录 README「重复性」
python -m eval.run_eval --tag "6表小schema" --concurrency 4

# 两轮交叉对比
python -m eval.compare_schema eval/reports/nl2sql_20261021-191836.json \
                              eval/reports/nl2sql_20261021-190409.json
```

## 为什么 JSON 也进仓库

早期版本把 `eval/reports/*.json` 写进了 `.gitignore`，只提交渲染后的 md。后果是：
README 说 +7.14pt / p=0.049，`nl2sql_report_large.md` 说 +1.79pt / p=0.754，
而没人能证明这两句其实来自**不同轮次**——因为产生它们的原始数据不在仓库里。

现在全部 JSON 入库（15 份合计约 1.3MB）。这个体积换「每个数字可复核」，值得。
