import asyncio
from steering_mock.steering import AsyncMessageQueue, BackpressureStrategy
from pro import produce_messages
from cus import consume_messages

async def main():
    # 创建共享的消息队列
    queue = AsyncMessageQueue[str](
        buffer_size=10,
        backpressure_strategy=BackpressureStrategy.DROP_OLDEST,
        enable_metrics=True
    )
    
    # 同时运行生产者和消费者
    await asyncio.gather(
        produce_messages(queue),
        consume_messages(queue)
    )

if __name__ == "__main__":
    asyncio.run(main())