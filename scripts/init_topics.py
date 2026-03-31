"""
初始化所有 Kafka Topics。
使用方式：python scripts/init_topics.py
需在 docker-compose 的 kafka 服務 healthy 後執行。
"""

import os
import sys
import time

from confluent_kafka.admin import AdminClient, NewTopic

BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:29092")

TOPICS = [
    NewTopic("ticker-tasks",     num_partitions=3, replication_factor=1),
    NewTopic("ticker-tasks.DLQ", num_partitions=1, replication_factor=1),
    NewTopic("analysis-results", num_partitions=1, replication_factor=1),
    NewTopic("agent-thoughts",   num_partitions=1, replication_factor=1),
    NewTopic("final-reports",    num_partitions=1, replication_factor=1),
]

TOPIC_RETENTION = {
    "ticker-tasks":      {"retention.ms": str(1  * 60 * 60 * 1000)},   # 1h
    "ticker-tasks.DLQ":  {"retention.ms": str(24 * 60 * 60 * 1000)},   # 24h
    "analysis-results":  {"retention.ms": str(1  * 60 * 60 * 1000)},   # 1h
    "agent-thoughts":    {"retention.ms": str(1  * 60 * 60 * 1000)},   # 1h
    "final-reports":     {"retention.ms": str(24 * 60 * 60 * 1000)},   # 24h
}


def wait_for_kafka(admin: AdminClient, retries: int = 10) -> None:
    for i in range(retries):
        try:
            admin.list_topics(timeout=5)
            print("[init] Kafka is ready.")
            return
        except Exception:
            print(f"[init] Waiting for Kafka... ({i + 1}/{retries})")
            time.sleep(3)
    print("[init] ERROR: Kafka not reachable after retries.", file=sys.stderr)
    sys.exit(1)


def create_topics(admin: AdminClient) -> None:
    existing = set(admin.list_topics(timeout=10).topics.keys())

    to_create = []
    for topic in TOPICS:
        if topic.topic in existing:
            print(f"[init] Already exists, skipping: {topic.topic}")
        else:
            topic.config = TOPIC_RETENTION.get(topic.topic, {})
            to_create.append(topic)

    if not to_create:
        print("[init] All topics already exist.")
        return

    results = admin.create_topics(to_create)
    for topic_name, future in results.items():
        try:
            future.result()
            print(f"[init] Created: {topic_name}")
        except Exception as e:
            print(f"[init] Failed to create {topic_name}: {e}", file=sys.stderr)
            sys.exit(1)


def main() -> None:
    print(f"[init] Connecting to Kafka at {BOOTSTRAP_SERVERS}")
    admin = AdminClient({"bootstrap.servers": BOOTSTRAP_SERVERS})
    wait_for_kafka(admin)
    create_topics(admin)
    print("[init] Done.")


if __name__ == "__main__":
    main()
