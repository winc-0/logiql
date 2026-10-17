from qdrant_client import AsyncQdrantClient, models

from app.conf.app_config import app_config
from app.models.qdrant.column_info_qdrant import ColumnInfoQdrant


class ColumnQdrantRepository:

    # 定义字段存储集合名称
    collection_name:str = "zhishu-column"

    def __init__(self,client:AsyncQdrantClient):
        self.client = client

    async def ensure_collection(self):
        """
        确保存储字段向量的集合存在
        :return:
        """
        # 查询并判断执行集合是否存在
        if not await self.client.collection_exists(collection_name=self.collection_name):
            # 创建集合
            await self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(size=app_config.qdrant.embedding_size, distance=models.Distance.COSINE),
            )

    async def recreate_collection(self):
        """
        删除并重建集合，供元数据全量重建前调用。

        直接复用 ensure_collection 无法清掉旧数据：字段描述或别名一旦调整，
        旧的向量点会残留在集合里，检索时依然可能被召回，导致元数据"改了却没生效"。
        """
        if await self.client.collection_exists(collection_name=self.collection_name):
            await self.client.delete_collection(collection_name=self.collection_name)
        await self.ensure_collection()

    async def upsert_column_embeddings(self, ids:list[str], embeddings:list[list[float]], payloads:list[ColumnInfoQdrant],batch_size:int=10):
        """
        保存字段向量到qdrant
        :param ids:
        :param embeddings:
        :param payloads:
        :return:
        """

        # 合并保存数据[(id,embedding,pyload),(id,embedding,pyload)]
        zipped=list(zip(ids,embeddings,payloads))
        # 遍历组合数据
        for i in range(0,len(zipped),batch_size):
            # 获取批量数据[(id,embedding,pyload),(id,embedding,pyload)]
            batch_zipped= zipped[i:i+batch_size]
            # 转换结构为[PointStruct,PointStruct,PointStruct]
            points=[ models.PointStruct(
                    id=id,
                    payload=payload,
                    vector=embdding,
                )
                for id,embdding,payload in batch_zipped]
            # 保存向量到qdrant
            await self.client.upsert(
                collection_name=self.collection_name,
                points=points
            )

    async def search(self, embeddings:list[float],score_threshold:float= 0.6 ):

        """
        根据指定向量召回字段信息列表
        :param embeddings:
        :param score_threshold:
        :return:
        """
        points = await self.client.query_points(
            collection_name=self.collection_name,
            query=embeddings,
            score_threshold=score_threshold

        )
        return [point.payload for point in points.points]



