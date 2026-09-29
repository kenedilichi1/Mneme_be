import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.core.queue import queue as queue_module
from app.core.queue.queue import EXCHANGE_NAME, RabbitMQ


class FakeQueue:
    def __init__(self, name: str) -> None:
        self.name = name
        self.bindings: list[tuple[str, str | None]] = []

    async def bind(self, exchange, routing_key: str | None = None) -> None:
        self.bindings.append((exchange.name, routing_key))


class FakeExchange:
    def __init__(self, name: str) -> None:
        self.name = name
        self.published: list = []

    async def publish(self, message, routing_key: str | None = None) -> None:
        self.published.append((message, routing_key))


class FakeChannel:
    def __init__(self) -> None:
        self.is_closed = False
        self.exchange_declarations: list[str] = []
        self.queue_declarations: list[str] = []
        self.exchanges: dict[str, FakeExchange] = {}
        self.queues: dict[str, FakeQueue] = {}

    async def declare_exchange(self, name, type, durable: bool = False):
        self.exchange_declarations.append(name)
        return self.exchanges.setdefault(name, FakeExchange(name))

    async def declare_queue(self, name, durable: bool = False, arguments=None):
        self.queue_declarations.append(name)
        return self.queues.setdefault(name, FakeQueue(name))

    async def close(self) -> None:
        self.is_closed = True


class FakeConnection:
    def __init__(self) -> None:
        self.is_closed = False
        self.channel_obj = FakeChannel()

    async def channel(self):
        return self.channel_obj

    async def close(self) -> None:
        self.is_closed = True


async def test_publish_declares_exchange_and_queue_once() -> None:
    conn = FakeConnection()
    rabbit = RabbitMQ()
    with patch.object(
        queue_module.aio_pika, "connect_robust", AsyncMock(return_value=conn)
    ):
        await rabbit.publish("documents.process", {"n": 1})
        await rabbit.publish("documents.process", {"n": 2})

    channel = conn.channel_obj
    assert channel.exchange_declarations == [EXCHANGE_NAME]
    assert channel.queue_declarations == ["documents.process"]
    assert len(channel.queues["documents.process"].bindings) == 1
    assert len(channel.exchanges[EXCHANGE_NAME].published) == 2


async def test_concurrent_publish_opens_single_connection() -> None:
    async def slow_connect(url):
        await asyncio.sleep(0.01)
        return FakeConnection()

    connect = AsyncMock(side_effect=slow_connect)
    rabbit = RabbitMQ()
    with patch.object(queue_module.aio_pika, "connect_robust", connect):
        await asyncio.gather(
            rabbit.publish("documents.process", {"n": 1}),
            rabbit.publish("documents.process", {"n": 2}),
        )

    assert connect.await_count == 1


async def test_publish_redeclares_after_new_channel() -> None:
    first = FakeConnection()
    second = FakeConnection()
    connect = AsyncMock(side_effect=[first, second])
    rabbit = RabbitMQ()
    with patch.object(queue_module.aio_pika, "connect_robust", connect):
        await rabbit.publish("documents.process", {"n": 1})
        await rabbit.close()
        await rabbit.publish("documents.process", {"n": 2})

    # The second connection's channel must get fresh declarations.
    assert second.channel_obj.exchange_declarations == [EXCHANGE_NAME]
    assert second.channel_obj.queue_declarations == ["documents.process"]
