import asyncio

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime


from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import DataAgentState
from app.core.log import logger
from app.models.qdrant.column_info_qdrant import ColumnInfoQdrant
from app.prompt.prompt_loader import load_prompt


async def recall_column(state:DataAgentState,runtime:Runtime[DataAgentContext]):
    await asyncio.sleep(1)
    # 获取流对象
    writer = runtime.stream_writer
    # 输出进度信息
    writer({"stage": "召回字段"})

    try:
        # 获取向量转换对象
        embedding_client=runtime.context["embedding_client"]
        # 获取字段向量召回repository
        column_qdrant_repository= runtime.context["column_qdrant_repository"]
        # 获取用户问题
        query = state["query"]
        # 获取提取的关键字
        keywords = state["keywords"]

        # 1.扩展关键字
        # 1.1 定义提示词模版
        prompt = PromptTemplate(template=load_prompt("extend_keywords_for_column_recall"),input_variables=["query"])
        # 1.2 构建输出解析器
        output_parser= JsonOutputParser()
        # 1.3 构建langchain链
        chain = prompt | llm | output_parser
        # 1.4 执行链获取结果
        result=await chain.ainvoke({"query":query})

        # 1.5 合并关键字
        keywords =set(keywords+result)

        # 定义字典去重字段对象
        recall_columns_map:dict[str,ColumnInfoQdrant]={}

        # 2.召回字段
        for keyword in keywords:
            # 2.1 转换向量
            embeddings=await embedding_client.aembed_query(keyword)
            # 2.2 根据向量查询qdrant
            payloads:list[ColumnInfoQdrant]=await column_qdrant_repository.search(embeddings)
            # 2.3 收集返回结果去重处理
            for payload in payloads:
                # 获取id
                column_id=payload["id"]
                # 判断
                if column_id not  in recall_columns_map:
                    recall_columns_map[column_id]= payload

        # 获取去重的字段列表
        recall_columns:list[ColumnInfoQdrant]= list(recall_columns_map.values())



        logger.info(f"召回字段信息成功：f{list(recall_columns_map.keys())}")

        return {"recall_columns":recall_columns}
    except Exception as e:
        logger.error(f"召回字段信息异常：{str(e)}")
        raise


