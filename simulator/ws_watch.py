"""监听平台 WS 事件 60s，验证 kpi / load_status / power_dispatch 调度闭环。"""
import asyncio
import json
import sys
import time

import httpx
import websockets

BASE = "http://127.0.0.1:8000"


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=15) as c:
        r = await c.post("/auth/login", json={"phone": "13800000002", "password": "operator123"})
        token = r.json()["token"]

    seen: dict[str, int] = {"kpi": 0, "load_status": 0, "power_dispatch": 0, "other": 0}
    t0 = time.time()
    async with websockets.connect(f"ws://127.0.0.1:8000/ws?token={token}") as ws:
        print("WS 已连接，监听 60s ……", flush=True)
        while time.time() - t0 < 60:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=10)
            except asyncio.TimeoutError:
                continue
            e = json.loads(msg)
            et = e.get("type", "other")
            if et in seen:
                seen[et] += 1
            else:
                seen["other"] += 1
            if et == "kpi" and seen["kpi"] % 3 == 1:
                print(f"[kpi] 在线={e['data']['piles_online']} 充电中={e['data']['charging_sessions']} 总功率={e['data']['total_power_kw']}kW", flush=True)
            elif et == "load_status":
                for st in e["data"]["stations"]:
                    if st["curbed"]:
                        print(f"[load] {st['name']} 需求{st['demand_kw']}/{st['capacity_kw']}kW 削峰{st['shaved_kw']}kW ← 限功率生效", flush=True)
                    elif st["demand_kw"] > 0:
                        print(f"[load] {st['name']} 需求{st['demand_kw']}/{st['capacity_kw']}kW 正常", flush=True)
            elif et == "power_dispatch":
                d = e["data"]
                print(f"[dispatch] 桩{d['pile_id']} 限功率={d['limit_kw']}kW", flush=True)
    print(f"事件统计: {json.dumps(seen, ensure_ascii=False)}", flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # noqa: BLE001
        print(f"失败: {exc}", file=sys.stderr, flush=True)
        raise
