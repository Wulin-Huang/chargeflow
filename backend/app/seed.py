import logging

from sqlalchemy import func, select

from app.db import SessionLocal, utcnow
from app.models import Pile, PricePolicy, Station, User
from app.security import make_password

logger = logging.getLogger("chargeflow.seed")

DEFAULT_PERIODS = [
    {"start": "00:00", "end": "08:00", "price_cents": 35},
    {"start": "08:00", "end": "17:00", "price_cents": 70},
    {"start": "17:00", "end": "21:00", "price_cents": 110},
    {"start": "21:00", "end": "24:00", "price_cents": 70},
]

DEMO_USERS = [
    ("13800000001", "customer123", "体验用户", "customer", 50000),
    ("13800000002", "operator123", "运维员", "operator", 0),
    ("13800000000", "admin123", "管理员", "admin", 0),
]

# 广东省 21 个地级市全覆盖：广深为一级枢纽（8 桩），珠三角主力城市二级（6 桩），粤东西北三级（4 桩）
# (名称, 地址, 纬度, 经度, 120kW直流数, 60kW直流数, 7kW交流数)
STATIONS = [
    # —— 广州 ——
    ("广州·天河体育中心超充站", "天河路 299 号", 23.1321, 113.3230, 3, 2, 3),
    ("广州·珠江新城枢纽站", "珠江西路 5 号", 23.1180, 113.3210, 3, 2, 3),
    ("广州·白云机场 P4 站", "白云国际机场 P4 停车楼", 23.3920, 113.2990, 3, 2, 3),
    ("广州·大学城综合能源站", "小谷围岛外环东路 232 号", 23.0440, 113.3980, 3, 2, 3),
    # —— 深圳 ——
    ("深圳·南山科技园超充站", "科苑南路 3331 号", 22.5380, 113.9430, 3, 2, 3),
    ("深圳·福田 CBD 枢纽站", "益田路 5033 号", 22.5330, 114.0550, 3, 2, 3),
    ("深圳·宝安国际机场站", "宝安国际机场 T3 停车楼", 22.6270, 113.8090, 3, 2, 3),
    ("深圳·龙华汽车城站", "工业路 68 号", 22.6840, 114.0320, 3, 2, 3),
    # —— 珠三角主力 ——
    ("珠海·香洲情侣路站", "情侣中路 88 号", 22.2700, 113.5850, 2, 2, 2),
    ("佛山·禅城祖庙站", "祖庙路 1 号", 23.0270, 113.1220, 2, 2, 2),
    ("佛山·顺德新城站", "大良新城区德胜东路 3 号", 22.9330, 113.3000, 2, 2, 2),
    ("东莞·南城第一国际站", "元美东路 22 号", 23.0210, 113.7520, 2, 2, 2),
    ("东莞·松山湖科技站", "新城路 1 号", 22.9130, 113.8870, 2, 2, 2),
    ("惠州·惠城江北站", "文明一路 5 号", 23.1110, 114.4160, 2, 2, 2),
    ("中山·东区利和广场站", "中山四路 32 号", 22.5170, 113.4020, 2, 2, 2),
    ("江门·蓬江万达站", "发展大道 223 号", 22.5850, 113.0810, 2, 2, 2),
    ("湛江·赤坎观海站", "观海路 8 号", 21.2660, 110.3650, 2, 2, 2),
    ("汕头·龙湖万象城站", "长平路 95 号", 23.3540, 116.6820, 2, 2, 2),
    # —— 粤东西北 ——
    ("韶关·武江高铁站", "浈江区南韶路 36 号", 24.8100, 113.5970, 1, 1, 2),
    ("肇庆·端州七星岩站", "端州四路 12 号", 23.0530, 112.4650, 1, 1, 2),
    ("茂名·茂南油城站", "油城八路 15 号", 21.6630, 110.9250, 1, 1, 2),
    ("阳江·江城鸳鸯湖站", "东风一路 35 号", 21.8580, 111.9830, 1, 1, 2),
    ("云浮·云城南山站", "南山路 28 号", 22.9150, 112.0440, 1, 1, 2),
    ("清远·清城凤城站", "先锋东路 18 号", 23.6820, 113.0560, 1, 1, 2),
    ("河源·源城新丰江站", "沿江中路 19 号", 23.7440, 114.7010, 1, 1, 2),
    ("梅州·梅江客都站", "梅江四路 50 号", 24.2890, 116.1180, 1, 1, 2),
    ("潮州·湘桥古城站", "潮州大道 11 号", 23.6570, 116.6230, 1, 1, 2),
    ("揭阳·榕城进贤门站", "进贤门大道 23 号", 23.5250, 116.3650, 1, 1, 2),
    ("汕尾·城区滨海站", "湖滨大道 6 号", 22.7860, 115.3750, 1, 1, 2),
]


def station_capacity(n_dc120: int, n_dc60: int, n_ac: int) -> int:
    """台区变压器可用容量：按桩额定总功率 × 70% 负载率设计（行业标准）。"""
    return int((n_dc120 * 120 + n_dc60 * 60 + n_ac * 7) * 0.7)


async def seed() -> None:
    async with SessionLocal() as db:
        if (await db.execute(select(func.count(User.id)))).scalar() == 0:
            for phone, pwd, nick, role, balance in DEMO_USERS:
                h, s = make_password(pwd)
                db.add(User(phone=phone, password_hash=h, salt=s, nickname=nick, role=role, balance_cents=balance))
            logger.info("已初始化演示用户（客户/运维/管理员）")

        if (await db.execute(select(func.count(Station.id)))).scalar() == 0:
            pile_no = 0
            for name, addr, lat, lng, n_dc120, n_dc60, n_ac in STATIONS:
                st = Station(name=name, address=addr, lat=lat, lng=lng, capacity_kw=station_capacity(n_dc120, n_dc60, n_ac))
                db.add(st)
                await db.flush()
                db.add(PricePolicy(station_id=st.id, periods=DEFAULT_PERIODS))
                for connector, power, count in (("DC", 120, n_dc120), ("DC", 60, n_dc60), ("AC", 7, n_ac)):
                    for _ in range(count):
                        pile_no += 1
                        db.add(Pile(station_id=st.id, code=f"P{pile_no:03d}", connector=connector, max_power_kw=power))
            logger.info("已初始化 %d 站 %d 桩（广东 21 市全覆盖）与默认峰谷电价", len(STATIONS), pile_no)
        else:
            # 存量库迁移：按桩额定功率回填台区容量
            stations = (await db.execute(select(Station))).scalars().all()
            for st in stations:
                if not st.capacity_kw:
                    rated = (await db.execute(select(func.sum(Pile.max_power_kw)).where(Pile.station_id == st.id))).scalar() or 0
                    st.capacity_kw = int(rated * 0.7)
                    logger.info("站 %s 回填台区容量 %dkW", st.name, st.capacity_kw)
        await db.commit()
