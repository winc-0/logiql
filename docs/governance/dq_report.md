# 数据质量报告

生成时间：2026-11-06 18:31

共 42 条规则：通过 37，已知缺陷 5（刻意埋设 3，规则发现 2），新问题 0，已知缺陷未检出 0，执行出错 0。

- **已知缺陷**：规则失败，但问题已经登记过，规则按预期把它查了出来。「刻意埋设」是造数时故意放进去、用来检验问答系统会不会选错表的坑；「规则发现」是规则跑出来的真实问题，已登记、暂不处理。
- **新问题**：没有登记过的失败，需要排查。
- **已知缺陷未检出**：登记过的问题这次没查出来，可能已经修好，也可能规则失效了，需要确认后更新规则。

## 表级

| 规则 | 说明 | 涉及表 | 结果 | 观测值 |
|---|---|---|---|---|
| `fact_outbound_not_empty` | 出库单事实表行数不能异常偏少 | fact_outbound | 通过 | 3066 行（要求 ≥ 1000） |
| `dws_outbound_daily_not_empty` | 出库日汇总表要有数据 | dws_outbound_daily | 通过 | 2346 行（要求 ≥ 1） |
| `fact_outbound_pk_unique` | 出库单号不能重复 | fact_outbound | 通过 | 0 组键重复，涉及 0 行 |
| `dws_outbound_daily_grain_unique` | 汇总表按「日期 + 仓库 + 品类」每组只有一行 | dws_outbound_daily | 通过 | 0 组键重复，涉及 0 行 |
| `fact_signoff_one_per_outbound` | 一张出库单最多一条签收记录 | fact_signoff | 通过 | 0 组键重复，涉及 0 行 |
| `fact_freight_settle_one_per_outbound` | 一张出库单只结算一次运费 | fact_freight_settle | 通过 | 0 组键重复，涉及 0 行 |
| `ods_waybill_no_unique` | 贴源层运单号不重复（表说明里写了「含重复单」，用规则核实） | ods_waybill_raw | 通过 | 0 组键重复，涉及 0 行 |
| `fact_inventory_daily_grain_unique` | 库存快照按「货品 + 仓库 + 日期」每组只有一行，否则求和会重复 | fact_inventory_daily | 通过 | 0 组键重复，涉及 0 行 |
| `dim_date_covers_outbound` | 日期维表覆盖出库单的全部日期 | fact_outbound, dim_date | 通过 | 731 个取值里缺 0 个 |
| `dws_covers_outbound_dates` | 汇总表覆盖出库单的全部日期 | fact_outbound, dws_outbound_daily | 已知缺陷（刻意埋设） | 731 个取值里缺 92 个，范围 20251001 ~ 20251231 |

## 字段级

| 规则 | 说明 | 涉及表 | 结果 | 观测值 |
|---|---|---|---|---|
| `fact_outbound_keys_not_null` | 出库单的货主、货品、日期、仓库、承运商都不能为空 | fact_outbound | 通过 | 0/3066 行不合格（0.00%） |
| `fact_outbound_amount_positive` | 出库货值必须大于 0 | fact_outbound | 通过 | 0/3066 行不合格（0.00%） |
| `fact_outbound_quantity_positive` | 出库件数至少 1 件 | fact_outbound | 通过 | 0/3066 行不合格（0.00%） |
| `fact_outbound_ontime_implies_delivered` | 准时妥投的单子必须已经妥投 | fact_outbound | 通过 | 0/3066 行不合格（0.00%） |
| `fact_outbound_delivery_hours_when_delivered` | 已妥投的单子必须有妥投时长 | fact_outbound | 通过 | 0/2983 行不合格（0.00%） |
| `dim_date_quarter_enum` | 季度只能是 Q1 到 Q4 | dim_date | 通过 | 0/731 行不合格（0.00%） |
| `fact_delivery_status_enum` | 派送状态在约定的四种之内 | fact_delivery | 通过 | 0/3633 行不合格（0.00%） |
| `fact_signoff_type_enum` | 签收方式在约定的四种之内 | fact_signoff | 通过 | 0/2983 行不合格（0.00%） |
| `fact_claim_status_enum` | 理赔状态在约定的三种之内 | fact_claim | 通过 | 0/151 行不合格（0.00%） |
| `fact_freight_settle_status_enum` | 结算状态在约定的三种之内 | fact_freight_settle | 通过 | 0/3066 行不合格（0.00%） |
| `fact_freight_settle_paid_formula` | 已结算的实付 = 应付 + 增值服务费；未结算的实付为 0 | fact_freight_settle | 通过 | 0/3066 行不合格（0.00%） |
| `fact_outbound_warehouse_ref` | 出库仓都能在仓库维表里找到 | fact_outbound, dim_warehouse | 通过 | 0 行找不到对应记录（0 个不同取值） |
| `fact_outbound_cargo_ref` | 货品都能在货品维表里找到 | fact_outbound, dim_cargo | 通过 | 0 行找不到对应记录（0 个不同取值） |
| `fact_outbound_shipper_ref` | 货主都能在货主维表里找到 | fact_outbound, dim_shipper | 通过 | 0 行找不到对应记录（0 个不同取值） |
| `fact_outbound_carrier_ref` | 承运商都能在承运商维表里找到 | fact_outbound, dim_carrier | 通过 | 0 行找不到对应记录（0 个不同取值） |
| `fact_signoff_outbound_ref` | 签收记录都能对应到出库单 | fact_signoff, fact_outbound | 通过 | 0 行找不到对应记录（0 个不同取值） |
| `fact_claim_outbound_ref` | 理赔单都能对应到出库单 | fact_claim, fact_outbound | 通过 | 0 行找不到对应记录（0 个不同取值） |
| `fact_signoff_date_valid` | 签收日期是真实存在的日期（不出现 20240132 这类值） | fact_signoff, dim_date | 通过 | 0 行找不到对应记录（0 个不同取值） |
| `fact_return_date_valid` | 退货日期是真实存在的日期（不出现 20240132 这类值） | fact_return, dim_date | 通过 | 0 行找不到对应记录（0 个不同取值） |
| `dim_date_covers_signoff` | 日期维表覆盖全部签收日期 | fact_signoff, dim_date | 已知缺陷（规则发现） | 15 行找不到对应记录（5 个不同取值） |
| `dim_date_covers_return` | 日期维表覆盖全部退货日期 | fact_return, dim_date | 已知缺陷（规则发现） | 3 行找不到对应记录（3 个不同取值） |
| `ods_non_test_waybill_in_fact` | 贴源层的正式运单（非测试单）都进了出库单事实表 | ods_waybill_raw, fact_outbound | 通过 | 0 行找不到对应记录（0 个不同取值） |
| `ods_low_amount_is_test` | 贴源层货值低于 10 元的都应该是测试单，否则就是没被标记的异常值 | ods_waybill_raw | 通过 | 0/975 行不合格（0.00%） |

## 跨表对账

| 规则 | 说明 | 涉及表 | 结果 | 观测值 |
|---|---|---|---|---|
| `dws_amount_matches_fact` | 汇总表每组（日期 + 仓库 + 品类）的货值 = 明细按同样分组求和 | dim_cargo, dws_outbound_daily, fact_outbound | 通过 | 配对 2346 组，不一致 0 组，左 12,844,918.81，右 16,157,440.3 |
| `dws_count_matches_fact` | 汇总表每组的出库单量 = 明细按同样分组计数 | dim_cargo, dws_outbound_daily, fact_outbound | 通过 | 配对 2346 组，不一致 0 组，左 2,448，右 3,066 |
| `dws_quantity_matches_fact` | 汇总表每组的出库件数 = 明细按同样分组求和 | dim_cargo, dws_outbound_daily, fact_outbound | 通过 | 配对 2346 组，不一致 0 组，左 23,386，右 29,536 |
| `item_amount_matches_header` | 每张出库单的明细行货值之和 = 单头货值 | fact_outbound, fact_outbound_item | 通过 | 配对 3066 组，不一致 0 组，左 16,157,440.3，右 16,157,440.3 |
| `ods_amount_matches_fact` | 贴源层正式运单的原始货值 = 出库单事实表的货值（清洗没有改动正常单） | fact_outbound, ods_waybill_raw | 通过 | 配对 836 组，不一致 0 组，左 4,459,610.71，右 16,157,440.3 |
| `signoff_count_matches_delivered` | 签收记录条数 = 出库单里已妥投的单数 | fact_outbound, fact_signoff | 通过 | 左 2,983，右 2,983 |

## 口径一致性

| 规则 | 说明 | 涉及表 | 结果 | 观测值 |
|---|---|---|---|---|
| `delivery_rate_signoff_path_agrees` | 妥投率两条正确路径一致：出库单 is_delivered 均值 = 签收单数 / 出库单数 | fact_outbound, fact_signoff | 通过 | 左 0.9729，右 0.9729 |
| `delivery_rate_wrong_table` | 只在签收表范围内算妥投率，会和正确口径不一致 | fact_outbound, fact_signoff | 已知缺陷（刻意埋设） | 左 0.9729，右 1 |
| `amount_2025_dws_vs_fact` | 2025 年出库货值，用汇总表算和用明细表算应该一样 | dws_outbound_daily, fact_outbound | 已知缺陷（刻意埋设） | 左 9,109,068.37，右 5,796,546.88 |

## 失败明细

### `dws_covers_outbound_dates`：已知缺陷（刻意埋设）

汇总表覆盖出库单的全部日期

- 观测值：731 个取值里缺 92 个，范围 20251001 ~ 20251231
- 已登记的原因：造数时刻意埋的坑：汇总表只回溯到 2025-09-30，2025 年 10 至 12 月缺失。 问 2025Q4 或全年合计时如果用汇总表，结果会偏小，必须回到 fact_outbound。

### `dim_date_covers_signoff`：已知缺陷（规则发现）

日期维表覆盖全部签收日期

- 观测值：15 行找不到对应记录（5 个不同取值）
- 已登记的原因：日期维表只到 2025-12-31，12 月底出库的单子在 2026 年初签收，这些签收日期关联不到日期维表， 按日期维表过滤时会被漏掉。dim_date 由 dw_expand.sql 生成，56 道评测题依赖它，暂不扩展。

| value | rows_count |
|---|---|
| 20260101 | 7 |
| 20260102 | 4 |
| 20260103 | 2 |
| 20260105 | 1 |
| 20260107 | 1 |

### `dim_date_covers_return`：已知缺陷（规则发现）

日期维表覆盖全部退货日期

- 观测值：3 行找不到对应记录（3 个不同取值）
- 已登记的原因：与 dim_date_covers_signoff 同一个原因：日期维表只到 2025 年底，2026 年初的退货日期关联不到。

| value | rows_count |
|---|---|
| 20260101 | 1 |
| 20260104 | 1 |
| 20260110 | 1 |

### `delivery_rate_wrong_table`：已知缺陷（刻意埋设）

只在签收表范围内算妥投率，会和正确口径不一致

- 观测值：左 0.9729，右 1
- 已登记的原因：造数时刻意埋的坑：fact_signoff 只包含已签收的单子，以它为范围算妥投率恒为 100%， 比正确口径偏高。妥投率的分母必须是全部出库单。

| 键 | 左 | 右 | 差额 |
|---|---|---|---|
| (整体) | 0.9729 | 1 | -0.0271 |

### `amount_2025_dws_vs_fact`：已知缺陷（刻意埋设）

2025 年出库货值，用汇总表算和用明细表算应该一样

- 观测值：左 9,109,068.37，右 5,796,546.88
- 已登记的原因：与 dws_covers_outbound_dates 是同一个缺陷：汇总表缺 2025 年 10 至 12 月， 用它算全年货值会少掉第四季度。这条规则把「少多少」量化出来。

| 键 | 左 | 右 | 差额 |
|---|---|---|---|
| (整体) | 9,109,068.37 | 5,796,546.88 | 3,312,521.49 |
