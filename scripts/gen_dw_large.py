#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
把 dw 从 6 张表扩张到 32 张表，用于验证「schema 规模是否为 NL2SQL 策略的决定性变量」。

背景：
    首轮评测（56 题 x 6 组）里出现一个反直觉的结果——三路召回 + LLM 精筛的
    完整链路 EX 92.86%，而「把全部元数据直接灌输给大模型」的裸基线也有 85.71%，
    单独使用召回反而低于基线。

    一个合理的解释是：基线 dw 只有 6 张表，全量 schema 才约 2k tokens，
    大模型完全塞得下，于是「先召回再筛选」这套流程的价值无从体现，反而因为
    召回了无关字段引入噪声。若 schema 扩张到真实企业数仓的规模，
    「全量灌输」应当会因上下文膨胀而退化。

    本脚本就是为验证这个假设准备数据侧基础设施。

设计的关键（决定实验有没有说服力）：
    1. 不改动基线 6 张表的任何结构与数据。所有 56 道评测题的 gold SQL
       依然可执行且结果不变 —— 于是「题目」成为控制变量，唯一自变量是 schema 规模。
    2. 新表不是随便堆数量，而是刻意制造真实企业数仓中会遇到的二义性：
        a. 数仓分层同名：ods_waybill_raw（贴源未清洗）/ fact_outbound（明细，gold 用这张）/
           dws_outbound_daily（预聚合汇总）
        b. 金额口径多态：outbound_amount(出库货值，收入口径) / freight_amount(对客户应收运费) /
           payable_freight(对承运商应付运费，成本口径) / paid_freight(实付) /
           daily_outbound_amount(汇总货值)
        c. 汇总表覆盖不全：dws_outbound_daily 只回溯到 2025-09，Q4 缺失 ——
           真实数仓的汇总表常因离线任务回溯有限而不完整，选错层就答错
        d. 货品粒度多态：cargo / cargo_sku / cargo_brand 三张维表，各自都有「名称」字段
        e. 方向相反的同名指标：入库单量 vs 出库单量，两张事实表都能数「单量」
        f. 口径不同的同名过程表：fact_signoff 只含已签收单，用它算妥投率必然偏高
    3. 每张新表的描述与别名都写清楚它的**适用边界**（什么场景能用、什么场景不能用），
       而不是含糊带过。否则召回系统没有可供判别的依据，实验就变成不公平的。

用法：
    python scripts/gen_dw_large.py                # 生成 SQL 与 meta_config_large.yaml
    python scripts/gen_dw_large.py --report       # 只打印规模统计
"""
from __future__ import annotations

import argparse
import datetime as dt
import random
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ------------------------------------------------------------------ 声明式表定义

ROLE_FACT, ROLE_DIM = "fact", "dim"
PK, FK, DIM, MEASURE = "primary_key", "foreign_key", "dimension", "measure"


@dataclass(frozen=True)
class Col:
    """一个字段的元数据定义。sync=True 时该字段的取值会被采样进 Elasticsearch。"""

    name: str
    role: str
    desc: str
    alias: list[str] = field(default_factory=list)
    sync: bool = False


@dataclass
class Table:
    name: str
    role: str
    desc: str
    cols: list[Col]
    # 建表 SQL 里的列定义部分（含类型与注释）
    ddl_cols: list[str]
    # 主键列组合。默认从 role=primary_key 的字段推导；
    # 复合主键（如 dws_order_daily 的 日期+大区+品类）必须显式声明，否则校验会漏
    pk_cols: list[str] | None = None

    def primary_keys(self) -> list[str]:
        if self.pk_cols:
            return self.pk_cols
        return [c.name for c in self.cols if c.role == PK]


def T(name: str, role: str, desc: str, ddl_cols: list[str], cols: list[Col],
      pk_cols: list[str] | None = None) -> Table:
    return Table(name=name, role=role, desc=desc, cols=cols, ddl_cols=ddl_cols, pk_cols=pk_cols)


TABLES: list[Table] = [
    # ---------------------------------------------- 数仓分层干扰（出库主链路）
    T(
        "ods_waybill_raw", ROLE_FACT,
        "贴源层原始运单表，由 TMS 业务库直接同步而来，未经任何清洗：含测试单、重复单与异常货值。"
        "本表仅用于数据溯源与问题排查，**不参与对外口径的业务统计**。"
        "若要做出库货值、出库单量等正式口径分析，请使用 fact_outbound（已清洗的事实明细）。",
        [
            "raw_id       INT PRIMARY KEY COMMENT '原始记录自增ID'",
            "waybill_no   VARCHAR(32)  NOT NULL COMMENT '业务运单号'",
            "cargo_id     VARCHAR(20)  NOT NULL COMMENT '货品ID'",
            "shipper_id   VARCHAR(20)  NOT NULL COMMENT '货主ID'",
            "ship_date    DATE         NOT NULL COMMENT '发货日期'",
            "amount_raw   DECIMAL(12,2) COMMENT '未清洗的原始货值，可能含异常值'",
            "weight_raw   DECIMAL(10,2) COMMENT '未清洗的原始重量，可能含异常值'",
            "is_test      TINYINT      COMMENT '是否为测试单，1=是'",
            "load_time    DATETIME     COMMENT '同步入库时间'",
        ],
        [
            Col("raw_id", PK, "原始记录自增ID", ["原始ID"], False),
            Col("waybill_no", DIM, "业务系统原始运单号", ["原始运单号", "运单号"], False),
            Col("cargo_id", FK, "货品标识", ["货品ID"], False),
            Col("shipper_id", FK, "货主标识", ["货主ID"], False),
            Col("ship_date", DIM, "发货日期", ["发货日期"], False),
            Col("amount_raw", MEASURE, "未清洗的原始货值，含测试单与异常单，不可用于正式口径统计", ["原始货值"], False),
            Col("weight_raw", MEASURE, "未清洗的原始重量，含异常值，不可用于正式口径统计", ["原始重量"], False),
            Col("is_test", DIM, "是否为测试单标记，1 表示测试单", ["测试标记"], True),
            Col("load_time", DIM, "同步入库时间", [], False),
        ],
    ),
    T(
        "dws_outbound_daily", ROLE_FACT,
        "出库日汇总表，按「日期 + 仓库 + 品类」粒度预聚合每日的出库结果，用于加速固定看板的查询。"
        "**已聚合，无法下钻到单笔出库单、单个货主或单个货品**。"
        "注意覆盖边界：本表由离线任务回溯生成，当前仅汇总到 2025 年 9 月，"
        "2025 年 10 月之后的数据缺失；涉及 2025Q4 或全年合计的统计不能用本表，必须回到 fact_outbound。",
        [
            "stat_date             INT          NOT NULL COMMENT '统计日期'",
            "warehouse_id          VARCHAR(20)  NOT NULL COMMENT '仓库ID'",
            "category              VARCHAR(32)  NOT NULL COMMENT '货品品类'",
            "daily_outbound_amount DECIMAL(14,2) COMMENT '当日出库货值合计'",
            "daily_outbound_count  INT          COMMENT '当日出库单量'",
            "daily_quantity        INT          COMMENT '当日出库件数合计'",
            # 粒度是「日期 + 仓库 + 品类」，单列主键会导致同一天多条互相覆盖
            "PRIMARY KEY (stat_date, warehouse_id, category)",
        ],
        [
            Col("stat_date", PK, "统计日期，关联 dim_date", ["汇总日期"], False),
            Col("warehouse_id", FK, "出库仓ID", ["仓库ID"], False),
            Col("category", DIM, "货品品类", ["品类", "类别"], True),
            Col("daily_outbound_amount", MEASURE, "当日出库货值合计，已聚合，不能下钻到单笔出库单", ["每日出库货值", "汇总货值"], False),
            Col("daily_outbound_count", MEASURE, "当日出库单量，已聚合", ["每日出库单量"], False),
            Col("daily_quantity", MEASURE, "当日出库件数合计，已聚合", ["每日出库件数"], False),
        ],
        pk_cols=["stat_date", "warehouse_id", "category"],
    ),
    T(
        "fact_outbound_item", ROLE_FACT,
        "出库单明细表，一张出库单可能拆成多行（不同货品或分批出库），粒度比 fact_outbound 更细。"
        "**在明细表上直接 COUNT(*) 会把单量夸大**——统计出库单量应回到 fact_outbound，"
        "本表只用于分析明细行级别的结构。",
        [
            "item_id        INT PRIMARY KEY COMMENT '明细行自增ID'",
            "outbound_id    VARCHAR(32)  COMMENT '所属出库单号'",
            "cargo_id       VARCHAR(20)  COMMENT '货品ID'",
            "item_quantity  INT          COMMENT '该明细行件数'",
            "item_amount    DECIMAL(12,2) COMMENT '该明细行货值'",
            "discount_rate  DECIMAL(6,4) COMMENT '该明细行的折扣率'",
        ],
        [
            Col("item_id", PK, "明细行自增ID", ["明细ID"], False),
            Col("outbound_id", FK, "所属出库单号", ["出库单号"], False),
            Col("cargo_id", FK, "货品标识", ["货品ID"], False),
            Col("item_quantity", MEASURE, "该明细行的出库件数", ["明细件数"], False),
            Col("item_amount", MEASURE, "该明细行的出库货值，同一出库单多行相加等于主表的出库货值", ["明细金额"], False),
            Col("discount_rate", MEASURE, "折扣率，0.9 表示九折，用于分析折扣结构，不是金额", ["折扣"], False),
        ],
    ),

    # ---------------------------------------------- 履约执行（派送 / 签收）
    T(
        "fact_delivery", ROLE_FACT,
        "派送事实表，记录出库单交给承运商之后的实际派送过程（派送员、派送次数、派送状态）。"
        "本表是**过程表**，一笔出库单可能产生多条派送记录（首次派送失败会再次派送）；"
        "统计派送单量前先确认是否需要去重。最终是否送到要看 fact_outbound 的妥投标记。",
        [
            "delivery_id     INT PRIMARY KEY COMMENT '派送记录自增ID'",
            "outbound_id     VARCHAR(32)  COMMENT '出库单号'",
            "carrier_id      VARCHAR(20)  COMMENT '承运商ID'",
            "courier_name    VARCHAR(32)  COMMENT '派送员姓名'",
            "dispatch_hours  INT          COMMENT '出库到开始派送的小时数'",
            "delivery_status VARCHAR(16)  COMMENT '派送状态：已签收/在途/拒收/派送失败'",
            "attempt_count   INT          COMMENT '累计派送次数'",
        ],
        [
            Col("delivery_id", PK, "派送记录自增ID", ["派送ID"], False),
            Col("outbound_id", FK, "出库单号", ["出库单号"], False),
            Col("carrier_id", FK, "承运商ID", ["承运商ID"], False),
            Col("courier_name", DIM, "派送员姓名", ["派送员", "快递员"], True),
            Col("dispatch_hours", MEASURE, "出库到开始派送的小时数", ["派送响应时长"], False),
            Col("delivery_status", DIM, "派送状态，取值为已签收、在途、拒收、派送失败", ["派送状态", "配送状态"], True),
            Col("attempt_count", MEASURE, "累计派送次数，大于 1 表示首次未成功", ["派送次数"], False),
        ],
    ),
    T(
        "fact_signoff", ROLE_FACT,
        "签收事实表，记录出库单的最终签收结果（签收方式、是否本人签收、是否异常签收）。"
        "本表只包含**已签收**的单据，未签收（丢件、拒收）的单据不在此表，"
        "因此用本表统计妥投率会偏高——统计妥投率应使用 fact_outbound 的 is_delivered 标记。",
        [
            "signoff_id    INT PRIMARY KEY COMMENT '签收记录自增ID'",
            "outbound_id   VARCHAR(32)  COMMENT '出库单号'",
            "signoff_date  INT          COMMENT '签收日期'",
            "signoff_type  VARCHAR(16)  COMMENT '签收方式：本人签收/他人代收/驿站代收/智能柜'",
            "is_self_sign  TINYINT      COMMENT '是否本人签收，1=是'",
            "is_exception  TINYINT      COMMENT '是否异常签收（破损、短少等），1=是'",
        ],
        [
            Col("signoff_id", PK, "签收记录自增ID", ["签收ID"], False),
            Col("outbound_id", FK, "出库单号", ["出库单号"], False),
            Col("signoff_date", FK, "签收日期，关联 dim_date", ["签收日期"], False),
            Col("signoff_type", DIM, "签收方式，取值为本人签收、他人代收、驿站代收、智能柜", ["签收方式"], True),
            Col("is_self_sign", MEASURE, "是否本人签收标记，1 表示本人签收", ["本人签收"], False),
            Col("is_exception", MEASURE, "是否异常签收标记，1 表示签收时就发现了破损或短少", ["异常签收"], False),
        ],
    ),

    # ---------------------------------------------- 入库与库存
    T(
        "fact_inbound", ROLE_FACT,
        "入库事实表，记录供应商送货到仓并完成验收的入库单。是「入库」这一动作的事实表，"
        "与出库方向的 fact_outbound 是**相反的两个业务动作**，统计「单量」时必须先分清方向。",
        [
            "inbound_id      INT PRIMARY KEY COMMENT '入库单自增ID'",
            "supplier_id     VARCHAR(20)  COMMENT '供应商ID'",
            "warehouse_id    VARCHAR(20)  COMMENT '收货仓库ID'",
            "cargo_id        VARCHAR(20)  COMMENT '货品ID'",
            "date_id         INT          COMMENT '入库日期'",
            "inbound_quantity INT         COMMENT '入库件数'",
            "accept_hours    INT          COMMENT '到货到验收完成的小时数'",
            "is_qualified    TINYINT      COMMENT '验收是否合格，1=合格'",
        ],
        [
            Col("inbound_id", PK, "入库单自增ID", ["入库单号"], False),
            Col("supplier_id", FK, "供应商ID", ["供应商ID"], False),
            Col("warehouse_id", FK, "收货仓库ID", ["仓库ID"], False),
            Col("cargo_id", FK, "货品ID", ["货品ID"], False),
            Col("date_id", FK, "入库日期，关联 dim_date", ["入库日期"], False),
            Col("inbound_quantity", MEASURE, "入库件数", ["入库数量"], False),
            Col("accept_hours", MEASURE, "到货到验收完成的小时数，衡量收货效率", ["验收时长"], False),
            Col("is_qualified", MEASURE, "验收是否合格标记，1 表示合格", ["验收合格"], False),
        ],
    ),
    T(
        "fact_inventory_daily", ROLE_FACT,
        "库存日快照表，按「货品 + 仓库 + 日期」记录每日闭仓时的在库件数与在库货值。"
        "**是快照不是流水**，同一批货在多个日期会重复出现，跨日期直接求和会重复计算；"
        "要看某一天的库存就限定单日，看周转则要用平均库存。",
        [
            "snapshot_id    INT PRIMARY KEY COMMENT '快照自增ID'",
            "cargo_id       VARCHAR(20)  COMMENT '货品ID'",
            "warehouse_id   VARCHAR(20)  COMMENT '仓库ID'",
            "date_id        INT          COMMENT '库存日期'",
            "stock_quantity INT          COMMENT '在库件数'",
            "stock_amount   DECIMAL(14,2) COMMENT '在库货值'",
            "stock_days     INT          COMMENT '库龄天数'",
        ],
        [
            Col("snapshot_id", PK, "快照自增ID", ["快照ID"], False),
            Col("cargo_id", FK, "货品ID", ["货品ID"], False),
            Col("warehouse_id", FK, "仓库ID", ["仓库ID"], False),
            Col("date_id", FK, "库存日期，关联 dim_date", ["库存日期"], False),
            Col("stock_quantity", MEASURE, "当日闭仓在库件数，跨日期求和会重复计算", ["在库件数", "库存数量"], False),
            Col("stock_amount", MEASURE, "当日闭仓在库货值，跨日期求和会重复计算", ["在库货值", "库存金额"], False),
            Col("stock_days", MEASURE, "库龄天数，衡量货物在仓存放了多久", ["库龄"], False),
        ],
    ),

    # ---------------------------------------------- 仓内作业
    T(
        "fact_picking", ROLE_FACT,
        "拣货作业事实表，记录仓内操作员按出库单拣货的作业量与差错情况。"
        "这是**仓内作业**数据，与出库单本身是一对多的关系（一张出库单可能多人分捡），"
        "统计出库单量不要使用本表。",
        [
            "picking_id   INT PRIMARY KEY COMMENT '拣货记录自增ID'",
            "outbound_id  VARCHAR(32)  COMMENT '出库单号'",
            "staff_id     VARCHAR(20)  COMMENT '拣货员ID'",
            "shift_id     VARCHAR(20)  COMMENT '班次ID'",
            "pick_lines   INT          COMMENT '拣货行数'",
            "pick_minutes INT          COMMENT '拣货耗时（分钟）'",
            "error_lines  INT          COMMENT '拣货差错行数'",
        ],
        [
            Col("picking_id", PK, "拣货记录自增ID", ["拣货ID"], False),
            Col("outbound_id", FK, "出库单号", ["出库单号"], False),
            Col("staff_id", FK, "拣货员ID", ["员工ID"], False),
            Col("shift_id", FK, "班次ID", ["班次ID"], False),
            Col("pick_lines", MEASURE, "拣货行数", ["拣货行数"], False),
            Col("pick_minutes", MEASURE, "拣货耗时（分钟）", ["拣货时长"], False),
            Col("error_lines", MEASURE, "拣货差错行数，衡量拣货质量", ["差错行数"], False),
        ],
    ),
    T(
        "fact_equipment_op", ROLE_FACT,
        "仓储装备作业事实表，记录叉车、AGV、分拣线等装备每日的运行与能耗。"
        "这是**装备运行**数据，与出库业务没有单据级关联，不能用来统计出库量或运费。",
        [
            "op_id        INT PRIMARY KEY COMMENT '作业记录自增ID'",
            "equipment_id VARCHAR(20)  COMMENT '装备ID'",
            "warehouse_id VARCHAR(20)  COMMENT '所在仓库ID'",
            "date_id      INT          COMMENT '作业日期'",
            "run_hours    DECIMAL(8,2) COMMENT '当日运行时长（小时）'",
            "energy_kwh   DECIMAL(10,2) COMMENT '当日能耗（度）'",
            "fault_count  INT          COMMENT '当日故障次数'",
        ],
        [
            Col("op_id", PK, "作业记录自增ID", ["作业ID"], False),
            Col("equipment_id", FK, "装备ID", ["设备ID"], False),
            Col("warehouse_id", FK, "所在仓库ID", ["仓库ID"], False),
            Col("date_id", FK, "作业日期，关联 dim_date", ["作业日期"], False),
            Col("run_hours", MEASURE, "当日运行时长（小时）", ["运行时长"], False),
            Col("energy_kwh", MEASURE, "当日能耗（度）", ["能耗", "用电量"], False),
            Col("fault_count", MEASURE, "当日故障次数", ["故障次数"], False),
        ],
    ),

    # ---------------------------------------------- 异常与逆向
    T(
        "fact_claim", ROLE_FACT,
        "异常理赔事实表，记录出库单发生破损、丢失、延误等异常后的理赔申请与处理结果。"
        "这是**负向指标**表：常规的出库货值、出库单量统计不扣减理赔，也不应 JOIN 本表；"
        "只有在问题明确提到「理赔」「赔付」「异常件」时才使用。",
        [
            "claim_id      VARCHAR(32) PRIMARY KEY COMMENT '理赔单号'",
            "outbound_id   VARCHAR(32)  COMMENT '关联出库单号'",
            "claim_type_id VARCHAR(20)  COMMENT '异常类型ID'",
            "date_id       INT          COMMENT '报案日期'",
            "claim_amount  DECIMAL(10,2) COMMENT '理赔金额'",
            "claim_status  VARCHAR(16)  COMMENT '处理状态：已赔付/审核中/已驳回'",
            "resolve_hours INT          COMMENT '报案到结案的小时数'",
        ],
        [
            Col("claim_id", PK, "理赔单号", ["理赔单号", "异常单号"], False),
            Col("outbound_id", FK, "关联出库单号", ["出库单号"], False),
            Col("claim_type_id", FK, "异常类型ID", ["异常类型ID"], False),
            Col("date_id", FK, "报案日期，关联 dim_date", ["报案日期"], False),
            Col("claim_amount", MEASURE, "理赔金额，负向指标，常规出库统计不扣减", ["理赔额", "赔付金额"], False),
            Col("claim_status", DIM, "处理状态，取值为已赔付、审核中、已驳回", ["理赔状态"], True),
            Col("resolve_hours", MEASURE, "报案到结案的小时数", ["理赔处理时长"], False),
        ],
    ),
    T(
        "fact_return", ROLE_FACT,
        "退货入库事实表，记录被拒收或退回的货物回到仓库的入库记录。"
        "这是**逆向物流**数据，与正向出库方向相反；统计出库量时绝不能把退货算进去。",
        [
            "return_id       INT PRIMARY KEY COMMENT '退货入库自增ID'",
            "outbound_id     VARCHAR(32)  COMMENT '原出库单号'",
            "warehouse_id    VARCHAR(20)  COMMENT '退回仓库ID'",
            "date_id         INT          COMMENT '退回日期'",
            "return_quantity INT          COMMENT '退回件数'",
            "return_reason   VARCHAR(32)  COMMENT '退货原因'",
            "is_reshelved    TINYINT      COMMENT '是否已二次上架，1=是'",
        ],
        [
            Col("return_id", PK, "退货入库自增ID", ["退货ID"], False),
            Col("outbound_id", FK, "原出库单号", ["出库单号"], False),
            Col("warehouse_id", FK, "退回仓库ID", ["仓库ID"], False),
            Col("date_id", FK, "退回日期，关联 dim_date", ["退货日期"], False),
            Col("return_quantity", MEASURE, "退回件数，逆向数据，不能计入出库件数", ["退货件数"], False),
            Col("return_reason", DIM, "退货原因，如拒收、地址错误、客户取消、质量问题", ["退货原因"], True),
            Col("is_reshelved", MEASURE, "是否已二次上架标记，1 表示已重新上架可售", ["二次上架"], False),
        ],
    ),

    # ---------------------------------------------- 干线与结算
    T(
        "fact_transport", ROLE_FACT,
        "干线运输事实表，按「线路 + 车次 + 日期」记录仓库之间的干线发运情况。"
        "这是**仓间调拨**层面的数据，与面向客户的出库单不在同一粒度，"
        "统计出库单量或妥投率不使用本表。",
        [
            "transport_id   INT PRIMARY KEY COMMENT '车次自增ID'",
            "line_id        VARCHAR(20)  COMMENT '干线线路ID'",
            "date_id        INT          COMMENT '发运日期'",
            "vehicle_no     VARCHAR(20)  COMMENT '车牌号'",
            "shipment_count INT          COMMENT '本车搭载的出库单量'",
            "volume_m3      DECIMAL(10,2) COMMENT '发运体积（立方米）'",
            "is_ontime      TINYINT      COMMENT '是否准点到达，1=是'",
        ],
        [
            Col("transport_id", PK, "车次自增ID", ["车次ID"], False),
            Col("line_id", FK, "干线线路ID", ["线路ID"], False),
            Col("date_id", FK, "发运日期，关联 dim_date", ["发运日期"], False),
            Col("vehicle_no", DIM, "车牌号", ["车牌"], False),
            Col("shipment_count", MEASURE, "本车搭载的出库单量，是干线口径不是客户口径", ["发运单量"], False),
            Col("volume_m3", MEASURE, "发运体积（立方米）", ["发运体积"], False),
            Col("is_ontime", MEASURE, "是否准点到达标记，1 表示准点", ["干线准点"], False),
        ],
    ),
    T(
        "fact_freight_settle", ROLE_FACT,
        "运费结算事实表，记录与承运商按单结算的运费结果，含应付、增值服务费与实付三个金额。"
        "**金额口径说明**：fact_outbound.freight_amount 是对客户应收的运费，"
        "本表的 payable_freight 是对承运商应付的成本，两者是收入与成本的区别，绝不可混用；"
        "paid_freight 是扣除争议与理赔后的实付金额，通常小于 payable_freight。",
        [
            "settle_id      INT PRIMARY KEY COMMENT '结算自增ID'",
            "outbound_id    VARCHAR(32)  COMMENT '出库单号'",
            "carrier_id     VARCHAR(20)  COMMENT '承运商ID'",
            "payable_freight DECIMAL(10,2) COMMENT '应付运费'",
            "extra_fee      DECIMAL(10,2) COMMENT '增值服务费（上楼、保价、超区等）'",
            "paid_freight   DECIMAL(10,2) COMMENT '实付运费，扣除争议与理赔后实际支付'",
            "settle_status  VARCHAR(16)  COMMENT '结算状态：已结算/对账中/有争议'",
            "date_id        INT          COMMENT '结算日期'",
        ],
        [
            Col("settle_id", PK, "结算自增ID", ["结算ID"], False),
            Col("outbound_id", FK, "出库单号", ["出库单号"], False),
            Col("carrier_id", FK, "承运商ID", ["承运商ID"], False),
            Col("payable_freight", MEASURE, "应付运费（对承运商的成本），与出库单上的应收运费是相反口径", ["应付运费"], False),
            Col("extra_fee", MEASURE, "增值服务费，含上楼费、保价费、超区派送费", ["增值服务费", "附加费"], False),
            Col("paid_freight", MEASURE, "实付运费，扣除争议与理赔后实际支付的金额，通常小于应付运费", ["实付运费"], False),
            Col("settle_status", DIM, "结算状态，取值为已结算、对账中、有争议", ["结算状态"], True),
            Col("date_id", FK, "结算日期，关联 dim_date", ["结算日期"], False),
        ],
    ),
    T(
        "fact_warehouse_cost", ROLE_FACT,
        "仓成本事实表，按「仓库 + 月份」记录仓储运营发生的各项成本。"
        "这是**成本**数据，与出库货值（托管货物价值）是完全不同的两个口径，不可混用；"
        "问「出库货值」「出库单量」时不要使用本表。",
        [
            "cost_id       INT PRIMARY KEY COMMENT '成本记录自增ID'",
            "warehouse_id  VARCHAR(20)  COMMENT '仓库ID'",
            "date_id       INT          COMMENT '成本所属月份（取月末日期）'",
            "storage_fee   DECIMAL(12,2) COMMENT '仓储费（库租）'",
            "operation_fee DECIMAL(12,2) COMMENT '操作费（出入库作业）'",
            "labor_cost    DECIMAL(12,2) COMMENT '人力成本'",
            "energy_cost   DECIMAL(12,2) COMMENT '能耗成本'",
        ],
        [
            Col("cost_id", PK, "成本记录自增ID", ["成本ID"], False),
            Col("warehouse_id", FK, "仓库ID", ["仓库ID"], False),
            Col("date_id", FK, "成本所属月份，关联 dim_date", ["成本月份"], False),
            Col("storage_fee", MEASURE, "仓储费（库租），按月发生", ["仓储费", "库租"], False),
            Col("operation_fee", MEASURE, "操作费，含出入库作业产生的费用", ["操作费"], False),
            Col("labor_cost", MEASURE, "人力成本", ["人工成本"], False),
            Col("energy_cost", MEASURE, "能耗成本，含制冷与照明用电", ["能耗成本"], False),
        ],
    ),

    # ---------------------------------------------- 维度表
    T(
        "dim_consignee", ROLE_DIM,
        "收货人维度表，描述包裹的最终收件方，用于分析末端派送的收件特征。",
        [
            "consignee_id   VARCHAR(20) PRIMARY KEY COMMENT '收货人ID'",
            "consignee_name VARCHAR(32) COMMENT '收货人姓名'",
            "phone_masked   VARCHAR(20) COMMENT '脱敏手机号'",
            "dest_id        VARCHAR(20) COMMENT '目的地区域ID'",
            "consignee_type VARCHAR(16) COMMENT '收货人类型：个人/企业/驿站'",
        ],
        [
            Col("consignee_id", PK, "收货人ID", ["收货人ID"], False),
            Col("consignee_name", DIM, "收货人姓名", ["收货人", "收件人"], True),
            Col("phone_masked", DIM, "脱敏后的手机号", ["手机号"], False),
            Col("dest_id", FK, "目的地区域ID", ["目的地ID"], False),
            Col("consignee_type", DIM, "收货人类型，取值为个人、企业、驿站", ["收货人类型"], True),
        ],
    ),
    T(
        "dim_destination", ROLE_DIM,
        "目的地区域维度表，描述包裹送达方向所在的省市区，以及是否属于偏远地区。"
        "注意与 dim_warehouse 的区别：本表描述**货送到哪里**，dim_warehouse 描述**货从哪个仓发出**。",
        [
            "dest_id     VARCHAR(20) PRIMARY KEY COMMENT '目的地区域ID'",
            "province    VARCHAR(32) COMMENT '省份'",
            "region_name VARCHAR(20) COMMENT '所属大区'",
            "city        VARCHAR(32) COMMENT '城市'",
            "is_remote   TINYINT     COMMENT '是否偏远地区，1=是'",
        ],
        [
            Col("dest_id", PK, "目的地区域ID", ["目的地ID"], False),
            Col("province", DIM, "收货省份", ["省份", "目的省份"], True),
            Col("region_name", DIM, "收货所在大区", ["目的大区", "大区"], True),
            Col("city", DIM, "收货城市", ["目的城市", "城市"], True),
            Col("is_remote", DIM, "是否偏远地区标记，1 表示偏远，偏远地区时效更长", ["偏远地区"], True),
        ],
    ),
    T(
        "dim_transport_line", ROLE_DIM,
        "干线线路维度表，描述仓库之间的固定运输线路及其里程与承诺时效。",
        [
            "line_id            VARCHAR(20) PRIMARY KEY COMMENT '线路ID'",
            "origin_warehouse_id VARCHAR(20) COMMENT '起点仓ID'",
            "dest_city          VARCHAR(32) COMMENT '终点城市'",
            "distance_km        INT         COMMENT '线路里程（公里）'",
            "promise_hours      INT         COMMENT '承诺时效（小时）'",
        ],
        [
            Col("line_id", PK, "干线线路ID", ["线路ID"], False),
            Col("origin_warehouse_id", FK, "起点仓ID", ["起点仓"], False),
            Col("dest_city", DIM, "终点城市", ["终点", "到达城市"], True),
            Col("distance_km", MEASURE, "线路里程（公里）", ["里程", "距离"], False),
            Col("promise_hours", MEASURE, "该线路的承诺时效（小时）", ["线路时效"], False),
        ],
    ),
    T(
        "dim_service_level", ROLE_DIM,
        "时效产品维度表，描述公司对外承诺的时效档次及其对应小时数。"
        "注意与 dim_carrier.service_level 的区别：本表是可售卖的**产品档位**，"
        "承运商表上的 service_level 只是该承运商的主打档位，统计应按时效产品走本表。",
        [
            "level_id       VARCHAR(20) PRIMARY KEY COMMENT '时效产品ID'",
            "level_name     VARCHAR(20) COMMENT '时效产品名称'",
            "promise_hours  INT         COMMENT '承诺时效（小时）'",
            "is_cold_chain  TINYINT     COMMENT '是否冷链产品，1=是'",
        ],
        [
            Col("level_id", PK, "时效产品ID", ["时效ID"], False),
            Col("level_name", DIM, "时效产品名称，取值为次日达、隔日达、标准陆运、经济", ["时效产品", "时效档位"], True),
            Col("promise_hours", MEASURE, "该时效产品的承诺小时数", ["承诺时效"], False),
            Col("is_cold_chain", DIM, "是否冷链产品标记，1 表示冷链", ["冷链"], True),
        ],
    ),
    T(
        "dim_claim_type", ROLE_DIM,
        "异常类型维度表，描述理赔与异常件的分类及其赔付规则。",
        [
            "claim_type_id   VARCHAR(20) PRIMARY KEY COMMENT '异常类型ID'",
            "claim_type_name VARCHAR(20) COMMENT '异常类型名称'",
            "compensate_rule VARCHAR(64) COMMENT '赔付规则说明'",
        ],
        [
            Col("claim_type_id", PK, "异常类型ID", ["异常类型ID"], False),
            Col("claim_type_name", DIM, "异常类型名称，取值为破损、丢失、延误、错发、短少", ["异常类型", "理赔类型"], True),
            Col("compensate_rule", DIM, "该类型的赔付规则说明", ["赔付规则"], False),
        ],
    ),
    T(
        "dim_cargo_sku", ROLE_DIM,
        "货品规格（SKU）维度表，描述同一个货品下的不同规格包装变体。"
        "**粒度是货品之下的规格**，比 dim_cargo 更细：统计品类或品牌口径时不要 JOIN 本表，"
        "否则会把同一货品重复计数。",
        [
            "sku_id       VARCHAR(20) PRIMARY KEY COMMENT 'SKU唯一标识'",
            "cargo_id     VARCHAR(20) COMMENT '所属货品ID'",
            "sku_name     VARCHAR(64) COMMENT 'SKU名称'",
            "spec         VARCHAR(32) COMMENT '规格描述'",
            "package_unit VARCHAR(16) COMMENT '包装单位：箱/件/托/袋'",
        ],
        [
            Col("sku_id", PK, "SKU唯一标识", ["SKU", "规格ID"], False),
            Col("cargo_id", FK, "所属货品ID", ["货品ID"], False),
            Col("sku_name", DIM, "SKU名称", ["SKU名称"], True),
            Col("spec", DIM, "规格描述，如标准装、家庭装、大容量", ["规格", "型号"], True),
            Col("package_unit", DIM, "包装单位，取值为箱、件、托、袋", ["包装单位"], True),
        ],
    ),
    T(
        "dim_cargo_brand", ROLE_DIM,
        "品牌维度表，描述货品品牌的产地与档次。与 dim_cargo 上的 cargo_brand 字段是同一语义的"
        "**两个存放位置**：按品牌分组统计用哪边都行，但不要两边都 JOIN，会导致品牌重复。",
        [
            "brand_id   VARCHAR(20) PRIMARY KEY COMMENT '品牌ID'",
            "brand_name VARCHAR(32) COMMENT '品牌名称'",
            "origin     VARCHAR(32) COMMENT '品牌所属国家或地区'",
            "tier       VARCHAR(16) COMMENT '品牌档次：高端/大众'",
        ],
        [
            Col("brand_id", PK, "品牌ID", ["品牌ID"], False),
            Col("brand_name", DIM, "品牌名称，如华为、美的、耐克", ["品牌", "牌子"], True),
            Col("origin", DIM, "品牌所属国家或地区", ["品牌产地", "国别"], True),
            Col("tier", DIM, "品牌档次，取值为高端、大众", ["档次"], True),
        ],
    ),
    T(
        "dim_staff", ROLE_DIM,
        "仓操作员维度表，描述仓库内作业人员的岗位与所属仓库，用于分析人效。",
        [
            "staff_id     VARCHAR(20) PRIMARY KEY COMMENT '员工ID'",
            "staff_name   VARCHAR(32) COMMENT '员工姓名'",
            "position     VARCHAR(20) COMMENT '岗位：拣货员/叉车工/复核员/打包员'",
            "warehouse_id VARCHAR(20) COMMENT '所属仓库ID'",
            "hire_date    INT         COMMENT '入职日期'",
        ],
        [
            Col("staff_id", PK, "员工ID", ["员工ID"], False),
            Col("staff_name", DIM, "员工姓名", ["员工", "操作员"], True),
            Col("position", DIM, "岗位，取值为拣货员、叉车工、复核员、打包员", ["岗位", "职位"], True),
            Col("warehouse_id", FK, "所属仓库ID", ["仓库ID"], False),
            Col("hire_date", FK, "入职日期，关联 dim_date", ["入职日期"], False),
        ],
    ),
    T(
        "dim_shift", ROLE_DIM,
        "班次维度表，描述仓库的作业班次及其起止时段。",
        [
            "shift_id   VARCHAR(20) PRIMARY KEY COMMENT '班次ID'",
            "shift_name VARCHAR(20) COMMENT '班次名称'",
            "start_hour INT         COMMENT '班次开始小时'",
            "end_hour   INT         COMMENT '班次结束小时'",
        ],
        [
            Col("shift_id", PK, "班次ID", ["班次ID"], False),
            Col("shift_name", DIM, "班次名称，取值为早班、晚班、夜班", ["班次"], True),
            Col("start_hour", DIM, "班次开始的小时", ["开始时间"], False),
            Col("end_hour", DIM, "班次结束的小时", ["结束时间"], False),
        ],
    ),
    T(
        "dim_zone", ROLE_DIM,
        "库区维度表，描述仓库内部的物理分区及其温层与用途。",
        [
            "zone_id          VARCHAR(20) PRIMARY KEY COMMENT '库区ID'",
            "zone_name        VARCHAR(32) COMMENT '库区名称'",
            "temperature_zone VARCHAR(16) COMMENT '温层：常温/冷藏/冷冻'",
            "usage_type       VARCHAR(16) COMMENT '用途：存储区/拣货区/复核区/退货区'",
        ],
        [
            Col("zone_id", PK, "库区ID", ["库区ID"], False),
            Col("zone_name", DIM, "库区名称，如A区、B区、冷藏区、次品区", ["库区"], True),
            Col("temperature_zone", DIM, "温层，取值为常温、冷藏、冷冻", ["温层", "温度区"], True),
            Col("usage_type", DIM, "库区用途，取值为存储区、拣货区、复核区、退货区", ["库区用途"], True),
        ],
    ),
    T(
        "dim_equipment", ROLE_DIM,
        "仓储装备维度表，描述仓内叉车、AGV、分拣线等装备的型号与额定参数。"
        "这是**装备资产**数据，与出库单据没有直接关联，不能用来统计出库业务量。",
        [
            "equipment_id   VARCHAR(20) PRIMARY KEY COMMENT '装备ID'",
            "equipment_name VARCHAR(32) COMMENT '装备名称'",
            "equipment_type VARCHAR(20) COMMENT '装备类型'",
            "warehouse_id   VARCHAR(20) COMMENT '所在仓库ID'",
            "rated_load     DECIMAL(8,2) COMMENT '额定载荷（吨）'",
        ],
        [
            Col("equipment_id", PK, "装备ID", ["设备ID"], False),
            Col("equipment_name", DIM, "装备名称，如内燃叉车、电动叉车、AGV、分拣线、缠绕机、打包台", ["装备名称", "设备名称"], True),
            Col("equipment_type", DIM, "装备类型，取值为搬运设备、分拣设备、包装设备、存储设备", ["装备类型", "设备类型"], True),
            Col("warehouse_id", FK, "所在仓库ID", ["仓库ID"], False),
            Col("rated_load", MEASURE, "额定载荷（吨），装备固有参数不是业务指标", ["额定载荷", "载荷"], False),
        ],
    ),
    T(
        "dim_supplier", ROLE_DIM,
        "供应商维度表，描述向仓库送货的供应商及其合作等级，用于分析入库来源。",
        [
            "supplier_id   VARCHAR(20) PRIMARY KEY COMMENT '供应商ID'",
            "supplier_name VARCHAR(64) COMMENT '供应商名称'",
            "coop_level    VARCHAR(16) COMMENT '合作等级：战略/核心/普通'",
            "city          VARCHAR(32) COMMENT '所在城市'",
        ],
        [
            Col("supplier_id", PK, "供应商ID", ["供应商ID"], False),
            Col("supplier_name", DIM, "供应商名称", ["供应商"], True),
            Col("coop_level", DIM, "合作等级，取值为战略、核心、普通", ["合作等级"], True),
            Col("city", DIM, "供应商所在城市", ["供应商城市"], True),
        ],
    ),
]

# ------------------------------------------------------------ 维度枚举取值

CLAIM_TYPES = [
    ("CT01", "破损", "按实际损失赔付，最高不超过保价金额"),
    ("CT02", "丢失", "按货值全额赔付，需提供发货凭证"),
    ("CT03", "延误", "按运费的三倍赔付"),
    ("CT04", "错发", "免费补发并承担退回运费"),
    ("CT05", "短少", "按短少数量占该出库单比例赔付"),
]
SERVICE_LEVELS = [
    ("SL01", "次日达", 24, 0),
    ("SL02", "隔日达", 48, 0),
    ("SL03", "标准陆运", 72, 0),
    ("SL04", "经济", 120, 0),
    ("SL05", "冷链次日达", 24, 1),
]
SHIFTS = [("SH01", "早班", 8, 16), ("SH02", "晚班", 16, 24), ("SH03", "夜班", 0, 8)]
ZONES = [
    ("Z01", "A区", "常温", "存储区"),
    ("Z02", "B区", "常温", "拣货区"),
    ("Z03", "冷藏区", "冷藏", "存储区"),
    ("Z04", "冷冻区", "冷冻", "存储区"),
    ("Z05", "复核区", "常温", "复核区"),
    ("Z06", "退货区", "常温", "退货区"),
]
EQUIPMENTS = [
    ("EQ01", "内燃叉车 CPCD30", "搬运设备", "W01", 3.0),
    ("EQ02", "电动叉车 CPD15", "搬运设备", "W02", 1.5),
    ("EQ03", "AGV 搬运机器人 A200", "搬运设备", "W05", 0.2),
    ("EQ04", "交叉带分拣线 S300", "分拣设备", "W01", 0.05),
    ("EQ05", "缠绕机 WR-100", "包装设备", "W07", 1.0),
    ("EQ06", "打包台 PT-08", "包装设备", "W08", 0.1),
    ("EQ07", "电动堆高车 CDD12", "搬运设备", "W09", 1.2),
    ("EQ08", "冷链穿梭车 CS-50", "存储设备", "W04", 0.5),
]
SUPPLIERS = [
    ("SU01", "华为终端供应链", "战略", "深圳"),
    ("SU02", "美的集团制造中心", "核心", "佛山"),
    ("SU03", "内蒙古乳业股份", "核心", "呼和浩特"),
    ("SU04", "浙江服装制造厂", "普通", "杭州"),
    ("SU05", "山东生鲜冷链基地", "核心", "青岛"),
    ("SU06", "广东家居制造", "普通", "东莞"),
]
DESTINATIONS = [
    ("D01", "广东省", "华南", "广州", 0),
    ("D02", "浙江省", "华东", "杭州", 0),
    ("D03", "江苏省", "华东", "南京", 0),
    ("D04", "北京市", "华北", "北京", 0),
    ("D05", "上海市", "华东", "上海", 0),
    ("D06", "四川省", "西南", "成都", 0),
    ("D07", "湖北省", "华中", "武汉", 0),
    ("D08", "新疆维吾尔自治区", "西北", "乌鲁木齐", 1),
    ("D09", "西藏自治区", "西南", "拉萨", 1),
    ("D10", "内蒙古自治区", "华北", "呼和浩特", 1),
]
TRANSPORT_LINES = [
    ("L01", "W01", "上海", 1213, 18),
    ("L02", "W01", "广州", 2128, 28),
    ("L03", "W05", "北京", 1213, 18),
    ("L04", "W05", "成都", 1960, 26),
    ("L05", "W07", "武汉", 1069, 16),
    ("L06", "W07", "杭州", 1290, 19),
    ("L07", "W09", "广州", 1069, 16),
    ("L08", "W10", "西安", 685, 12),
]
POSITIONS = ["拣货员", "叉车工", "复核员", "打包员"]
CLAIM_STATUS = ["已赔付", "审核中", "已驳回"]
DELIVERY_STATUS = ["已签收", "在途", "拒收", "派送失败"]
SIGNOFF_TYPES = ["本人签收", "他人代收", "驿站代收", "智能柜"]
RETURN_REASONS = ["客户拒收", "地址错误", "客户取消", "质量问题", "超时未取"]
SETTLE_STATUS = ["已结算", "对账中", "有争议"]
SKU_SPECS = ["标准装", "家庭装", "大容量", "礼盒装", "单件装"]
PACKAGE_UNITS = ["箱", "件", "托", "袋"]

# dws_outbound_daily 刻意只回溯到 2025-09 —— 汇总表不得不用不通，必须回到 fact_outbound
DWS_COVER_END = 20250930


# ------------------------------------------------------------ 新增业务指标
#
# 小 schema 里只有 3 个指标，metric 召回形同虚设 —— 指标库就这么大，
# 「召不召回」没有区别。真实企业指标卡通常有几十上百个，且互相之间口径接近。
# 因此大 schema 追加下面这批指标，其中「单量」与「运费」两族刻意设置歧义：
# 出库单量 / 入库单量 方向相反，应收运费 / 实付运费 口径不同，选错答案就错。

METRICS: list[dict] = [
    dict(
        name="INBOUND_COUNT",
        desc="入库单量，指供应商送货入库的单据笔数，方向是「货进仓」。"
             "与出库单量是相反的两个业务动作，问「出库了多少单」时不能用本指标。",
        alias=["入库单量", "入库量", "收货单量"],
        cols=["fact_inbound.inbound_id"],
    ),
    dict(
        name="SIGN_RATE",
        desc="妥投率（签收率），指最终成功签收的出库单占比，"
             "等于 SUM(is_delivered) / COUNT(*)。"
             "注意它只回答「送到没有」，不回答「按时送到没有」——"
             "问「及时率」「准时率」应改用 DELIVERY_ONTIME_RATE。",
        alias=["妥投率", "签收率", "送达率"],
        cols=["fact_outbound.is_delivered"],
    ),
    dict(
        name="CLAIM_AMOUNT",
        desc="理赔金额，指因破损、丢失、延误等异常而实际赔付的金额总和，是负向指标。"
             "只有在问题明确提到「理赔」「赔付」时才使用；常规的出库货值、出库单量统计"
             "不扣减理赔，也不应 JOIN 理赔表。",
        alias=["理赔额", "赔付金额", "理赔金额"],
        cols=["fact_claim.claim_amount"],
    ),
    dict(
        name="PAID_FREIGHT",
        desc="实付运费，指与承运商结算后扣除争议与理赔实际支付的运费，通常小于应付运费。"
             "与出库单上的 freight_amount（对客户应收运费）是成本与收入两个方向，绝不可混用；"
             "泛泛地问「运费多少」默认指出库单上的应收运费。",
        alias=["实付运费", "结算运费"],
        cols=["fact_freight_settle.paid_freight"],
    ),
    dict(
        name="PICKING_EFFICIENCY",
        desc="拣货效率，指单位时间完成的拣货行数，等于拣货行数除以拣货耗时（换算为小时）。"
             "这是仓内作业效率指标，与出库业务量无关；问「出库了多少」时不使用。",
        alias=["拣货效率", "人效", "拣货人效"],
        cols=["fact_picking.pick_lines", "fact_picking.pick_minutes"],
    ),
]


# ---------------------------------------------------------------- 数据行生成


def build_rows(outbound_rows, shippers: list[tuple], cargos: list[tuple],
               warehouses: list[tuple], seed: int = 20260601):
    """基于已有出库单派生各新增表的数据行。返回 {表名: [行元组]}。"""
    rng = random.Random(seed)
    data: dict[str, list[tuple]] = {t.name: [] for t in TABLES}

    cargo_category = {c[0]: c[1] for c in cargos}
    wh_ids = [w[0] for w in warehouses]

    # ---- 维表：直接枚举
    data["dim_claim_type"] = list(CLAIM_TYPES)
    data["dim_service_level"] = list(SERVICE_LEVELS)
    data["dim_shift"] = list(SHIFTS)
    data["dim_zone"] = list(ZONES)
    data["dim_equipment"] = list(EQUIPMENTS)
    data["dim_supplier"] = list(SUPPLIERS)
    data["dim_destination"] = list(DESTINATIONS)
    data["dim_transport_line"] = list(TRANSPORT_LINES)

    cargo_ids = sorted({r[2] for r in outbound_rows})
    shipper_ids = sorted({r[1] for r in outbound_rows})

    # dim_cargo_sku：每个货品 2-4 个规格变体
    sku_seq = 0
    for gid in cargo_ids:
        for spec in rng.sample(SKU_SPECS, k=rng.randint(2, 4)):
            sku_seq += 1
            data["dim_cargo_sku"].append(
                (f"SKU{sku_seq:04d}", gid, f"{gid}-{spec}", spec, rng.choice(PACKAGE_UNITS))
            )

    # dim_cargo_brand：从货品档案里汇总出品牌，制造与 dim_cargo 的重复存放
    brands = sorted({c[3] for c in cargos})
    data["dim_cargo_brand"] = [
        (f"BR{i:03d}", name, rng.choice(["中国", "美国", "德国", "日本", "法国"]),
         "高端" if name in ("苹果", "华为", "戴森", "兰蔻", "欧莱雅") else "大众")
        for i, name in enumerate(brands, start=1)
    ]

    # dim_staff：每个仓 3-5 名操作员
    staff_seq = 0
    staff_ids: list[str] = []
    for wid in wh_ids:
        for _ in range(rng.randint(3, 5)):
            staff_seq += 1
            sid = f"ST{staff_seq:03d}"
            staff_ids.append(sid)
            data["dim_staff"].append(
                (sid, f"操作员{staff_seq:03d}", rng.choice(POSITIONS), wid, 20230101 + staff_seq)
            )

    # dim_consignee：每个目的地 2-3 个收货人
    consignee_seq = 0
    for dest in DESTINATIONS:
        for _ in range(rng.randint(2, 3)):
            consignee_seq += 1
            data["dim_consignee"].append(
                (f"CN{consignee_seq:04d}", f"收件人{consignee_seq:04d}",
                 f"138****{consignee_seq:04d}", dest[0], rng.choice(["个人", "企业", "驿站"]))
            )

    # ---- 事实表：逐笔出库单派生
    item_id = delivery_id = signoff_id = claim_seq = 0
    return_id = picking_id = settle_id = raw_seq = 0

    for (ob_id, shipper_id, cargo_id, date_id, wh_id, carrier_id,
         qty, amount, ob_hours, _is_ob, dlv_hours, is_dlv, _is_ot, freight) in outbound_rows:
        # ods_waybill_raw 只同步部分原始单，且故意掺入测试单与异常货值
        if rng.random() < 0.32:
            raw_seq += 1
            is_test = 1 if rng.random() < 0.15 else 0
            raw_amt = amount
            if is_test:
                raw_amt = round(rng.uniform(0.01, 9.9), 2)
            data["ods_waybill_raw"].append(
                (raw_seq, ob_id, cargo_id, shipper_id, _d2s(date_id), raw_amt,
                 round(qty * rng.uniform(0.5, 3.0), 2), is_test, _d2s(date_id) + " 03:15:00")
            )

        # 出库明细：多数 1 行，部分拆多行
        lines = rng.randint(1, 2) if amount < 5000 else 1
        remain = amount
        for i in range(lines):
            item_id += 1
            if i == lines - 1:
                line_amt = round(remain, 2)
            else:
                line_amt = round(remain * rng.uniform(0.3, 0.6), 2)
                remain = round(remain - line_amt, 2)
            if line_amt <= 0:
                continue
            data["fact_outbound_item"].append(
                (item_id, ob_id, cargo_id, max(1, int(qty / lines)), line_amt,
                 round(rng.uniform(0.80, 1.00), 4))
            )

        # 派送：记录派送过程，少数单需要二次派送
        attempts = 1 if rng.random() < 0.88 else rng.randint(2, 3)
        for att in range(1, attempts + 1):
            delivery_id += 1
            status = "已签收" if (is_dlv and att == attempts) else rng.choice(
                ["派送失败", "拒收", "在途"])
            data["fact_delivery"].append(
                (delivery_id, ob_id, carrier_id, f"派送员{rng.randint(1, 40):03d}",
                 rng.randint(1, 8), status, att)
            )

        # 签收：只有已妥投的才有签收记录（本表天然不含未妥投单）
        if is_dlv:
            signoff_id += 1
            data["fact_signoff"].append(
                (signoff_id, ob_id, date_id + int(dlv_hours // 24) if dlv_hours else date_id,
                 rng.choice(SIGNOFF_TYPES), rng.choice([1, 1, 1, 0]),
                 1 if rng.random() < 0.05 else 0)
            )

        # 理赔：约 5%，异常件才产生
        if rng.random() < 0.05:
            claim_seq += 1
            ctype = rng.choice(CLAIM_TYPES)[0]
            data["fact_claim"].append(
                (f"CLM{claim_seq:06d}", ob_id, ctype, date_id,
                 round(amount * rng.uniform(0.05, 0.6), 2),
                 rng.choice(CLAIM_STATUS), rng.randint(12, 240))
            )

        # 退货入库：约 3%
        if rng.random() < 0.03:
            return_id += 1
            data["fact_return"].append(
                (return_id, ob_id, wh_id, date_id + rng.randint(2, 10),
                 max(1, int(qty * rng.uniform(0.3, 1.0))),
                 rng.choice(RETURN_REASONS), rng.choice([1, 1, 0]))
            )

        # 拣货作业：约 60% 有拣货记录（部分由 AGV 自动完成不入表）
        if rng.random() < 0.60:
            picking_id += 1
            lines_cnt = rng.randint(1, 8)
            data["fact_picking"].append(
                (picking_id, ob_id, rng.choice(staff_ids), rng.choice(SHIFTS)[0],
                 lines_cnt, rng.randint(5, 90), rng.choice([0, 0, 0, 1]))
            )

        # 运费结算：对承运商的应付 / 实付，与出库单上的应收运费是两个方向
        settle_id += 1
        payable = round(freight * rng.uniform(0.55, 0.80), 2)
        extra = round(rng.choice([0.0, 0.0, 5.0, 12.0, 20.0]), 2)
        status = rng.choice(SETTLE_STATUS)
        paid = round(payable + extra, 2) if status == "已结算" else 0.0
        data["fact_freight_settle"].append(
            (settle_id, ob_id, carrier_id, payable, extra, paid, status, date_id)
        )

    # 入库：每天每个仓若干笔，与出库是相反方向
    inbound_id = 0
    for did in sorted({r[3] for r in outbound_rows})[::2]:
        for _ in range(rng.randint(1, 4)):
            inbound_id += 1
            data["fact_inbound"].append(
                (inbound_id, rng.choice(SUPPLIERS)[0], rng.choice(wh_ids),
                 rng.choice(cargo_ids), did, rng.randint(20, 600),
                 rng.randint(2, 48), rng.choice([1, 1, 1, 0]))
            )

    # 库存快照：每周一次，抽样 SKU
    sku_ids = [r[0] for r in data["dim_cargo_sku"]]
    for did in sorted({r[3] for r in outbound_rows})[::7]:
        for sid in rng.sample(sku_ids, k=min(len(sku_ids), 18)):
            q = rng.randint(0, 500)
            cargo_of = next((s[1] for s in data["dim_cargo_sku"] if s[0] == sid), None)
            data["fact_inventory_daily"].append(
                (len(data["fact_inventory_daily"]) + 1, cargo_of, rng.choice(wh_ids), did,
                 q, round(q * rng.uniform(10, 800), 2), rng.randint(1, 90))
            )

    # 仓成本：每月每仓一条
    months = sorted({(r[3] // 100) * 100 + 28 for r in outbound_rows})
    for did in months:
        for wid in wh_ids:
            data["fact_warehouse_cost"].append(
                (len(data["fact_warehouse_cost"]) + 1, wid, did,
                 round(rng.uniform(30000, 180000), 2), round(rng.uniform(10000, 60000), 2),
                 round(rng.uniform(50000, 220000), 2), round(rng.uniform(5000, 40000), 2))
            )

    # 装备作业：每台装备每月抽样若干天
    for eq in EQUIPMENTS:
        for did in rng.sample(sorted({r[3] for r in outbound_rows}), k=30):
            data["fact_equipment_op"].append(
                (len(data["fact_equipment_op"]) + 1, eq[0], eq[3], did,
                 round(rng.uniform(2, 16), 2), round(rng.uniform(5, 120), 2),
                 rng.choice([0, 0, 0, 1]))
            )

    # 干线运输：每条线路抽样若干天发运
    for line in TRANSPORT_LINES:
        for did in rng.sample(sorted({r[3] for r in outbound_rows}), k=40):
            cnt = rng.randint(30, 400)
            data["fact_transport"].append(
                (len(data["fact_transport"]) + 1, line[0], did,
                 f"京A{rng.randint(10000, 99999)}", cnt,
                 round(cnt * rng.uniform(0.02, 0.15), 2), rng.choice([1, 1, 1, 0]))
            )

    # dws_outbound_daily：按「日期 + 仓库 + 品类」预聚合，但只回溯到 DWS_COVER_END
    agg: dict[tuple, list[float]] = {}
    for (_ob, _sid, cid, did, wid, _cr, q, amt, *_rest) in outbound_rows:
        if did > DWS_COVER_END:
            continue
        key = (did, wid, cargo_category.get(cid, "未知"))
        cur = agg.setdefault(key, [0.0, 0, 0])
        cur[0] += amt
        cur[1] += 1
        cur[2] += q
    for (did, wid, cat), (amt, cnt, q) in sorted(agg.items()):
        data["dws_outbound_daily"].append((did, wid, cat, round(amt, 2), cnt, q))

    return data


def _d2s(date_id: int) -> str:
    """20250101 -> '2025-01-01'"""
    s = str(date_id)
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def load_base_context(seed: int, y0: int, y1: int):
    """复用 scripts/gen_dw_data.py 的业务档案与出库行，保证两套脚本口径完全一致。

    同库里的 fact_outbound / dim_date 由 dw_expand.sql 写入，这里按同样的随机种子
    重新推导一次，使新增外键表能准确关联到既有出库单。
    """
    import importlib.util

    src = ROOT / "scripts" / "gen_dw_data.py"
    spec = importlib.util.spec_from_file_location("gen_dw_data", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # 该模块顶层只有常量与函数定义，不会执行写入

    _, outbound_rows = mod.build_rows(seed, y0, y1)
    return mod.SHIPPERS, mod.CARGOS, mod.WAREHOUSES, outbound_rows


# -------------------------------------------------------------------- 渲染


def validate(data: dict[str, list[tuple]]) -> list[str]:
    """生成器自检：主键唯一性 + 行宽与字段定义一致。

    主键冲突在导入 MySQL 时才报 Duplicate entry，会让整份 SQL 半途而废
    （已经写入的表没法回滚），在生成阶段拦下代价小得多。
    """
    errors: list[str] = []
    for t in TABLES:
        rows = data.get(t.name, [])
        if not rows:
            continue
        names = [c.name for c in t.cols]
        width = len(names)
        for i, row in enumerate(rows):
            if len(row) != width:
                errors.append(f"{t.name} 第 {i+1} 行宽度 {len(row)} != 字段数 {width}")

        pk = t.primary_keys()
        if not pk:
            errors.append(f"{t.name} 没有声明任何主键列")
            continue
        idx = [names.index(p) for p in pk]
        seen: set[tuple] = set()
        dupes: list[tuple] = []
        for row in rows:
            key = tuple(row[i] for i in idx)
            if key in seen:
                dupes.append(key)
            seen.add(key)
        if dupes:
            errors.append(
                f"{t.name} 主键 {pk} 有 {len(dupes)} 处重复，例如 {dupes[0]}"
            )
    return errors


def sql_quote(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def render_sql(data: dict[str, list[tuple]]) -> str:
    out: list[str] = []
    add = out.append
    add("-- 本文件由 scripts/gen_dw_large.py 自动生成，请勿手工编辑。")
    add("-- 用途：把 dw 从 6 张表扩张到 32 张表，用于验证 schema 规模对 NL2SQL 策略的影响。")
    add("-- 原有 6 张表（dim_date/dim_warehouse/dim_cargo/dim_shipper/dim_carrier/fact_outbound）不做任何改动，")
    add("-- 以保证既有 56 道评测题的 gold SQL 仍可执行且结果不变。")
    add("")
    add("SET NAMES utf8mb4;")
    add("USE dw;")
    add("SET FOREIGN_KEY_CHECKS = 0;")
    add("")

    for t in TABLES:
        rows = data.get(t.name, [])
        add(f"DROP TABLE IF EXISTS {t.name};")
        add(f"CREATE TABLE {t.name} (")
        add(",\n".join("  " + c for c in t.ddl_cols))
        add(") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='" + t.desc.replace("'", "")[:60] + "';")
        add("")
        if not rows:
            continue
        col_names = [c.name for c in t.cols]
        add(f"-- {t.name}：{len(rows)} 行")
        for i in range(0, len(rows), 200):
            chunk = rows[i:i + 200]
            vals = ",\n       ".join(
                "(" + ", ".join(sql_quote(v) for v in row) + ")" for row in chunk
            )
            add(f"INSERT INTO {t.name} ({', '.join(col_names)})")
            add(f"VALUES {vals};")
        add("")

    add("SET FOREIGN_KEY_CHECKS = 1;")
    return "\n".join(out)


def render_yaml_block() -> str:
    """为新增表渲染一段可直接嵌入 meta_config.yaml 的 YAML。"""
    lines: list[str] = []
    for t in TABLES:
        lines.append(f"  - name: {t.name}")
        lines.append(f"    role: {t.role}")
        lines.append(f"    description: {t.desc}")
        lines.append("    columns:")
        for c in t.cols:
            lines.append(f"      - name: {c.name}")
            lines.append(f"        role: {c.role}")
            lines.append(f"        description: {c.desc}")
            if c.alias:
                alias = ", ".join(f"'{a}'" for a in c.alias)
                lines.append(f"        alias: [ {alias} ]")
            else:
                # alias 在 ColumnConfig 里是必填项，缺省会报 MissingMandatoryValue，
                # 日期类字段确实没有自然中文别名，显式渲染成空列表
                lines.append("        alias: []")
            lines.append(f"        sync: {'true' if c.sync else 'false'}")
            lines.append("")
        lines.append("")
    return "\n".join(lines)


def render_metrics_block() -> str:
    """渲染追加到 metrics 段末尾的新指标定义。"""
    lines: list[str] = []
    for m in METRICS:
        lines.append(f"  - name: {m['name']}")
        lines.append(f"    description: {m['desc']}")
        lines.append("    relevant_columns:")
        for c in m["cols"]:
            lines.append(f"      - {c}")
        lines.append(f"    alias: [ {', '.join(m['alias'])} ]")
        lines.append("")
    return "\n".join(lines)


def build_large_meta(src: Path, dst: Path) -> int:
    """读取小 schema 的 meta_config.yaml，在 metrics 段之前插入新表定义，
    并在 metrics 段末尾追加新指标。
    """
    text = src.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    idx = next(i for i, ln in enumerate(lines) if ln.startswith("metrics:"))

    head = "".join(lines[:idx])
    tail = "".join(lines[idx:])
    banner = (
        "# ---------------------------------------------------------------------------\n"
        "# 以下 tables 由 scripts/gen_dw_large.py 追加，用于把 schema 从 6 表扩到 32 表。\n"
        "# 原 6 张表的定义保持原样不动，保证 56 道评测题的 gold SQL 不受影响。\n"
        "# ---------------------------------------------------------------------------\n"
        "\n"
    )
    body = head.rstrip() + "\n\n" + banner + render_yaml_block() + "\n" + tail.rstrip() + "\n\n"
    body += render_metrics_block()
    dst.write_text(body, encoding="utf-8")
    return len(TABLES)


# -------------------------------------------------------------------- 入口


def main() -> None:
    ap = argparse.ArgumentParser(description="生成 dw 从 6 表到 32 表的扩张脚本")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--sql-out", type=Path, default=ROOT / "docker" / "mysql" / "dw_large.sql")
    ap.add_argument(
        "--meta-src", type=Path, default=ROOT / "conf" / "meta_config.yaml"
    )
    ap.add_argument(
        "--meta-out", type=Path, default=ROOT / "conf" / "meta_config_large.yaml"
    )
    ap.add_argument("--report", action="store_true", help="只打印规模统计")
    args = ap.parse_args()

    shippers, cargos, warehouses, outbound_rows = load_base_context(args.seed, 2024, 2025)
    data = build_rows(outbound_rows, shippers, cargos, warehouses, seed=args.seed + 1)

    n_cols = sum(len(t.cols) for t in TABLES)

    errors = validate(data)
    if errors:
        print("生成器自检未通过：")
        for e in errors:
            print("  -", e)
        raise SystemExit(1)
    print(f"新增表 {len(TABLES)} 张，字段 {n_cols} 个")
    total_rows = sum(len(v) for v in data.values())
    print(f"新增数据行数合计：{total_rows}")
    if args.report:
        for t in TABLES:
            print(f"  {t.name:<22} {str(len(t.cols)):>2} 字段  {str(len(data.get(t.name, []))):>6} 行")
        return

    sql = render_sql(data)
    args.sql_out.parent.mkdir(parents=True, exist_ok=True)
    args.sql_out.write_text(sql, encoding="utf-8")
    print(f"已写入 {args.sql_out}  ({args.sql_out.stat().st_size / 1024:.0f} KB)")

    n = build_large_meta(args.meta_src, args.meta_out)
    print(f"已写入 {args.meta_out}  (追加 {n} 张表的元数据定义)")


if __name__ == "__main__":
    main()
