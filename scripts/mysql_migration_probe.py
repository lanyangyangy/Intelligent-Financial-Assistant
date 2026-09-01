import asyncio

import aiomysql


async def main():
    conn=await aiomysql.connect(host="127.0.0.1",port=3307,user="wealth",password="wealth_dev",db="wealth_manager",autocommit=True)
    async with conn.cursor() as cur:
        await cur.execute("SELECT VERSION()")
        print({"mysql_version": (await cur.fetchone())[0]})
        await cur.execute("CREATE TABLE IF NOT EXISTS migration_probe (id VARCHAR(36) PRIMARY KEY, name VARCHAR(128) NOT NULL, amount DECIMAL(18,2) NOT NULL DEFAULT 0, metadata_json JSON NULL, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        await cur.execute("INSERT INTO migration_probe (id,name,amount,metadata_json) VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE name=VALUES(name), amount=VALUES(amount), metadata_json=VALUES(metadata_json)", ("probe-1","mysql-adapter", "123.45", '{"backend":"mysql"}'))
        await cur.execute("SELECT id,name,amount,metadata_json FROM migration_probe WHERE id=%s", ("probe-1",))
        row=await cur.fetchone(); print({"probe_row": row})
    conn.close()

if __name__ == "__main__": asyncio.run(main())
