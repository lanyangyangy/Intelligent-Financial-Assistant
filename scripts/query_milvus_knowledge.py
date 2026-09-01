from pymilvus import MilvusClient

from app.core.settings import get_settings

s=get_settings(); c=MilvusClient(uri=s.milvus_uri); c.load_collection(collection_name=s.milvus_collection); print(c.query(collection_name=s.milvus_collection,filter="id != ''",output_fields=["id","text","title_path"],limit=3))
