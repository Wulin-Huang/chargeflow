"""挂一个长充电会话，等混沌模式杀掉以验证 LWT→工单→AI 诊断闭环。"""
import time
import uuid

import httpx

BASE = "http://127.0.0.1:8000"
client = httpx.Client(base_url=BASE, timeout=30)

r = client.post("/auth/login", json={"phone": "13800000001", "password": "customer123"})
token = r.json()["token"]
H = {"Authorization": f"Bearer {token}"}

stations = client.get("/stations", headers=H).json()
sid = stations[0]["id"]
piles = client.get(f"/stations/{sid}/piles", headers=H).json()
idle = [p for p in piles if p["status"] == "idle" and p["connector"] == "DC"]
if not idle:
    print("没有空闲桩")
    raise SystemExit(1)
pile = idle[0]
r = client.post(
    "/sessions/start",
    json={"pile_id": pile["id"], "request_id": f"chaos-{uuid.uuid4().hex[:12]}"},
    headers=H,
)
print("session:", r.json())
print(f"桩 {pile['code']} 开始充电，等待混沌触发 LWT……")
