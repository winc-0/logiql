# 字段血缘报告

生成时间：2026-11-06 17:33

## 概况

| 项 | 结果 |
|---|---|
| 表 | 32 张 |
| 字段有血缘声明 | 190/190（100.0%） |
| 字段间的加工关系（边） | 16 条，其中 10 条由质量规则用数据验证 |
| 评测集用到的字段 | 30 个，能追到源系统 30 个 |
| 评测题全部字段可追溯 | 56/56 题 |
| 血缘自检问题 | 0 个 |

## 分层

| 层 | 表 |
|---|---|
| ODS | ods_waybill_raw |
| DWD | fact_claim, fact_delivery, fact_equipment_op, fact_freight_settle, fact_inbound, fact_inventory_daily, fact_outbound, fact_outbound_item, fact_picking, fact_return, fact_signoff, fact_transport, fact_warehouse_cost |
| DWS | dws_outbound_daily |
| ADS | （暂无） |
| DIM | dim_cargo, dim_cargo_brand, dim_cargo_sku, dim_carrier, dim_claim_type, dim_consignee, dim_date, dim_destination, dim_equipment, dim_service_level, dim_shift, dim_shipper, dim_staff, dim_supplier, dim_transport_line, dim_warehouse, dim_zone |

## 源系统

| 系统 | 说明 | 直接来源的表 |
|---|---|---|
| WMS | 仓储管理系统（入库、出库、库存、拣货、退货） | fact_inbound, fact_inventory_daily, fact_outbound, fact_outbound_item, fact_picking, fact_return |
| TMS | 运输管理系统（承运商派送、签收、干线运输） | fact_delivery, fact_outbound, fact_signoff, fact_transport, ods_waybill_raw |
| FIN | 财务结算系统（运费结算、仓成本、理赔赔付） | fact_claim, fact_freight_settle, fact_outbound, fact_warehouse_cost |
| MDM | 主数据（仓库、货品、货主、承运商等档案） | dim_cargo, dim_cargo_brand, dim_cargo_sku, dim_carrier, dim_claim_type, dim_consignee, dim_destination, dim_equipment, dim_service_level, dim_shift, dim_shipper, dim_staff, dim_supplier, dim_transport_line, dim_warehouse, dim_zone |
| IOT | 设备物联网平台（叉车、AGV、分拣线运行数据） | fact_equipment_op |
| CAL | 日历（脚本生成的日期维度） | dim_date |

## 加工关系

| 下游字段 | 上游字段 | 加工方式 | 验证规则 |
|---|---|---|---|
| dim_cargo_brand.brand_name | dim_cargo.cargo_brand | 从货品档案的品牌字段去重得到；和 dim_cargo.cargo_brand 是同一语义的两处存放 | - |
| fact_outbound.outbound_amount | fact_outbound_item.item_amount | 按出库单汇总明细行货值 | `item_amount_matches_header` |
| fact_outbound.is_ontime_outbound | fact_outbound.outbound_hours | 出库时效达标记 1 | - |
| fact_outbound.is_delivered | fact_signoff.outbound_id | 有签收记录记 1，否则记 0 | `signoff_count_matches_delivered`, `delivery_rate_signoff_path_agrees` |
| fact_outbound.is_ontime_delivery | fact_outbound.is_delivered<br>fact_outbound.delivery_hours | 已妥投且妥投时长达标记 1 | `fact_outbound_ontime_implies_delivered` |
| fact_freight_settle.paid_freight | fact_freight_settle.payable_freight<br>fact_freight_settle.extra_fee<br>fact_freight_settle.settle_status | 已结算的为应付运费加增值服务费，未结算的为 0 | `fact_freight_settle_paid_formula` |
| dws_outbound_daily.stat_date | fact_outbound.date_id | 分组键，只取 2025-09-30 及以前（缺口由质量规则 dws_covers_outbound_dates 登记） | - |
| dws_outbound_daily.warehouse_id | fact_outbound.warehouse_id | 分组键 | - |
| dws_outbound_daily.category | fact_outbound.cargo_id<br>dim_cargo.cargo_category | 用货品 ID 关联货品维表取品类，作为分组键 | - |
| dws_outbound_daily.daily_outbound_amount | fact_outbound.outbound_amount | 按分组求和 | `dws_amount_matches_fact` |
| dws_outbound_daily.daily_outbound_count | fact_outbound.outbound_id | 按分组计数 | `dws_count_matches_fact` |
| dws_outbound_daily.daily_quantity | fact_outbound.outbound_quantity | 按分组求和 | `dws_quantity_matches_fact` |

## 评测集反向校验

解析 56 道评测题 gold SQL 用到的字段，逐个检查能否追到源系统。

| 字段 | 用到的题数 | 源系统 |
|---|---|---|
| dim_date.date_id | 50 | CAL |
| dim_date.year | 50 | CAL |
| fact_outbound.date_id | 50 | WMS |
| fact_outbound.outbound_amount | 36 | WMS |
| dim_cargo.cargo_id | 21 | MDM |
| fact_outbound.cargo_id | 21 | WMS |
| dim_warehouse.warehouse_id | 15 | MDM |
| fact_outbound.warehouse_id | 15 | WMS |
| dim_cargo.cargo_category | 13 | MDM |
| dim_warehouse.region_name | 9 | MDM |
| dim_cargo.cargo_brand | 8 | MDM |
| fact_outbound.shipper_id | 8 | WMS |
| dim_date.month | 7 | CAL |
| dim_shipper.shipper_id | 7 | MDM |
| fact_outbound.outbound_quantity | 6 | WMS |
| dim_date.quarter | 5 | CAL |
| dim_shipper.shipper_level | 5 | MDM |
| dim_carrier.carrier_id | 4 | MDM |
| dim_carrier.carrier_name | 4 | MDM |
| dim_warehouse.city | 4 | MDM |
| fact_outbound.carrier_id | 4 | WMS |
| dim_warehouse.warehouse_type | 3 | MDM |
| fact_outbound.is_ontime_delivery | 2 | TMS |
| dim_cargo.cargo_name | 1 | MDM |
| dim_shipper.shipper_name | 1 | MDM |
| dim_warehouse.warehouse_name | 1 | MDM |
| fact_outbound.delivery_hours | 1 | TMS |
| fact_outbound.freight_amount | 1 | FIN |
| fact_outbound.is_delivered | 1 | TMS |
| fact_outbound.is_ontime_outbound | 1 | WMS |
