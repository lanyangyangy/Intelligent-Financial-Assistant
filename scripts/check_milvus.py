from pymilvus import MilvusClient

c=MilvusClient(uri="http://127.0.0.1:19530")
print({"healthy": c.is_health() if hasattr(c,"is_health") else "connected", "collections": c.list_collections()})
