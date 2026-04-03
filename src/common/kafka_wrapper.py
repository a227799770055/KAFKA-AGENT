import logging
import os
from typing import Optional, Type, TypeVar

from confluent_kafka import Consumer, Producer
from confluent_kafka import Message as KafkaMessage
from pydantic import BaseModel

logger = logging.getLogger(__name__)

BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:29092")

M = TypeVar("M", bound=BaseModel)


class KafkaProducer:
    """封裝 confluent-kafka Producer，接受 Pydantic Model，序列化後送出。"""

    def __init__(self, bootstrap_servers: str = BOOTSTRAP_SERVERS) -> None:
        self._producer = Producer({
            "bootstrap.servers": bootstrap_servers,
            "acks": "all",  # 確保 Broker 確認收到才算成功
            "partitioner": "random",        # key=None 時隨機分配到各 partition
            "queue.buffering.max.ms": "0",  # 立即送出，避免 sticky batching 把連續訊息黏在同一個 partition
        })

    def produce(
        self,
        topic: str,
        message: BaseModel,
        key: Optional[str] = None,
        partition: Optional[int] = None,
    ) -> None:
        """
        將 Pydantic Model 序列化成 JSON 送到指定 Topic。
        key 用於決定要送到哪個 partition（相同 key 保證同一 partition）。
        partition 直接指定 partition index，優先於 key。
        """
        payload = message.model_dump_json().encode("utf-8")
        encoded_key = key.encode("utf-8") if key else None

        kwargs = dict(
            topic=topic,
            value=payload,
            key=encoded_key,
            on_delivery=self._delivery_callback,
        )
        if partition is not None:
            kwargs["partition"] = partition

        self._producer.produce(**kwargs)
        self._producer.poll(0)  # 觸發非同步回呼，不阻塞

    def flush(self) -> None:
        """等待所有訊息確認送達，程式結束前呼叫。"""
        self._producer.flush()

    def _delivery_callback(self, err: Optional[Exception], msg: KafkaMessage) -> None:
        if err:
            logger.error(
                "Delivery failed",
                extra={"topic": msg.topic(), "error": str(err)},
            )
        else:
            logger.debug(
                "Delivered",
                extra={
                    "topic": msg.topic(),
                    "partition": msg.partition(),
                    "offset": msg.offset(),
                },
            )


class KafkaConsumer:
    """封裝 confluent-kafka Consumer，拉取訊息並反序列化成 Pydantic Model。"""

    def __init__(
        self,
        topics: list[str],
        group_id: str,
        bootstrap_servers: str = BOOTSTRAP_SERVERS,
    ) -> None:
        self._consumer = Consumer({
            "bootstrap.servers": bootstrap_servers,
            "group.id": group_id,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,  # 手動 commit，確保處理完才標記
        })
        self._consumer.subscribe(topics)
        logger.info(f"Subscribed to topics: {topics} as group: {group_id}")

    def poll(
        self,
        model: Type[M],
        timeout: float = 1.0,
    ) -> Optional[M]:
        """
        拉取一筆訊息並反序列化成指定的 Pydantic Model。
        沒有新訊息時回傳 None。
        """
        msg = self._consumer.poll(timeout)

        if msg is None:
            return None

        if msg.error():
            logger.error(f"Consumer error: {msg.error()}")
            return None

        try:
            parsed = model.model_validate_json(msg.value().decode("utf-8"))
            self._consumer.commit(message=msg)  # 處理成功才 commit
            return parsed
        except Exception as e:
            logger.error(
                "Failed to deserialize message",
                extra={"error": str(e), "raw": msg.value()},
            )
            return None

    def close(self) -> None:
        """關閉 Consumer，釋放資源。"""
        self._consumer.close()
