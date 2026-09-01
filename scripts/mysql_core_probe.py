import asyncio
import uuid

import aiomysql

DDL={
"users":"CREATE TABLE IF NOT EXISTS users (id VARCHAR(36) PRIMARY KEY, username VARCHAR(128) NOT NULL UNIQUE, display_name VARCHAR(128) NOT NULL, status VARCHAR(20) NOT NULL DEFAULT 'active', created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)",
"customer_profile":"CREATE TABLE IF NOT EXISTS customer_profile (id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36) NOT NULL UNIQUE, age INT NULL, occupation VARCHAR(128) NOT NULL DEFAULT '', region VARCHAR(128) NOT NULL DEFAULT '', customer_type VARCHAR(64) NOT NULL DEFAULT 'individual', customer_tier VARCHAR(64) NOT NULL DEFAULT 'ordinary', investment_experience_years INT NOT NULL DEFAULT 0, investment_goal VARCHAR(64) NOT NULL DEFAULT 'balanced', investment_horizon_years INT NULL, liquidity_preference VARCHAR(16) NOT NULL DEFAULT 'medium', created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)",
"product":"CREATE TABLE IF NOT EXISTS product (id VARCHAR(36) PRIMARY KEY, name VARCHAR(255) NOT NULL UNIQUE, product_type VARCHAR(64) NOT NULL DEFAULT 'fund', risk_level VARCHAR(16) NOT NULL DEFAULT 'C1', target_customer_type VARCHAR(64) NOT NULL DEFAULT 'individual', minimum_amount DECIMAL(18,2) NOT NULL DEFAULT 0, status VARCHAR(32) NOT NULL DEFAULT 'active', created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)",
"customer_risk_assessment":"CREATE TABLE IF NOT EXISTS customer_risk_assessment (id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36) NOT NULL, risk_level VARCHAR(8) NOT NULL DEFAULT 'C1', score INT NOT NULL DEFAULT 0, answers_json JSON NULL, status VARCHAR(32) NOT NULL DEFAULT 'provisional', source_type VARCHAR(32) NOT NULL DEFAULT 'system_default', assessed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, expires_at TIMESTAMP NULL)",
"account":"CREATE TABLE IF NOT EXISTS account (id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36) NOT NULL UNIQUE, account_no VARCHAR(64) NOT NULL UNIQUE, available_balance DECIMAL(18,2) NOT NULL DEFAULT 0, frozen_balance DECIMAL(18,2) NOT NULL DEFAULT 0, status VARCHAR(32) NOT NULL DEFAULT 'active')"}
async def main():
 conn=await aiomysql.connect(host="127.0.0.1",port=3307,user="wealth",password="wealth_dev",db="wealth_manager",autocommit=True)
 async with conn.cursor() as cur:
  for name,sql in DDL.items(): await cur.execute(sql); print(name,'ok')
  uid=str(uuid.uuid4()); await cur.execute("INSERT IGNORE INTO users(id,username,display_name) VALUES(%s,%s,%s)",(uid,'mysql_probe','MySQL Probe')); await cur.execute("INSERT IGNORE INTO customer_profile(id,user_id,investment_goal,investment_horizon_years,liquidity_preference) VALUES(%s,%s,%s,%s,%s)",(str(uuid.uuid4()),uid,'balanced',3,'medium')); await cur.execute("SELECT u.username,p.investment_goal,p.investment_horizon_years FROM users u JOIN customer_profile p ON p.user_id=u.id WHERE u.id=%s",(uid,)); print('joined=',await cur.fetchone())
 conn.close()
if __name__=='__main__': asyncio.run(main())
