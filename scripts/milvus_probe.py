from pymilvus import DataType, MilvusClient

URI="http://127.0.0.1:19530"
NAME="wealth_documents_probe"
DIM=4
c=MilvusClient(uri=URI)
if c.has_collection(collection_name=NAME): c.drop_collection(collection_name=NAME)
schema=c.create_schema(auto_id=False, enable_dynamic_field=True)
schema.add_field(field_name="id", datatype=DataType.VARCHAR, is_primary=True, max_length=64)
schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=DIM)
index=c.prepare_index_params(); index.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
c.create_collection(collection_name=NAME, schema=schema, index_params=index)
c.insert(collection_name=NAME,data=[{"id":"doc-1","vector":[1,0,0,0],"category":"risk"},{"id":"doc-2","vector":[0,1,0,0],"category":"product"}])
c.flush(collection_name=NAME)
c.load_collection(collection_name=NAME)
result=c.search(collection_name=NAME,data=[[1,0,0,0]],limit=1,output_fields=["category"]); print(result)
c.delete(collection_name=NAME,ids=["doc-1"]); print({"collections":c.list_collections(),"probe":"pass"})
