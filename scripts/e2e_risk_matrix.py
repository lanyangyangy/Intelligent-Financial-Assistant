import asyncio
import json

import httpx

BASE="http://127.0.0.1:8000/api/v1"
ACCOUNTS={"retail_investor_demo":("retail_investor_demo","Demo@2026RetailInvestor"),"high_net_worth_demo":("high_net_worth_demo","Demo@2026HighNetWorth")}

async def main():
 async with httpx.AsyncClient(base_url=BASE,timeout=20) as c:
  out=[]
  for name,(username,password) in ACCOUNTS.items():
   login=await c.post('/auth/login',json={"username":username,"password":password}); data=login.json().get('data') or {}; token=data.get('access_token'); headers={"Authorization":f"Bearer {token}"} if token else {}
   products=(await c.get('/profile/products',headers=headers)).json().get('data',[]) if token else []
   risk=(await c.get('/profile/me/risk-assessment',headers=headers)).json().get('data') if token else None
   out.append({"account":name,"login":login.status_code,"risk":risk,"products":[{"name":p["name"],"risk":p["risk_level"],"type":p.get("target_customer_type")} for p in products]})
  print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=='__main__': asyncio.run(main())
