# ChargeFlow · 充电站 IoT 运营平台

一个**数据是活的**充电站运营平台：覆盖广东省 21 个地级市的 29 个站点、168 台充电桩模拟器经 MQTT 实时上报遥测，DeepSeek AI 生成车型参数驱动物理仿真、诊断工单根因、撰写运营日报，前端实时大屏秒级刷新。从设备协议、指令可靠性、并发防护到 AI 工程化，覆盖 IoT 平台全栈核心难题。

## 核心亮点

| 难题 | 本项目的解法 |
|------|--------------|
| 设备指令会丢 | MQTT QoS1 至少一次投递 + `request_id` 业务幂等 + 8s 超时补偿重发，三层保障 |
| 设备会异常掉线 | 每桩独立 MQTT 连接注册 LWT 遗嘱 → Broker 推遗嘱 → 网关置 fault → 自动建工单 → AI 定因 |
| 会话状态失控 | 显式状态机：跃迁表集中定义，非法跃迁直接抛错，缺陷在开发期暴露 |
| 并发预约同一桩 | 双层防护：Redis SETNX 锁拦截 + PostgreSQL 部分唯一索引兜底（锁失效也插不进第二条） |
| 计费精度与涨价纠纷 | 分时电价按分段积分计算、DECIMAL 精度、会话启动时锁定价格快照（契约成立时定价） |
| 台区变压器过载 | **有序充电**：水位填充公平份额算法每 6s 重分配，超容动态限功率、回落自动解除（防抖动） |
| 支付闭环断裂 | **钱包体系**：余额风控启动拦截 → 结算事务内扣款 → 欠费标记 → 充值解除，环环闭环 |
| 设备带病运行 | **桩健康度评分（PHM）**：工单压力 + 运行状态 + 实时枪温可解释加权，按分派检修建议 |
| AI 不可用拖垮业务 | 全部 AI 场景可失败：LLM 调用失败静默降级，核心充电/计费链路零依赖 |
| LLM 幻觉编数据 | Function Calling 白名单工具注册表，参数经 pydantic 严格校验，写操作只出参数卡片需用户确认 |
| AI 成本黑箱 | `ai_logs` 表记录每次调用的 token/延迟/成本/成败，管理端可视 |

## 架构

```
┌─────────────────────────────────────────────────────────────┐
│  前端 React 18 + TS + ECharts                                │
│  充电站/充电监控(实时曲线)/订单/AI助手/运营大屏/平台管理        │
└──────────────┬──────────────────────────┬───────────────────┘
        REST (JWT)                 WebSocket (实时推送)
┌──────────────▼──────────────────────────▼───────────────────┐
│  FastAPI 后端                                                │
│  ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐  │
│  │ 认证RBAC │ │ 会话状态机 │ │ 预约(锁) │ │ 分时计费+钱包扣款 │  │
│  └─────────┘ └──────────┘ └──────────┘ └──────────────────┘  │
│  ┌──────────────────┐  ┌────────────────────┐ ┌──────────┐  │
│  │ MQTT 网关(aiomqtt)│  │ 负荷调度器(有序充电) │ │ PHM评分  │  │
│  │ 遥测/事件/指令下发 │  │ 公平份额/限功率      │ │ 健康度   │  │
│  │ 规则引擎→工单      │  └────────────────────┘ └──────────┘  │
│  └────────┬─────────┘  ┌───────────────────────────────┐     │
│           │             │ AI 层(DeepSeek)               │     │
│           │             │ 车型生成/助手FC/诊断/日报      │     │
│           │             │ 统一降级 + ai_logs 观测       │     │
│           │             └───────────────────────────────┘     │
└───────────┼──────────────────────────────────────────────────┘
            │ MQTT (QoS1 + LWT + Retain)
┌───────────▼───────────────────────────┐
│  嵌入式 MQTT Broker (amqtt, :1883)     │◄── 168 桩模拟器
└───────────────────────────────────────┘    (每桩独立连接+遗嘱+混沌模式
                                               + 虚拟车流经真实API循环充电)

存储：SQLite(aiosqlite，可平滑切 PG) + Redis(fakeredis 进程内兜底，
切换只改 REDIS_URL)   实时扇出：Redis pub/sub → 各实例 WS 连接
```

## 技术栈

**后端**：Python 3.11+ · FastAPI · SQLAlchemy 2 (async) · aiomqtt/amqtt · PyJWT · httpx
**前端**：React 18 · TypeScript · Vite · ECharts · React Router
**AI**：DeepSeek Chat/Reasoner · Function Calling · JSON 结构化输出
**测试**：pytest（计费引擎单测）+ 端到端冒烟脚本

## 核心设计详解

### 1. IoT 指令可靠性三层保障

充电启动指令"发了没收到"是真实充电平台的经典问题，本项目用三层叠加解决：

- **QoS1**：MQTT 至少一次投递（可能重复 → 需要幂等）
- **业务幂等**：`charging_sessions.request_id` 唯一索引，同一请求重发返回同一会话
- **补偿重发**：网关维护 `_pending_cmds`，8 秒未收到设备 ACK 自动补发

### 2. LWT 遗嘱与故障处理闭环

每桩独立 MQTT 连接（真实设备形态），连接时注册遗嘱消息。桩异常掉线 → Broker 自动发遗嘱 → 网关将桩置 `fault` → 规则引擎建工单 → DeepSeek 基于真实遥测摘要做根因诊断（结构化输出：severity/root_cause/evidence/action/confidence）。**混沌模式**会随机触发掉线/降功率/枪温异常，持续验证这条链路。

### 3. 显式会话状态机

```
idle → reserved → starting → charging ⇄ paused → occupied → settling → idle
任意 → fault → idle
```

跃迁表集中定义（`core/statemachine.py`），非法跃迁直接告警，杜绝"结算后又收到启动指令"之类的脏状态。

### 4. 分时计费引擎

`core/billing.py`：把充电时长切到电价分段上逐段积分（功率 × 时长 → 分），DECIMAL 精度、分为单位存储避免浮点误差；启动会话时给会话拍电价快照，中途改价不影响进行中的订单（契约成立时定价）。**6 个单元测试覆盖跨峰谷边界、过零点等场景**。

### 5. 预约并发双层防护

第一层 Redis `SETNX` 锁（带 TTL 防死锁）拦截大部分并发；第二层数据库**部分唯一索引**（`WHERE status IN ('pending','active')`）兜底——即使锁实现有 bug 或 Redis 挂了，同一活跃预约也插不进第二条。

### 6. 实时推送管道

遥测逐秒流入但高频事件会压垮前端：网关侧节流（同 key 1s 一条）→ Redis pub/sub 频道 `ws:broadcast` → Broadcaster 扇出给各实例的 WS 连接。多实例水平扩展时业务侧无感。前端 WS 带心跳 + 指数退避重连，登录建立、退出断开。

### 7. AI 工程化：可观测、可降级、不越权

- **白名单工具注册表**（`ai/tools.py`）：LLM 只能调用注册过的 5 个工具（查站点/查详情/推荐时段/解释账单/准备预约），不能"造"工具；参数全部过 pydantic 校验，非法参数回喂模型自纠
- **写操作不越权**：`prepare_reservation` 只生成参数卡片，落库必须走用户确认的 REST 接口
- **统一降级**：车型生成/诊断/日报任一失败都静默降级（默认参数包/无诊断工单/统计模板），核心链路零依赖
- **成本观测**：`ai_logs` 记录每次调用的 token（含缓存命中）/延迟/成本/成败，管理端「AI 调用观测」标签页可视

### 8. AI 驱动"活数据"

车型仿真参数包（电池容量、CC-CV 充电曲线、温度系数、异常概率画像）由 DeepSeek 生成并入库，桩模拟器按参数做物理仿真——**每次生成的曲线、异常率都不同**，平台数据因此不是写死的剧本。管理端可一键"让 AI 再生成 12 个"。

### 9. 有序充电：台区负荷调度

每个站点共用一台配电变压器（台区），容量 = 桩额定总功率 × 70% 负载率（行业标准）。多桩同时满功率会超过台区容量，真实运营商的核心能力就是"有序充电"：

- **水位填充公平份额算法**（`core/loadbalancer.py`）：把台区容量想象成水池，各桩需求是高低不一的柱子——需求低于水位的桩全额满足，剩余容量在高需求桩间平摊。数学性质由单测锁定：**分配之和恒 ≤ 容量、无嫉妒分配（需求小的桩分到的不会多于需求大的桩）**
- **每 6s 一轮**：读实时功率 → 按站聚合 → 超容即经 MQTT QoS1 通道下发 `set_power` 限功率指令（桩侧生效后功率立即封顶）
- **防抖动解除**：需求回落到容量 95% 以下才下发解除指令，避免限功率/解除来回震荡
- **不超容零干预**：负载正常时不发任何指令，不影响充电体验
- 运维大屏「台区有序充电」卡片实时展示各站需求/容量/负载率/削峰量，`power_dispatch` 事件流可见每次调度

### 10. 钱包支付闭环

充电平台的真实营收不靠"订单记账"结束，而是余额风控闭环：

```
启动拦截 → 充电计量 → 结算事务内扣款 → 欠费标记 → 充值解除 → 恢复充电
```

- **启动风控**：余额 ≤ 0 拒绝启动充电（欠费用户必须先充值）
- **结算扣款**：`settle_session` 在同一数据库事务内完成账单落库 + 余额扣减，余额变负即打欠费标记，不留"能充电不付钱"的窗口
- **充值与明细**：`/wallet` 返回余额/欠费状态/最近订单，`/wallet/recharge` 充值；前端充电页有余额卡片（含一键充值与欠费警示）
- **虚拟车流**：`python pile_simulator.py --traffic 6` 启动 6 位虚拟车主经**真实 API** 循环充电（余额不足自动充值），为调度器/结算/大屏提供持续的活负载

### 11. 桩健康度评分（PHM 预测性维护）

168 桩的健康度不是拍脑袋数字，而是**可解释加权模型**（`/admin/pile-health`）：

| 维度 | 扣分 |
|------|------|
| 未结工单 | 每个 -25 |
| 近 7 天已结工单 | 每个 -8 |
| 当前离线 | -20 |
| 故障停机 | -30 |
| 实时枪温 > 45℃ | -15 |

评分映射等级（优/良/关注/维护）与维护建议（健康/持续观察/列入本周巡检/立即派单检修），每个扣分项都展示给运维员——**评分可追溯、可解释**，不是黑箱。管理端「桩健康度」标签页每 30s 自动刷新，按健康度升序展示最需要关注的桩。

## 快速开始

前置：Python 3.11+、Node 18+。

```bash
# 1. 配置 AI（可选——不配则 AI 场景全部降级，核心链路照常跑）
#    chargeflow/.env
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com

# 2. 后端（含内嵌 MQTT Broker，单进程即可跑）
cd backend
pip install -r requirements.txt
python serve.py            # http://127.0.0.1:8000  Windows 下自动用 Selector 事件循环

# 3. 桩模拟器（另开终端）
cd ../simulator
python pile_simulator.py --traffic 6   # 168 桩上线 + 6 位虚拟车主循环充电（混沌模式自动注入异常）

# 4. 前端（开发模式）
cd ../frontend
npm install
npm run dev                # 生产模式：npm run build 后由后端托管 dist

# 5. 端到端冒烟（需后端+模拟器都在跑）
cd ../simulator
python smoke_test.py       # 登录→启动充电→遥测计费→停止结算→AI 助手→成本观测，全链路验证
```

## 演示账号

| 角色 | 手机号 | 密码 | 可见功能 |
|------|--------|------|----------|
| 车主 | 13800000001 | customer123 | 站点/充电监控(钱包+充值)/AI 助手/订单 |
| 运维员 | 13800000002 | operator123 | + 运营大屏(台区负荷调度)/工单/桩健康度 |
| 管理员 | 13800000000 | admin123 | + 电价策略/AI 车型库/AI 观测/日报 |

## API 一览（JWT Bearer 认证）

```
POST /auth/login|register|me          登录/注册/当前用户
GET  /stations, /stations/{id}/piles  站点与桩（桩状态由 MQTT 驱动）
POST /sessions/start                  启动充电（request_id 幂等 + 余额风控拦截）
POST /sessions/{id}/stop, GET /sessions/active, /sessions/{id}/bill
GET  /wallet, POST /wallet/recharge   钱包余额/消费明细/充值（欠费状态）
POST /reservations, GET /reservations/mine, DELETE /reservations/{id}
POST /ai/chat                         AI 助手（Function Calling 多轮）
GET  /ai/logs                         AI 调用观测
GET  /admin/stats, /admin/work-orders, POST /admin/work-orders/{id}/close
GET  /admin/pile-health               桩健康度评分（PHM 预测性维护）
POST /admin/price-policies            发布电价新版本（新会话生效）
GET  /admin/vehicle-profiles, POST /admin/ai/generate-profiles
POST /admin/daily-report, GET /admin/daily-reports
WS   /ws?token=...                    实时事件（遥测/会话/工单/KPI/账单/负荷调度）
GET  /internal/bootstrap              模拟器拉取桩清单（内部接口）
```

## 测试

```bash
cd backend
pytest                      # 14 项单测：计费引擎 6 项 + 公平份额分配 8 项

cd ../simulator
python smoke_test.py        # 端到端：登录→启动充电(含幂等重试)→计费→结算→AI 助手→成本观测
python ws_watch.py          # 调度闭环观察：60s 监听 load_status/power_dispatch/kpi 事件
```

## 目录结构

```
chargeflow/
├── .env                          # DeepSeek 配置
├── backend/
│   ├── serve.py                  # 启动入口（Windows Selector 循环兼容）
│   ├── app/
│   │   ├── main.py               # FastAPI 组装 + lifespan + WS + 静态托管
│   │   ├── config.py             # pydantic-settings 配置
│   │   ├── models.py             # 11 张表
│   │   ├── broker.py             # 内嵌 amqtt Broker
│   │   ├── bus.py                # Redis/fakeredis 总线抽象（同接口切换）
│   │   ├── scheduler.py          # 周期任务（预约巡检 30s / KPI 聚合 5s）
│   │   ├── api/                  # 路由层（auth/stations/sessions/reservations/ai/admin/wallet/internal）
│   │   ├── core/                 # 状态机 / 计费引擎 / 预约锁 / 负荷调度器(有序充电)
│   │   ├── gateway/              # MQTT 网关 / WS 广播 / 遥测缓冲批量写 / 结算扣款
│   │   └── ai/                   # client(重试/降级/成本) / tools(FC 白名单) / profiles / diagnose / report
│   └── tests/test_billing.py, test_loadbalance.py
├── simulator/
│   ├── pile_simulator.py         # 168 桩（广东 21 市） · 每桩独立 MQTT 连接 · LWT · 混沌模式 · set_power 限功率
│   ├── smoke_test.py             # 端到端冒烟
│   ├── ws_watch.py               # WS 事件监听（调度闭环观察）
│   └── start_session.py          # 手动触发一次充电（演示用）
└── frontend/
    └── src/
        ├── store.tsx             # Auth + LiveProvider(WS 重连)
        ├── api.ts                # 401 统一处理
        └── pages/                # Login/Stations/Charging/History/Assistant/Dashboard/Admin
```

## 设计取舍备忘

- **SQLite 起步**：`db_url` 一行切换 PostgreSQL（部分唯一索引已按 PG 方言建好）；预约双层防护就是为分布式场景设计的
- **fakeredis 兜底**：不配 `REDIS_URL` 时进程内模拟，接口完全一致（SETNX/EX/pub-sub），保证演示可跑且行为一致
- **内嵌 Broker**：amqtt 与后端同进程，一条命令跑起全链路；`embed_broker=False` 可切换外置 EMQX/Mosquitto
- **Windows 兼容**：aiomqtt 依赖 `add_reader` 仅支持 SelectorEventLoop，`serve.py` 在 win32 下显式设置事件循环策略
