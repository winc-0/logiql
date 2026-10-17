import asyncio

import yaml
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import DataAgentState, MetricInfoState
from app.core.log import logger
from app.prompt.prompt_loader import load_prompt


async def filter_metric(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    # 获取流对象
    writer = runtime.stream_writer
    # 输出进度信息
    writer({"stage": "过滤指标"})

    try:
        # 获取用户的问题
        query = state["query"]
        # 获取指标信息列表
        metric_infos: list[MetricInfoState] = state["metric_infos"]

        # 1.调用模型，返回与业务相关的表信息
        # 1.1 定义提示词模版
        prompt = PromptTemplate(template=load_prompt("filter_metric_info"), input_variables=["query", "metric_infos"])
        # 1.2 构建数据转换器
        output_parser = JsonOutputParser()
        # 1.3 构建执行链
        chain = prompt | llm | output_parser
        # 1.4 执行链，获取结果
        result = await chain.ainvoke(
            {"query": query, "metric_infos": yaml.dump(metric_infos, allow_unicode=True, sort_keys=False)})

        """
            [
              "GMV",
            ]
        
        """
        # 遍历召回指标信息
        for metric_info in metric_infos[:]:
            # 获取指标name
            metric_name= metric_info["name"]
            # 判断，剔除
            if metric_name not in result:
                metric_infos.remove(metric_info)

        logger.info(f"过滤指标成功：{metric_infos}")

        return {"metric_infos":metric_infos}
    except Exception as e:
        logger.error(f"过滤指标异常：{str(e)}")
        raise