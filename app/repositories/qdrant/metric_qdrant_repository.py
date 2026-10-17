from qdrant_client import AsyncQdrantClient, models

from app.conf.app_config import app_config
from app.models.qdrant.metric_info_qdrant import MetricInfoQdrant


class MetricQdrantRepository:
    # 定义字段存储集合名称
    collection_name: str = "zhishu-metric"

    def __init__(self,client:AsyncQdrantClient):
        self.client = client



    async def ensure_collection(self):
        """
        确保存储指标向量的集合存在
        :return:
        """
        # 查询并判断执行集合是否存在
        if not await self.client.collection_exists(collection_name=self.collection_name):
            # 创建集合
            await self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(size=app_config.qdrant.embedding_size,
                                                   distance=models.Distance.COSINE),
            )

    async def recreate_collection(self):
        """
        删除并重建集合，供元数据全量重建前调用。

        指标口径调整后，旧的向量点会残留在集合里继续被召回，
        导致元数据"改了却没生效"。
        """
        if await self.client.collection_exists(collection_name=self.collection_name):
            await self.client.delete_collection(collection_name=self.collection_name)
        await self.ensure_collection()

    async def upsert_column_embeddings(self, ids:list[str], embeddings:list[list[float]], payloads:list[MetricInfoQdrant],batch_size:int=10):
        """
        保存指标信息到向量数据库
        :param ids:
        :param embeddings:
        :param payloads:
        :param batch_size:
        :return:
        """
        # 合并保存数据[(id,embedding,pyload),(id,embedding,pyload)]
        zipped = list(zip(ids, embeddings, payloads))
        # 遍历组合数据
        for i in range(0, len(zipped), batch_size):
            # 获取批量数据[(id,embedding,pyload),(id,embedding,pyload)]
            batch_zipped = zipped[i:i + batch_size]
            # 转换结构为[PointStruct,PointStruct,PointStruct]
            points = [models.PointStruct(
                id=id,
                payload=payload,
                vector=embdding,
            )
                for id, embdding, payload in batch_zipped]
            # 保存向量到qdrant
            await self.client.upsert(
                collection_name=self.collection_name,
                points=points
            )

    async def search_embedding(self, embedding:list[float],limit:int =10):
        """
        根据指标召回指标向量
        :param embedding:
        :return:
        """
        # Search for nearest neighbors
        points = await self.client.query_points(
            collection_name=self.collection_name,
            query=embedding,
            score_threshold=0.6,
            limit=limit
        )

        return [point.payload for point in points.points]

