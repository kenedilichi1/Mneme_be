import asyncio
import json
import logging
import aio_pika

from app.core.config import settings

logger = logging.getLogger(__name__)

EXCHANGE_NAME = "documents"
# One queue per delay so a short delay never waits behind a longer one
RETRY_DELAYS_SECONDS = (30, 120, 600)


def retry_queue_name(queue_name: str, attempt: int) -> str:
    return f"{queue_name}.retry.{attempt}"


def dead_letter_queue_name(queue_name: str) -> str:
    return f"{queue_name}.dead"


async def declare_processing_queues(
    channel: aio_pika.abc.AbstractChannel, queue_name: str
) -> aio_pika.abc.AbstractQueue:
    """Declare the main queue plus its delayed-retry and dead-letter queues."""
    exchange = await channel.declare_exchange(
        EXCHANGE_NAME, aio_pika.ExchangeType.DIRECT, durable=True
    )
    queue = await channel.declare_queue(queue_name, durable=True)
    await queue.bind(exchange, routing_key=queue_name)

    # Expired messages are dead-lettered back to the main queue
    for attempt, delay in enumerate(RETRY_DELAYS_SECONDS, start=1):
        await channel.declare_queue(
            retry_queue_name(queue_name, attempt),
            durable=True,
            arguments={
                "x-message-ttl": delay * 1000,
                "x-dead-letter-exchange": EXCHANGE_NAME,
                "x-dead-letter-routing-key": queue_name,
            },
        )

    await channel.declare_queue(dead_letter_queue_name(queue_name), durable=True)
    return queue


class RabbitMQ:
    def __init__(self) -> None:
        self.connection: aio_pika.abc.AbstractRobustConnection | None = None
        self.channel: aio_pika.abc.AbstractChannel | None = None
        self._connect_lock = asyncio.Lock()
        self._exchange: aio_pika.abc.AbstractExchange | None = None
        self._declared_queues: set[str] = set()

    async def connect(self) -> None:
        # Serialize: two concurrent publishes used to race the None-check and
        # open two connections (MNE-29).
        async with self._connect_lock:
            if self.connection is None or self.connection.is_closed:
                self.connection = await aio_pika.connect_robust(settings.RABBITMQ_URL)
                self.channel = await self.connection.channel()
                # Cached declarations belong to the old channel.
                self._exchange = None
                self._declared_queues.clear()
                logger.info("Connected to RabbitMQ")

    async def _get_exchange(self) -> aio_pika.abc.AbstractExchange:
        assert self.channel is not None
        if self._exchange is None:
            self._exchange = await self.channel.declare_exchange(
                EXCHANGE_NAME, aio_pika.ExchangeType.DIRECT, durable=True
            )
        return self._exchange

    async def publish(self, queue_name: str, message: dict) -> None:
        await self.connect()
        assert self.channel is not None

        exchange = await self._get_exchange()
        if queue_name not in self._declared_queues:
            queue = await self.channel.declare_queue(queue_name, durable=True)
            await queue.bind(exchange, routing_key=queue_name)
            self._declared_queues.add(queue_name)

        body = aio_pika.Message(
            body=json.dumps(message).encode("utf-8"),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )
        await exchange.publish(body, routing_key=queue_name)

    async def close(self) -> None:
        if self.channel and not self.channel.is_closed:
            await self.channel.close()
            self.channel = None
        if self.connection and not self.connection.is_closed:
            await self.connection.close()
            self.connection = None
            logger.info("RabbitMQ connection closed")
        self._exchange = None
        self._declared_queues.clear()


rabbitmq = RabbitMQ()

