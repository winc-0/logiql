#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
生成仓配自助问数 dw 数据仓库的业务数据，替代初始自带的示例数据。

原 dw.sql 只含 2025 年 Q1 的 90 天日期与 115 笔出库单，时间跨度不足，
无法支撑「月度趋势 / 季度对比 / 年度同比」这类问数场景，评测也没有区分度。
本脚本按第三方仓配（3PL）业务的真实形态补齐 2024-01-01 ~ 2025-12-31
共 24 个月的仓配履约数据。

设计原则：
    1. 可复现：固定随机种子，同一份代码永远产出同一份数据
    2. 有业务含义：不是纯随机，而是带季节性、仓型吞吐差异、客户等级权重、
       承运商时效分层、品类货值带
    3. 可解释：每条生成规则都能对应到一条业务假设，便于在评测报告里说明

业务假设（直接决定评测里能不能看出信号）：
    - 仓型吞吐：中心仓 > 区域仓 > 前置仓 > 冷链仓（冷链只做生鲜品类）
    - 季节性：Q4 受双十一与年货节拉动显著高于全年，2 月受春节停摆影响为全年最低
    - 年度增长：2025 年整体比 2024 年增长约 25%，让同比/环比类问数有真实信号
    - 时效分层：次日达承运商妥投更快但运费更高；经济型更慢更便宜
    - 生鲜冷链只在冷链仓（W04）出库，否则「冷链仓出库量」这类问题没有区分度

用法：
    python scripts/gen_dw_data.py                      # 输出到 docker/mysql/dw_expand.sql
    python scripts/gen_dw_data.py --stdout             # 打印到标准输出
    python scripts/gen_dw_data.py --seed 42 --start 2024 --end 2025
"""
from __future__ import annotations

import argparse
import datetime as dt
import random
from pathlib import Path

# ---------------------------------------------------------------- 业务档案
#
# 下面四张维表的档案必须与 docker/mysql/dw.sql 中的 INSERT 一一对应：
# 事实表的外键要能 JOIN 到维表，改一边忘改另一边会直接产生 NULL 分组。

# 仓库档案：(warehouse_id, 仓型, 吞吐权重, 是否冷链仓)
# 权重体现仓型差异 —— 中心仓承担主要出货，前置仓只做末端补货
WAREHOUSES = [
    ("W01", "中心仓", 0.18, False),
    ("W02", "区域仓", 0.13, False),
    ("W03", "前置仓", 0.07, False),
    ("W04", "冷链仓", 0.05, True),
    ("W05", "中心仓", 0.17, False),
    ("W06", "区域仓", 0.11, False),
    ("W07", "中心仓", 0.14, False),
    ("W08", "前置仓", 0.06, False),
    ("W09", "区域仓", 0.06, False),
    ("W10", "区域仓", 0.03, False),
]

# 货品档案：(cargo_id, 品类, 单价, 单件重量kg, 单笔件数下限, 单笔件数上限, 出货频次权重)
CARGOS = [
    ("G001", "3C数码",   3999.0, 0.35, 1,  5,  0.9),
    ("G002", "3C数码",   4999.0, 0.68, 1,  3,  0.7),
    ("G003", "3C数码",    699.0, 0.12, 2, 20,  1.2),
    ("G004", "家用电器",  3299.0, 32.0, 1,  2,  0.6),
    ("G005", "家用电器",  2899.0, 68.0, 1,  2,  0.5),
    ("G006", "服装服饰",   899.0, 1.20, 1, 10,  1.3),
    ("G007", "服装服饰",   799.0, 0.90, 1, 12,  1.2),
    ("G008", "食品饮料",    68.0, 12.0, 5, 40,  1.6),
    ("G009", "食品饮料",   129.0, 2.50, 3, 30,  1.5),
    ("G010", "生鲜冷链",   258.0, 5.00, 2, 20,  1.1),
    ("G011", "生鲜冷链",   198.0, 3.00, 2, 15,  1.0),
    ("G012", "家居建材",  1899.0, 45.0, 1,  3,  0.5),
    ("G013", "家居建材",  2599.0, 38.0, 1,  2,  0.4),
    ("G014", "美妆个护",  1080.0, 0.30, 1, 15,  1.1),
    ("G015", "母婴用品",   199.0, 3.20, 2, 25,  1.3),
    ("G016", "美妆个护",   328.0, 0.25, 2, 20,  1.2),
    ("G017", "母婴用品",    89.0, 1.80, 3, 30,  1.4),
]
# 只有生鲜品类能走冷链仓，否则「冷链仓出库结构」这类问题没有业务含义
COLD_CHAIN_CATEGORY = "生鲜冷链"

# 货主档案：(shipper_id, 客户等级)。等级越高，出货频次与单均货值越高
SHIPPERS = [
    ("S01", "战略"), ("S02", "战略"), ("S03", "核心"), ("S04", "核心"),
    ("S05", "核心"), ("S06", "普通"), ("S07", "普通"), ("S08", "普通"),
    ("S09", "核心"), ("S10", "普通"),
]
SHIPPER_WEIGHT = {"战略": 2.0, "核心": 1.4, "普通": 0.9}

# 承运商档案：(carrier_id, 时效产品, 妥投时长下限h, 上限h, 准时妥投率, 每kg费率)
# 费率与时效正相关（快时效更贵），但重货倾向走经济渠道，
# 因此「均运费」并不必然与时效正相关 —— 这正是值得下钻分析的点
CARRIERS = [
    ("CR01", "次日达",   12,  36,  0.96, 2.4),
    ("CR02", "次日达",   14,  40,  0.94, 2.1),
    ("CR03", "标准陆运", 36,  84,  0.90, 1.4),
    ("CR04", "标准陆运", 40,  96,  0.88, 1.3),
    ("CR05", "隔日达",   24,  60,  0.92, 1.8),
    ("CR06", "经济",     72, 168,  0.82, 0.9),
]

# 季节性：Q4 含双十一与年货节，2 月受春节休假影响
QUARTER_FACTOR = {1: 0.92, 2: 1.00, 3: 1.00, 4: 1.28}
MONTH_FACTOR = {
    1: 1.12, 2: 0.68, 3: 1.00, 4: 1.00, 5: 1.00, 6: 1.26,
    7: 1.00, 8: 1.00, 9: 1.05, 10: 1.12, 11: 1.52, 12: 1.32,
}
# 年度增长：2025 年整体比 2024 年增长约 25%
YEAR_FACTOR = {2024: 1.00, 2025: 1.25}

# 每日基准出库单量
BASE_OUTBOUND_PER_DAY = 3.0

# 运费首重费（元/票）。快递计价的固定部分，与重量无关
BASE_FREIGHT_FEE = 12.0

# 仓型对出库时效的影响倍率：前置仓离客户近、作业轻，出库最快
WAREHOUSE_SPEED = {"中心仓": 1.15, "区域仓": 1.00, "前置仓": 0.80, "冷链仓": 1.30}
# 出库时效基准（小时），超过 24 小时即视为未按时出库。
# 取 18 是因为若基准太低，除中心仓外所有仓型的时效都在 24 小时内，
# 「各仓出库及时率」会齐刷刷 100%，指标失去区分度
BASE_OUTBOUND_HOURS = 18

# dw.sql 预置的 2025Q1 数据（90 天 / 115 笔）口径与生成数据不一致：
# 它的单量密度只有其它季度的三分之一，直接混用会让 2025Q1 在时间趋势上
# 出现一个假的低谷。因此在写入生成数据前先清除该区间，再用统一口径重建。
PRESET_RANGE = (dt.date(2025, 1, 1), dt.date(2025, 3, 31))


def build_rows(seed: int, start_year: int, end_year: int):
    """生成 dim_date 与 fact_outbound 的数据行。返回 (日期行列表, 出库行列表)。"""
    rng = random.Random(seed)

    wh_ids = [w[0] for w in WAREHOUSES]
    wh_weights = [w[2] for w in WAREHOUSES]
    wh_type = {w[0]: w[1] for w in WAREHOUSES}
    wh_cold = {w[0]: w[3] for w in WAREHOUSES}

    cargo_ids = [c[0] for c in CARGOS]
    cargo_weights = [c[6] for c in CARGOS]
    cargo_price = {c[0]: c[2] for c in CARGOS}
    cargo_weight_kg = {c[0]: c[3] for c in CARGOS}
    cargo_qty = {c[0]: (c[4], c[5]) for c in CARGOS}
    cargo_category = {c[0]: c[1] for c in CARGOS}

    shipper_ids = [s[0] for s in SHIPPERS]
    shipper_weights = [SHIPPER_WEIGHT[s[1]] for s in SHIPPERS]

    carrier_ids = [c[0] for c in CARRIERS]
    # 高价值货物更倾向走快时效承运商：按货值带分配权重，让运费与时效相关
    carrier_fast = [c[0] for c in CARRIERS if c[1] in ("次日达", "隔日达")]
    carrier_slow = [c[0] for c in CARRIERS if c[1] not in ("次日达", "隔日达")]
    carrier_profile = {c[0]: c for c in CARRIERS}

    date_rows: list[tuple[int, int, str, int, int]] = []
    outbound_rows: list[tuple] = []

    day = dt.date(start_year, 1, 1)
    last = dt.date(end_year, 12, 31)

    while day <= last:
        y, m, d = day.year, day.month, day.day
        q = (m - 1) // 3 + 1
        date_id = y * 10000 + m * 100 + d

        date_rows.append((date_id, y, f"Q{q}", m, d))

        intensity = (
            BASE_OUTBOUND_PER_DAY
            * QUARTER_FACTOR[q] * MONTH_FACTOR[m] * YEAR_FACTOR[y]
        )
        # 工作日比周末单量略高
        if day.weekday() < 5:
            intensity *= 1.1
        count = int(intensity) + (1 if rng.random() < intensity % 1 else 0)

        for seq in range(1, count + 1):
            wid = rng.choices(wh_ids, weights=wh_weights, k=1)[0]
            cid = rng.choices(cargo_ids, weights=cargo_weights, k=1)[0]

            # 冷链仓只出生鲜；非冷链仓不出生鲜 —— 保证「冷链仓」是一个有意义的筛选维度
            if wh_cold[wid] and cargo_category[cid] != COLD_CHAIN_CATEGORY:
                cid = "G010"
            if not wh_cold[wid] and cargo_category[cid] == COLD_CHAIN_CATEGORY:
                cid = "G001"

            sid = rng.choices(shipper_ids, weights=shipper_weights, k=1)[0]

            lo, hi = cargo_qty[cid]
            qty = rng.randint(lo, hi)
            # 货值允许 ±5% 的促销浮动，保留 2 位小数
            unit = cargo_price[cid] * rng.uniform(0.95, 1.00)
            amount = round(unit * qty, 2)

            # 高货值走快时效承运商
            if amount >= 3000 and rng.random() < 0.75:
                crid = rng.choice(carrier_fast)
            elif amount < 500 and rng.random() < 0.6:
                crid = rng.choice(carrier_slow)
            else:
                crid = rng.choice(carrier_ids)

            _, _, dlo, dhi, ontime_rate, rate_kg = carrier_profile[crid]

            # 出库时效：仓型决定快慢，冷链仓多一道温控复核
            ob_hours = int(BASE_OUTBOUND_HOURS * WAREHOUSE_SPEED[wh_type[wid]] * rng.uniform(0.7, 1.6))
            ob_hours = max(2, ob_hours)

            # 妥投时长与是否准时：由承运商的时效档位决定
            delivery_hours = rng.randint(dlo, dhi)
            is_ontime_delivery = 1 if rng.random() < ontime_rate else 0
            # 未准时妥投的单，实际时长要明显超出时效上限，否则「准时率」与「时长」不自洽
            if not is_ontime_delivery:
                delivery_hours = int(delivery_hours * rng.uniform(1.3, 2.0))
            # 极小概率丢件/拒收，形成未妥投
            is_delivered = 0 if rng.random() < 0.03 else 1
            if not is_delivered:
                delivery_hours = 0
                is_ontime_delivery = 0

            # 运费：首重费（按票固定）+ 续重费（按 kg 阶梯费率）
            # 首重费是每票都要收的揽收与面单成本，缺了它轻小件会算出几块钱的运费
            kg = round(cargo_weight_kg[cid] * qty, 2)
            freight = round(BASE_FREIGHT_FEE + rate_kg * kg, 2)

            outbound_rows.append((
                f"OB{y:04d}{m:02d}{d:02d}{seq:03d}",
                sid, cid, date_id, wid, crid,
                qty, amount, ob_hours,
                1 if ob_hours <= 24 else 0,
                delivery_hours, is_delivered, is_ontime_delivery,
                freight,
            ))

        day += dt.timedelta(days=1)

    return date_rows, outbound_rows


def render_sql(date_rows, outbound_rows) -> str:
    """把数据行渲染成 SQL。分块 INSERT，避免单条语句过长。"""
    out: list[str] = []
    out.append("-- 本文件由 scripts/gen_dw_data.py 自动生成，请勿手工编辑。")
    out.append("-- 初始自带数据仅覆盖 2025Q1，这里用统一口径重建 2024-01-01 ~ 2025-12-31 的完整业务数据。")
    out.append("")
    out.append("SET NAMES utf8mb4;")
    out.append("USE dw;")
    out.append("")
    out.append("-- 预置的 2025Q1 出库单量密度只有其它季度的三分之一，与生成数据混用")
    out.append("-- 会让 2025Q1 在时间趋势上出现一个假低谷，因此这两张表全部改用统一口径生成。")
    out.append("-- 清空而非按区间删除，是为了让本文件可重复执行。")
    out.append("TRUNCATE TABLE fact_outbound;")
    out.append("TRUNCATE TABLE dim_date;")
    out.append("")

    out.append(f"-- 日期维度：{len(date_rows)} 天")
    for i in range(0, len(date_rows), 100):
        chunk = date_rows[i:i + 100]
        vals = ",\n       ".join(
            f"({did}, {y}, '{q}', {m}, {d})" for did, y, q, m, d in chunk
        )
        out.append("INSERT INTO dim_date (date_id, year, quarter, month, day)")
        out.append(f"VALUES {vals};")
    out.append("")

    out.append(f"-- 出库事实表：{len(outbound_rows)} 笔")
    for i in range(0, len(outbound_rows), 100):
        chunk = outbound_rows[i:i + 100]
        vals = ",\n       ".join(
            "('{}', '{}', '{}', {}, '{}', '{}', {}, {}, {}, {}, {}, {}, {}, {})".format(*row)
            for row in chunk
        )
        out.append(
            "INSERT INTO fact_outbound "
            "(outbound_id, shipper_id, cargo_id, date_id, warehouse_id, carrier_id, "
            "outbound_quantity, outbound_amount, outbound_hours, is_ontime_outbound, "
            "delivery_hours, is_delivered, is_ontime_delivery, freight_amount)"
        )
        out.append(f"VALUES {vals};")
    out.append("")

    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="生成仓配自助问数 dw 仓配履约数据仓库业务数据")
    ap.add_argument("--seed", type=int, default=20251018, help="随机种子，决定数据内容")
    ap.add_argument("--start", type=int, default=2024, help="起始年份")
    ap.add_argument("--end", type=int, default=2025, help="结束年份")
    ap.add_argument(
        "--out", type=Path, default=Path("docker/mysql/dw_expand.sql"),
        help="输出 SQL 文件路径",
    )
    ap.add_argument("--stdout", action="store_true", help="打印到标准输出而不写文件")
    args = ap.parse_args()

    date_rows, outbound_rows = build_rows(args.seed, args.start, args.end)
    sql = render_sql(date_rows, outbound_rows)

    if args.stdout:
        print(sql)
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(sql, encoding="utf-8")
        print(f"已写入 {args.out}")
        print(f"  日期维度：{len(date_rows)} 天")
        print(f"  出库事实：{len(outbound_rows)} 笔")
        print(f"  文件大小：{args.out.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
