import yaml
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import DataAgentState, TableInfoState
import asyncio

from app.core.log import logger
from app.prompt.prompt_loader import load_prompt


async def generate_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    await asyncio.sleep(1)
    # 获取流对象
    writer = runtime.stream_writer
    # 输出进度信息
    writer({"stage": "生成sql"})
    try:

        # 获取用户的问题
        query = state["query"]
        # 获取表列表信息
        table_infos = state["table_infos"]
        # 获取指标列表信息
        metric_infos = state["metric_infos"]
        # 获取时间信息
        date_info = state["date_info"]
        # 获取数据库信息
        db_info = state["db_info"]

        # 构建提示词信息
        prompt = PromptTemplate(template=load_prompt("generate_sql"),
                                input_variables=["query", "table_infos", "metric_infos", "date_info", "db_info"])
        # 构建转换器
        output_parser = StrOutputParser()
        # 构建执行链
        chain = prompt | llm | output_parser
        # 执行链，获取sql语句
        sql =await  chain.ainvoke(
            {
                "query": query,
                "table_infos": yaml.dump(table_infos, allow_unicode=True, sort_keys=False),
                "metric_infos": yaml.dump(metric_infos, allow_unicode=True, sort_keys=False),
                "date_info": yaml.dump(date_info, allow_unicode=True, sort_keys=False),
                "db_info": yaml.dump(db_info, allow_unicode=True, sort_keys=False)
            }
        )
        # sql = """
        #         SELECT AVG(fo.outbound_amount) AS 平均出库货值
        #     FROM fact_outbound fo
        #              JOIN dim_warehouse dw ON fo.warehouse_id = dw.warehouse_id
        #     WHERE dw.region_name = '华北'
        # """


        logger.info(f"生成sql成功：{sql}")
        # 把 SQL 推给前端。原来只推了 stage 进度，界面上只看得到结果表格、
        # 看不到生成了什么语句——对一个 NL2SQL 系统来说这是最该看见的东西
        writer({"sql": sql})

        return {"sql":sql}
    except Exception as e:
        logger.info(f"生成sql异常：{str(e)}")
        raise