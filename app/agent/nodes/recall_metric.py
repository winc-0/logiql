import asyncio

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import DataAgentState
from app.core.log import logger
from app.models.qdrant.metric_info_qdrant import MetricInfoQdrant
from app.prompt.prompt_loader import load_prompt


async def recall_metric(state:DataAgentState,runtime:Runtime[DataAgentContext]):
    await asyncio.sleep(1)
    # 获取流对象
    writer = runtime.stream_writer
    # 输出进度信息
    writer({"stage": "召回指标"})
    # 获取用户问题
    query = state["query"]
    # 获取关键字列表
    keywords = state["keywords"]
    # 获取向量转换对象
    embedding_client=runtime.context["embedding_client"]

    try:
        # 获取repository对象
        metric_qdrant_repository= runtime.context["metric_qdrant_repository"]

        # 1.扩展关键字
        # 1.1 定义提示词模版
        prompt = PromptTemplate(template=load_prompt("extend_keywords_for_metric_recall"), input_variables=["query"])
        # 1.2 构建输出解析器
        output_parser = JsonOutputParser()
        # 1.3 构建langchain链
        chain = prompt | llm | output_parser
        # 1.4 执行链获取结果
        result = await chain.ainvoke({"query": query})

        # 2.合并关键字
        keywords = set(keywords+result)

        logger.info(f"扩展后关键字：{[keywords]}")

        # 定义字典处理返回指标
        retrieved_metric_map:dict[str,MetricInfoQdrant]={}

        # 3.召回指标
        for keyword in keywords:
            # 转换向量
            embedding=await embedding_client.aembed_query(keyword)

            # 根据向量查询qdrant中的指标数据
            payloads:list[MetricInfoQdrant]= await  metric_qdrant_repository.search_embedding(embedding)

            # 处理查询结果-去重
            for payload in payloads:
                # 获取指标id
                metric_id = payload["id"]
                # 判断是否已经存在召回指标列表中
                if metric_id not in retrieved_metric_map:
                    # 收集指标
                    retrieved_metric_map[metric_id]=payload

        # 处理返回结果
        recall_metric:list[MetricInfoQdrant]=list(retrieved_metric_map.values())

        logger.info(f"召回指标成功:{[retrieved_metric_map.keys() ]}")

        return {"recall_metric":recall_metric}
    except Exception as e:
        logger.error(f"召回指标异常：{str(e)}")
        raise



