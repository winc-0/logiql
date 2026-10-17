import asyncio
from typing import Optional

from langchain_huggingface import HuggingFaceEndpointEmbeddings

from app.conf.app_config import app_config, EmbeddingConfig


class EmbeddingClientManager:
    def __init__(self,config:EmbeddingConfig):
        self.client:Optional[HuggingFaceEndpointEmbeddings] =None
        self.config =config

    def _get_url(self):
        return f"http://{self.config.host}:{self.config.port}"
    def init(self):
        self.client = HuggingFaceEndpointEmbeddings(model=self._get_url())

embedding_manager = EmbeddingClientManager(app_config.embedding)

if __name__ == '__main__':
    # 初始化向量对象
    embedding_manager.init()
    # 获取向量对象
    embeddings=embedding_manager.client
    # 定义异步函数
    async def test():
        # 定义文本
        text="我是中国人"
        text_list=["我是中国人","我爱中国"]
        # 转换 list[float]
        # query_result =await embeddings.aembed_query(text)
        # list[list[float],list[float]]
        result_list=await embeddings.aembed_documents(text_list)

        print(result_list)
        # print(len(query_result))
        # print(type(query_result))
        # print(query_result)

    asyncio.run(test())









