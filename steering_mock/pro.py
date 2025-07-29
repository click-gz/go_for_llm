import asyncio
from steering_mock.steering import AsyncMessageQueue

async def produce_messages(queue: AsyncMessageQueue[str]):
    """生产者协程，向队列发送消息"""
    print("🚀 生产者启动")
    
    messages = [
        "第一条消息1",
        "第二条消息2",
        "第三条消息3",
        "第四条消息4",
        "最后一条消息"
    ]
    
    for msg in messages:
        await asyncio.sleep(0.5)  # 模拟处理延迟
        await queue.enqueue(msg)
        print(f"📤 已发送: {msg}")
    
    # 标记队列完成
    queue.complete()
    print("✅ 生产者完成")

async def main():
    # 创建消息队列
    queue = AsyncMessageQueue[str](
        buffer_size=10,
        backpressure_strategy=BackpressureStrategy.DROP_OLDEST,
        enable_metrics=True
    )
    
    # 启动生产者
    await produce_messages(queue)

if __name__ == "__main__":
    asyncio.run(main())