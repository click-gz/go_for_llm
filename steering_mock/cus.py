import asyncio
from steering_mock.steering import AsyncMessageQueue

async def consume_messages(queue: AsyncMessageQueue[str]):
    """消费者协程，从队列获取消息"""
    print("🛒 消费者启动")
    
    try:
        async for message in queue:
            print(f"📥 收到消息: {message}")
            await asyncio.sleep(0.3)  # 模拟处理延迟
    
    except Exception as e:
        print(f"❌ 消费错误: {e}")
    finally:
        print("✅ 消费者完成")
        print("📊 队列指标:", queue.get_metrics().to_dict())

async def main():
    # 创建消息队列
    queue = AsyncMessageQueue[str](
        buffer_size=10,
        backpressure_strategy=BackpressureStrategy.DROP_OLDEST,
        enable_metrics=True
    )
    
    # 启动消费者
    await consume_messages(queue)

if __name__ == "__main__":
    asyncio.run(main())