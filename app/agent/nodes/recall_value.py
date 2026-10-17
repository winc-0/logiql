import asyncio

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import DataAgentState
from app.core.log import logger
from app.models.es.value_info_es import ValueInfoEs
from app.prompt.prompt_loader import load_prompt


async def recall_value(state:DataAgentState,runtime:Runtime[DataAgentContext]):
    # 获取流对象
    writer = runtime.stream_writer
    # 输出进度信息
    writer({"stage": "召回字段取值"})

    try:
        # 获取repository
        value_es_repository= runtime.context["value_es_repository"]

        # 获取用户问题
        query = state["query"]
        # 获取提取的关键字
        keywords = state["keywords"]

        # 1.扩展关键字
        # 1.1 定义提示词模版
        prompt = PromptTemplate(template=load_prompt("extend_keywords_for_value_recall"), input_variables=["query"])
        # 1.2 构建输出解析器
        output_parser = JsonOutputParser()
        # 1.3 构建langchain链
        chain = prompt | llm | output_parser
        # 1.4 执行链获取结果
        result = await chain.ainvoke({"query": query})

        # 2.合并关键字
        keywords = set(keywords + result)

        # 定义map去重收集取值结果
        value_maps:dict[str,ValueInfoEs]={}

        # 3.召回字段取值
        for keyword in keywords:
            # 根据关键字查询es 字段取值
            values:list[ValueInfoEs] = await value_es_repository.search_value(keyword)
            # 处理返回结果
            for value in values:
                # 获取取值的id
                value_id=value["id"]
                # 判断
                if value_id not in value_maps:
                    value_maps[value_id]=value


        # 转换结果结构
        recall_values:list[ValueInfoEs] =list(value_maps.values())

        logger.info(f"召回字段取值成功：{list(value_maps.keys())}")
        return {"recall_values":recall_values}
    except Exception as e:
        logger.error(f"召回字段取值异常{str(e)}")
        raise