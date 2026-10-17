import asyncio

import yaml
from dns.e164 import query
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import DataAgentState, TableInfoState
from app.core.log import logger
from app.prompt.prompt_loader import load_prompt


async def filter_table(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    # 获取流对象
    writer = runtime.stream_writer
    # 输出进度信息
    writer({"stage": "过滤表信息"})
    try:
        # 获取用户的问题
        query = state["query"]
        # 获取表信息列表
        table_infos: list[TableInfoState] = state["table_infos"]

        # 1.调用模型，返回与业务相关的表信息
        # 1.1 定义提示词模版
        prompt = PromptTemplate(template=load_prompt("filter_table_info"), input_variables=["query", "table_infos"])
        # 1.2 构建数据转换器
        output_parser = JsonOutputParser()
        # 1.3 构建执行链
        chain = prompt | llm | output_parser
        # 1.4 执行链，获取结果
        result = await chain.ainvoke({"query": query, "table_infos": yaml.dump(table_infos,allow_unicode=True,sort_keys=False)})
        """
        模型返回结构示例（问「华北地区2025年的出库货值是多少」）：
        {
            'fact_outbound': ['outbound_amount', 'warehouse_id'],
            'dim_warehouse': ['region_name', 'warehouse_id']
        }

        select sum(fact_outbound.outbound_amount) from dim_warehouse inner join fact_outbound
             on dim_warehouse.warehouse_id = fact_outbound.warehouse_id
            where dim_warehouse.region_name = '华北'
        """

        # 2.循环遍历合并表信息，根据业务相关结果，剔除信息
        for table_info in table_infos[:]:
            # 获取表的id
            table_name = table_info["name"]

            # 判断是否为业务相关
            if table_name not in result:
                table_infos.remove(table_info)
            else:
                # 获取模型处理后的字段列表
                result_column = result[table_name]
                # 遍历表的columns
                for column_info in table_info["columns"][:]:
                    # 获取召回的字段名称
                    column_name=column_info["name"]
                    if column_name not in result_column:
                        table_info["columns"].remove(column_info)

        logger.info(f"过滤表成功：{table_infos}")

        return {"table_infos":table_infos}
    except Exception as e:
        logger.error(f"过滤表异常:{str(e)}")
        raise
