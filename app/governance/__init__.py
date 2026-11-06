"""数据治理：数据质量规则（dq）与字段级血缘（lineage）。

两部分都用声明式配置（conf/dq_rules.yaml、conf/lineage.yaml）描述，
这里只放解析、校验和执行逻辑，不依赖 Agent 主流程。
"""
