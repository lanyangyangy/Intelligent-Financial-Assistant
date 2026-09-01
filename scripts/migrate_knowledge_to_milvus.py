import asyncio

from pymilvus import DataType, MilvusClient
from sqlalchemy import select

from app.core.settings import get_settings
from app.db.session import Database
from app.models.knowledge import KnowledgeChunk, KnowledgeDocument, KnowledgeDocumentVersion


async def main():
 settings=get_settings(); db=Database(settings); client=MilvusClient(uri=settings.milvus_uri); name=settings.milvus_collection
 if client.has_collection(collection_name=name): client.drop_collection(collection_name=name)
 schema=client.create_schema(auto_id=False,enable_dynamic_field=True); schema.add_field(field_name="id",datatype=DataType.VARCHAR,is_primary=True,max_length=64); schema.add_field(field_name="vector",datatype=DataType.FLOAT_VECTOR,dim=settings.embedding_dimension)
 index=client.prepare_index_params(); index.add_index(field_name="vector",index_type="AUTOINDEX",metric_type="COSINE"); client.create_collection(collection_name=name,schema=schema,index_params=index)
 async with db.session_factory() as session:
  q=select(KnowledgeChunk).join(KnowledgeDocument,KnowledgeDocument.id==KnowledgeChunk.document_id).join(KnowledgeDocumentVersion,KnowledgeDocumentVersion.id==KnowledgeChunk.version_id).where(KnowledgeChunk.status=="active",KnowledgeDocument.status=="active",KnowledgeDocument.deleted_at.is_(None),KnowledgeDocumentVersion.status=="active",KnowledgeChunk.embedding.is_not(None)).limit(1000)
  rows=list((await session.execute(q)).scalars().all())
 data=[{"id":str(r.id),"vector":list(r.embedding),"text":r.content,"document_id":str(r.document_id),"version_id":str(r.version_id),"title_path":r.title_path,"content_hash":r.content_hash} for r in rows]
 if data: client.insert(collection_name=name,data=data); client.flush(collection_name=name); client.load_collection(collection_name=name)
 print({"collection":name,"migrated":len(data),"collections":client.list_collections()})
 await db.dispose()
if __name__=='__main__': asyncio.run(main())
