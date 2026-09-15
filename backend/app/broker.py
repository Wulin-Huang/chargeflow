"""嵌入式 MQTT Broker（amqtt，协议级实现 QoS/Retain/LWT）。

生产环境换 EMQX：EMBED_BROKER=false 且 MQTT_HOST 指向 EMQX，客户端代码零改动。
"""

import logging

from amqtt.broker import Broker

from app.config import settings

logger = logging.getLogger("chargeflow.broker")


async def start_embedded_broker() -> Broker | None:
    if not settings.embed_broker:
        return None
    config = {
        "listeners": {"default": {"type": "tcp", "bind": f"0.0.0.0:{settings.mqtt_port}"}},
        "auth": {"allow-anonymous": True},
    }
    broker = Broker(config)
    await broker.start()
    logger.info("嵌入式 MQTT Broker 已启动 :%s", settings.mqtt_port)
    return broker


async def stop_embedded_broker(broker: Broker | None) -> None:
    if broker is not None:
        await broker.shutdown()
