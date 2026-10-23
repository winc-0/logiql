-- =====================================================================
-- 仓配自助问数 · 仓配履约数据仓库（dw 库）小 schema 基线
--
-- 6 张表：1 张出库事实表 + 5 张维度表，采用出库单星型模型。
-- 事实表的业务数据由 scripts/gen_dw_data.py 按统一口径生成
-- （本文件自带的 2025Q1 示例数据会被该脚本 TRUNCATE 后重建，
--   原示例仅覆盖 90 天、单量密度与生成的 24 个月口径不一致，混用会在
--   时间趋势上造成一个假低谷）。
-- =====================================================================

SET NAMES utf8mb4;
USE dw;

DROP TABLE IF EXISTS dim_date;
CREATE TABLE dim_date
(
    date_id INT PRIMARY KEY COMMENT '日期YYYYMMDD',
    year    INT         COMMENT '年份',
    quarter VARCHAR(8)  COMMENT '季度Q1-Q4',
    month   INT         COMMENT '月份',
    day     INT         COMMENT '日'
) COMMENT '日期维度表';

INSERT INTO dim_date (date_id, year, quarter, month, day)
VALUES (20250101, 2025, 'Q1', 1, 1),
       (20250102, 2025, 'Q1', 1, 2),
       (20250103, 2025, 'Q1', 1, 3),
       (20250104, 2025, 'Q1', 1, 4),
       (20250105, 2025, 'Q1', 1, 5),
       (20250106, 2025, 'Q1', 1, 6),
       (20250107, 2025, 'Q1', 1, 7),
       (20250108, 2025, 'Q1', 1, 8),
       (20250109, 2025, 'Q1', 1, 9),
       (20250110, 2025, 'Q1', 1, 10);

INSERT INTO dim_date (date_id, year, quarter, month, day)
VALUES (20250111, 2025, 'Q1', 1, 11),
       (20250112, 2025, 'Q1', 1, 12),
       (20250113, 2025, 'Q1', 1, 13),
       (20250114, 2025, 'Q1', 1, 14),
       (20250115, 2025, 'Q1', 1, 15),
       (20250116, 2025, 'Q1', 1, 16),
       (20250117, 2025, 'Q1', 1, 17),
       (20250118, 2025, 'Q1', 1, 18),
       (20250119, 2025, 'Q1', 1, 19),
       (20250120, 2025, 'Q1', 1, 20);

INSERT INTO dim_date (date_id, year, quarter, month, day)
VALUES (20250121, 2025, 'Q1', 1, 21),
       (20250122, 2025, 'Q1', 1, 22),
       (20250123, 2025, 'Q1', 1, 23),
       (20250124, 2025, 'Q1', 1, 24),
       (20250125, 2025, 'Q1', 1, 25),
       (20250126, 2025, 'Q1', 1, 26),
       (20250127, 2025, 'Q1', 1, 27),
       (20250128, 2025, 'Q1', 1, 28),
       (20250129, 2025, 'Q1', 1, 29),
       (20250130, 2025, 'Q1', 1, 30);

INSERT INTO dim_date (date_id, year, quarter, month, day)
VALUES (20250131, 2025, 'Q1', 1, 31),
       (20250201, 2025, 'Q1', 2, 1),
       (20250202, 2025, 'Q1', 2, 2),
       (20250203, 2025, 'Q1', 2, 3),
       (20250204, 2025, 'Q1', 2, 4),
       (20250205, 2025, 'Q1', 2, 5),
       (20250206, 2025, 'Q1', 2, 6),
       (20250207, 2025, 'Q1', 2, 7),
       (20250208, 2025, 'Q1', 2, 8),
       (20250209, 2025, 'Q1', 2, 9);

INSERT INTO dim_date (date_id, year, quarter, month, day)
VALUES (20250210, 2025, 'Q1', 2, 10),
       (20250211, 2025, 'Q1', 2, 11),
       (20250212, 2025, 'Q1', 2, 12),
       (20250213, 2025, 'Q1', 2, 13),
       (20250214, 2025, 'Q1', 2, 14),
       (20250215, 2025, 'Q1', 2, 15),
       (20250216, 2025, 'Q1', 2, 16),
       (20250217, 2025, 'Q1', 2, 17),
       (20250218, 2025, 'Q1', 2, 18),
       (20250219, 2025, 'Q1', 2, 19);

INSERT INTO dim_date (date_id, year, quarter, month, day)
VALUES (20250220, 2025, 'Q1', 2, 20),
       (20250221, 2025, 'Q1', 2, 21),
       (20250222, 2025, 'Q1', 2, 22),
       (20250223, 2025, 'Q1', 2, 23),
       (20250224, 2025, 'Q1', 2, 24),
       (20250225, 2025, 'Q1', 2, 25),
       (20250226, 2025, 'Q1', 2, 26),
       (20250227, 2025, 'Q1', 2, 27),
       (20250228, 2025, 'Q1', 2, 28),
       (20250301, 2025, 'Q1', 3, 1);

INSERT INTO dim_date (date_id, year, quarter, month, day)
VALUES (20250302, 2025, 'Q1', 3, 2),
       (20250303, 2025, 'Q1', 3, 3),
       (20250304, 2025, 'Q1', 3, 4),
       (20250305, 2025, 'Q1', 3, 5),
       (20250306, 2025, 'Q1', 3, 6),
       (20250307, 2025, 'Q1', 3, 7),
       (20250308, 2025, 'Q1', 3, 8),
       (20250309, 2025, 'Q1', 3, 9),
       (20250310, 2025, 'Q1', 3, 10),
       (20250311, 2025, 'Q1', 3, 11);

INSERT INTO dim_date (date_id, year, quarter, month, day)
VALUES (20250312, 2025, 'Q1', 3, 12),
       (20250313, 2025, 'Q1', 3, 13),
       (20250314, 2025, 'Q1', 3, 14),
       (20250315, 2025, 'Q1', 3, 15),
       (20250316, 2025, 'Q1', 3, 16),
       (20250317, 2025, 'Q1', 3, 17),
       (20250318, 2025, 'Q1', 3, 18),
       (20250319, 2025, 'Q1', 3, 19),
       (20250320, 2025, 'Q1', 3, 20),
       (20250321, 2025, 'Q1', 3, 21);

INSERT INTO dim_date (date_id, year, quarter, month, day)
VALUES (20250322, 2025, 'Q1', 3, 22),
       (20250323, 2025, 'Q1', 3, 23),
       (20250324, 2025, 'Q1', 3, 24),
       (20250325, 2025, 'Q1', 3, 25),
       (20250326, 2025, 'Q1', 3, 26),
       (20250327, 2025, 'Q1', 3, 27),
       (20250328, 2025, 'Q1', 3, 28),
       (20250329, 2025, 'Q1', 3, 29),
       (20250330, 2025, 'Q1', 3, 30),
       (20250331, 2025, 'Q1', 3, 31);

DROP TABLE IF EXISTS dim_warehouse;
CREATE TABLE dim_warehouse
(
    warehouse_id   VARCHAR(20) PRIMARY KEY COMMENT '仓库ID',
    warehouse_name VARCHAR(32) NOT NULL    COMMENT '仓库名称',
    region_name    VARCHAR(20)             COMMENT '所属大区',
    city           VARCHAR(20)             COMMENT '所在城市',
    warehouse_type VARCHAR(20)             COMMENT '仓库类型'
) COMMENT '仓库维度表';

INSERT INTO dim_warehouse (warehouse_id, warehouse_name, region_name, city, warehouse_type)
VALUES ('W01', '华北1仓', '华北', '北京', '中心仓'),
       ('W02', '华北2仓', '华北', '北京', '区域仓'),
       ('W03', '华北3仓', '华北', '天津', '前置仓'),
       ('W04', '华北4仓', '华北', '北京', '冷链仓'),
       ('W05', '华东1仓', '华东', '上海', '中心仓'),
       ('W06', '华东2仓', '华东', '杭州', '区域仓'),
       ('W07', '华南1仓', '华南', '广州', '中心仓'),
       ('W08', '华南2仓', '华南', '深圳', '前置仓'),
       ('W09', '华中1仓', '华中', '武汉', '区域仓'),
       ('W10', '西南1仓', '西南', '成都', '区域仓');

DROP TABLE IF EXISTS dim_cargo;
CREATE TABLE dim_cargo
(
    cargo_id       VARCHAR(20) PRIMARY KEY COMMENT '货品ID',
    cargo_name     VARCHAR(64) NOT NULL    COMMENT '货品名称',
    cargo_category VARCHAR(32)             COMMENT '货品品类',
    cargo_brand    VARCHAR(32)             COMMENT '品牌',
    unit_price     DECIMAL(10,2)           COMMENT '单价（元）',
    unit_weight_kg DECIMAL(8,2)            COMMENT '单件重量（kg）'
) COMMENT '货品维度表';

INSERT INTO dim_cargo (cargo_id, cargo_name, cargo_category, cargo_brand, unit_price, unit_weight_kg)
VALUES ('G001', '智能手机', '3C数码', '华为', 3999.0, 0.35),
       ('G002', '平板电脑', '3C数码', '苹果', 4999.0, 0.68),
       ('G003', '无线耳机', '3C数码', '小米', 699.0, 0.12),
       ('G004', '变频空调', '家用电器', '美的', 3299.0, 32.0),
       ('G005', '滚筒洗衣机', '家用电器', '海尔', 2899.0, 68.0),
       ('G006', '羽绒服', '服装服饰', '波司登', 899.0, 1.2),
       ('G007', '运动跑鞋', '服装服饰', '耐克', 799.0, 0.9),
       ('G008', '纯牛奶箱装', '食品饮料', '蒙牛', 68.0, 12.0),
       ('G009', '坚果礼盒', '食品饮料', '三只松鼠', 129.0, 2.5),
       ('G010', '进口牛排', '生鲜冷链', '恒都', 258.0, 5.0);

INSERT INTO dim_cargo (cargo_id, cargo_name, cargo_category, cargo_brand, unit_price, unit_weight_kg)
VALUES ('G011', '冷鲜三文鱼', '生鲜冷链', '獐子岛', 198.0, 3.0),
       ('G012', '实木餐桌', '家居建材', '林氏木业', 1899.0, 45.0),
       ('G013', '记忆棉床垫', '家居建材', '慕思', 2599.0, 38.0),
       ('G014', '护肤精华', '美妆个护', '兰蔻', 1080.0, 0.3),
       ('G015', '儿童纸尿裤', '母婴用品', '好奇', 199.0, 3.2),
       ('G016', '面膜套装', '美妆个护', '欧莱雅', 328.0, 0.25),
       ('G017', '婴儿湿巾', '母婴用品', '帮宝适', 89.0, 1.8);

DROP TABLE IF EXISTS dim_shipper;
CREATE TABLE dim_shipper
(
    shipper_id    VARCHAR(20) PRIMARY KEY COMMENT '货主ID',
    shipper_name  VARCHAR(64) NOT NULL    COMMENT '货主名称',
    shipper_level VARCHAR(20)             COMMENT '客户等级',
    industry      VARCHAR(32)             COMMENT '所属行业'
) COMMENT '货主维度表';

INSERT INTO dim_shipper (shipper_id, shipper_name, shipper_level, industry)
VALUES ('S01', '京东零售', '战略', '电商零售'),
       ('S02', '天猫超市', '战略', '电商零售'),
       ('S03', '小米科技', '核心', '3C制造'),
       ('S04', '美的集团', '核心', '家电制造'),
       ('S05', '波司登服饰', '核心', '服装纺织'),
       ('S06', '三只松鼠', '普通', '食品饮料'),
       ('S07', '恒都农业', '普通', '生鲜供应'),
       ('S08', '林氏木业', '普通', '家居建材'),
       ('S09', '欧莱雅中国', '核心', '美妆个护'),
       ('S10', '好孩子集团', '普通', '母婴用品');

DROP TABLE IF EXISTS dim_carrier;
CREATE TABLE dim_carrier
(
    carrier_id    VARCHAR(20) PRIMARY KEY COMMENT '承运商ID',
    carrier_name  VARCHAR(32) NOT NULL    COMMENT '承运商名称',
    carrier_type  VARCHAR(20)             COMMENT '承运商类型',
    service_level VARCHAR(20)             COMMENT '主打时效产品'
) COMMENT '承运商维度表';

INSERT INTO dim_carrier (carrier_id, carrier_name, carrier_type, service_level)
VALUES ('CR01', '顺丰速运', '快运网络', '次日达'),
       ('CR02', '京东物流', '自营', '次日达'),
       ('CR03', '中通快运', '加盟', '标准陆运'),
       ('CR04', '圆通速递', '加盟', '标准陆运'),
       ('CR05', '德邦物流', '专线', '隔日达'),
       ('CR06', '邮政EMS', '快运网络', '经济');

DROP TABLE IF EXISTS fact_outbound;
CREATE TABLE fact_outbound
(
    outbound_id        VARCHAR(32) PRIMARY KEY COMMENT '出库单号',
    shipper_id         VARCHAR(20)  COMMENT '货主ID',
    cargo_id           VARCHAR(20)  COMMENT '货品ID',
    date_id            INT          COMMENT '出库日期',
    warehouse_id       VARCHAR(20)  COMMENT '出库仓ID',
    carrier_id         VARCHAR(20)  COMMENT '承运商ID',
    outbound_quantity  INT          COMMENT '出库件数',
    outbound_amount    DECIMAL(12,2) COMMENT '出库货值（元）',
    outbound_hours     INT          COMMENT '出库时效（下单到出库的小时数）',
    is_ontime_outbound TINYINT      COMMENT '是否按时出库，1=是',
    delivery_hours     INT          COMMENT '妥投时长（出库到签收的小时数）',
    is_delivered       TINYINT      COMMENT '是否妥投，1=是',
    is_ontime_delivery TINYINT      COMMENT '是否准时妥投，1=是',
    freight_amount     DECIMAL(10,2) COMMENT '运费（元）'
) COMMENT '出库单事实表，出库时写入、妥投后回填履约结果';
