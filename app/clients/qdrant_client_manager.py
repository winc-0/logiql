import asyncio
import random
from typing import Optional


from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import Distance, models

from app.conf.app_config import app_config, QdrantConfig


class QdrantClientManager():
    def __init__(self, config: QdrantConfig):
        self.client: Optional[AsyncQdrantClient] = None
        self.config = config

    def _get_url(self):
        return f"http://{self.config.host}:{self.config.port}"

    def init(self):
        self.client = AsyncQdrantClient(url=self._get_url())

    async def close(self):
        await self.client.close()


qdrant_client_manager = QdrantClientManager(app_config.qdrant)

if __name__ == '__main__':

    # 初始化客户端对象
    qdrant_client_manager.init()
    # 获取客户端
    client =qdrant_client_manager.client
    async def test():

        # 1.判断指定集合是否存在
        if not  await client.collection_exists(collection_name="my_collection"):
             # 2.不存在集合，创建
             await client.create_collection(
                 collection_name="my_collection",
                 vectors_config=models.VectorParams(size=app_config.qdrant.embedding_size, distance=Distance.COSINE),
             )

        # 3.新增向量数据
        # await client.upsert(
        #     collection_name="my_collection",
        #     points=[
        #         models.PointStruct(
        #             id=i,
        #             payload={
        #                 "color": f"red{i}",
        #             },
        #             vector=[ random.random() for _ in range(1024)],
        #         )
        #         for i in range(10)
        #
        #     ],
        # )


        # # 4.查询向量数据
        points = await client.query_points(
            collection_name="my_collection",
            query=[ random.random() for _ in range(1024)],
            limit=2,
            score_threshold=0.4
        )

        print(points)
        print("-----------------")
        print(points.points)
        print("-----------------")
        print(points.points[0].payload)
        list=[point.payload for point in points.points]
        print(list)
        # for point in points.points:
        #     print(point)




        await qdrant_client_manager.close()

    asyncio.run(test())
