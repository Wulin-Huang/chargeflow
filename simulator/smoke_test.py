"""ChargeFlow 端到端冒烟：登录 → 启动充电 → 遥测计费 → 停止结算 → AI 助手 → 成本观测。"""
import json
import time
import uuid

import httpx

BASE = "http://127.0.0.1:8000"
client = httpx.Client(base_url=BASE, timeout=30)


def step(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
    if not ok:
        raise SystemExit(1)


# 1. 登录
r = client.post("/auth/login", json={"phone": "13800000001", "password": "customer123"})
step("登录", r.status_code == 200, f"status={r.status_code}")
token = r.json()["token"]
H = {"Authorization": f"Bearer {token}"}

# 2. 站点与桩
r = client.get("/stations", headers=H)
step("获取站点", r.status_code == 200, f"stations={len(r.json())}")
stations = r.json()
sid = stations[0]["id"]
r = client.get(f"/stations/{sid}/piles", headers=H)
piles = [p for p in r.json() if p["status"] == "idle" and p["connector"] == "DC"]
step("获取空闲直流桩", len(piles) > 0, f"idle_dc={len(piles)}")
pile = piles[0]

# 3. 启动充电
rid = f"smoke-{uuid.uuid4().hex[:12]}"
r = client.post("/sessions/start", json={"pile_id": pile["id"], "request_id": rid}, headers=H)
step("启动充电会话", r.status_code == 200, f"session={r.json().get('id')} status={r.json().get('status')}")

# 幂等重试（同 request_id 应返回同一会话）
r2 = client.post("/sessions/start", json={"pile_id": pile["id"], "request_id": rid}, headers=H)
step("幂等重试", r2.json().get("id") == r.json().get("id"))

session_id = r.json()["id"]

# 4. 等充电中 + 遥测累积（kwh_wh 结算时才落库，充电中用账单接口的 cum_wh 判断）
charging = False
b = {}
for i in range(15):
    time.sleep(2)
    r = client.get("/sessions/active", headers=H)
    s = r.json()
    if s and s["status"] == "charging":
        b = client.get(f"/sessions/{session_id}/bill", headers=H).json()
        if b.get("cum_wh", 0) > 0:
            charging = True
            break
step("进入充电且电量累积", charging, f"cum_wh={b.get('cum_wh')}Wh")

# 5. 实时账单预估
step("实时账单预估", not b.get("settled"), f"est={b.get('est_total_cents')}cents cum={b.get('cum_wh')}Wh")

# 6. 停止 → 结算
r = client.post(f"/sessions/{session_id}/stop", headers=H)
step("停止指令下发", r.status_code == 200)

settled = None
for i in range(20):
    time.sleep(1.5)
    r = client.get(f"/sessions/{session_id}/bill", headers=H)
    b = r.json()
    if b.get("settled"):
        settled = b
        break
step("账单结算完成", settled is not None, json.dumps({k: v for k, v in (settled or {}).items() if k != 'segments'}, ensure_ascii=False))

# 7. AI 助手（Function Calling + SSE）
r = client.post(
    "/ai/chat",
    json={"messages": [{"role": "user", "content": "现在哪个站有空闲快充桩？顺便看下我的余额还够充多少度电。"}]},
    headers=H,
)
ok = r.status_code == 200
tool_seen = answer_seen = False
if ok:
    for line in r.text.split("\n"):
        if line.startswith("event: tool"):
            tool_seen = True
        if line.startswith("event: delta"):
            answer_seen = True
step("AI 助手 SSE", ok, f"tool_called={tool_seen} answered={answer_seen}")

# 8. AI 成本观测（operator 登录）
r = client.post("/auth/login", json={"phone": "13800000002", "password": "operator123"})
token2 = r.json()["token"]
r = client.get("/ai/logs", headers={"Authorization": f"Bearer {token2}"})
logs = r.json()
step("AI 成本观测", r.status_code == 200 and logs.get("call_count", 0) >= 2,
     f"calls={logs.get('call_count')} ok_rate={logs.get('ok_rate')} cost=${logs.get('total_cost_usd')}")

print("\nALL SMOKE TESTS PASSED")
