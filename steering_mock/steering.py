"""
异步消息队列系统的Python实现
基于之前的TypeScript版本移植，提供高效的异步消息处理能力
支持"""

import asyncio
import time
from enum import Enum
from typing import Generic, TypeVar, List, Optional, Callable, Dict, Any, Iterator, AsyncIterator

T = TypeVar('T')

class BackpressureStrategy(Enum):
    """背压策略枚举"""
    DROP_OLDEST = "drop-oldest"
    DROP_NEWEST = "drop-newest"
    BLOCK = "block"
    THROW_ERROR = "throw-error"

class QueueStatus(Enum):
    """队列状态枚举"""
    INITIALIZED = "initialized"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"

class QueueMetrics:
    """队列性能指标"""
    def __init__(self):
        self.enqueue_count = 0
        self.dequeue_count = 0
        self.max_queue_size = 0
        self.total_wait_time = 0.0
        self.avg_latency = 0.0
        self.error_count = 0
        self.current_size = 0
        self.dropped_messages = 0

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典用于展示"""
        return {
            "enqueue_count": self.enqueue_count,
            "dequeue_count": self.dequeue_count,
            "max_queue_size": self.max_queue_size,
            "total_wait_time": self.total_wait_time,
            "avg_latency": self.avg_latency,
            "error_count": self.error_count,
            "current_size": self.current_size,
            "dropped_messages": self.dropped_messages
        }

class CircularBuffer(Generic[T]):
    """循环缓冲区实现"""
    def __init__(self, capacity: int):
        if capacity <= 0:
            raise ValueError("缓冲区容量必须大于0")
        self.capacity = capacity
        self.buffer: List[Optional[T]] = [None] * capacity
        self.head = 0
        self.tail = 0
        self.size = 0

    def enqueue(self, item: T) -> bool:
        """入队操作"""
        if self.is_full():
            return False
        
        self.buffer[self.tail] = item
        self.tail = (self.tail + 1) % self.capacity
        self.size += 1
        return True

    def dequeue(self) -> Optional[T]:
        """出队操作"""
        if self.is_empty():
            return None
        
        item = self.buffer[self.head]
        self.head = (self.head + 1) % self.capacity
        self.size -= 1
        return item

    def is_full(self) -> bool:
        """检查缓冲区是否已满"""
        return self.size == self.capacity

    def is_empty(self) -> bool:
        """检查缓冲区是否为空"""
        return self.size == 0

    def get_size(self) -> int:
        """获取当前大小"""
        return self.size

    def clear(self) -> None:
        """清空缓冲区"""
        self.head = 0
        self.tail = 0
        self.size = 0
        self.buffer = [None] * self.capacity

    def to_list(self) -> List[T]:
        """转换为列表"""
        result: List[T] = []
        index = self.head
        for _ in range(self.size):
            item = self.buffer[index]
            if item is not None:
                result.append(item)
            index = (index + 1) % self.capacity
        return result

class StateManager:
    """状态管理器"""
    def __init__(self, initial_status: QueueStatus = QueueStatus.INITIALIZED):
        self.status = initial_status
        self.error: Optional[Exception] = None

    def get_status(self) -> QueueStatus:
        """获取当前状态"""
        return self.status

    def set_status(self, status: QueueStatus) -> None:
        """设置状态"""
        self.status = status
        if status != QueueStatus.ERROR:
            self.error = None

    def set_error(self, error: Exception) -> None:
        """设置错误状态"""
        self.error = error
        self.status = QueueStatus.ERROR

    def get_error(self) -> Optional[Exception]:
        """获取错误信息"""
        return self.error

    def can_accept_messages(self) -> bool:
        """检查是否可以接受消息"""
        return self.status in [QueueStatus.RUNNING, QueueStatus.INITIALIZED]

    def is_terminated(self) -> bool:
        """检查是否已终止"""
        return self.status in [QueueStatus.COMPLETED, QueueStatus.ERROR]

class AsyncMessageQueue(Generic[T]):
    """异步消息队列实现"""
    def __init__(
        self,
        buffer_size: int = 1000,
        backpressure_strategy: BackpressureStrategy = BackpressureStrategy.DROP_OLDEST,
        enable_metrics: bool = False,
        timeout_ms: int = 0,
        on_error: Optional[Callable[[Exception], None]] = None,
        validate_message: Optional[Callable[[T], bool]] = None
    ):
        self.primary_buffer = CircularBuffer[T](buffer_size)
        self.secondary_buffer = CircularBuffer[T](buffer_size)
        self.state_manager = StateManager(QueueStatus.RUNNING)
        self.backpressure_strategy = backpressure_strategy
        self.enable_metrics = enable_metrics
        self.timeout_ms = timeout_ms
        self.on_error = on_error
        self.validate_message = validate_message
        
        # 异步迭代器状态
        self.read_event = asyncio.Event()
        self.read_result: Optional[T] = None
        self.read_error: Optional[Exception] = None
        self.timeout_task: Optional[asyncio.Task] = None
        
        # 性能指标
        self.metrics = QueueMetrics()
        
        # 锁机制
        self.lock = asyncio.Lock()

    async def enqueue(self, message: T) -> bool:
        """入队操作"""
        async with self.lock:
            # 检查队列状态
            if not self.state_manager.can_accept_messages():
                error = Exception(f"无法入队消息 - 队列状态: {self.state_manager.get_status().value}")
                self._handle_error(error)
                return False

            # 验证消息
            if self.validate_message and not self.validate_message(message):
                error = Exception("消息验证失败")
                self._handle_error(error)
                return False

            # 尝试直接传递给等待的读取者
            if self.read_event.is_set() is False:
                self.read_result = message
                self.read_event.set()
                
                if self.enable_metrics:
                    self._update_metrics_on_dequeue()
                return True

            # 尝试加入主缓冲区
            enqueued = self.primary_buffer.enqueue(message)
            
            # 处理背压
            if not enqueued:
                enqueued = await self._handle_backpressure(message)

            if enqueued and self.enable_metrics:
                self.metrics.enqueue_count += 1
                self._update_max_queue_size()
                self.metrics.current_size = self._get_total_size()

            return enqueued

    async def _handle_backpressure(self, message: T) -> bool:
        """处理背压情况"""
        if self.backpressure_strategy == BackpressureStrategy.DROP_OLDEST:
            # 移除最旧的消息，为新消息腾出空间
            self.primary_buffer.dequeue()
            result = self.primary_buffer.enqueue(message)
            if self.enable_metrics:
                self.metrics.dropped_messages += 1
            return result
            
        elif self.backpressure_strategy == BackpressureStrategy.DROP_NEWEST:
            # 直接丢弃新消息
            if self.enable_metrics:
                self.metrics.dropped_messages += 1
            return False
            
        elif self.backpressure_strategy == BackpressureStrategy.BLOCK:
            # 阻塞直到有空间
            if not self.secondary_buffer.enqueue(message):
                # 如果secondary buffer也满了，转移到primary
                self._swap_buffers()
                return self.secondary_buffer.enqueue(message)
            return True
            
        elif self.backpressure_strategy == BackpressureStrategy.THROW_ERROR:
            error = Exception("队列已满 - 背压策略: 抛出错误")
            self._handle_error(error)
            return False
            
        return False

    def _swap_buffers(self) -> None:
        """交换主缓冲区和副缓冲区"""
        self.primary_buffer, self.secondary_buffer = self.secondary_buffer, self.primary_buffer
        self.secondary_buffer.clear()

    def __aiter__(self) -> AsyncIterator[T]:
        """返回异步迭代器"""
        return self

    async def __anext__(self) -> T:
        """异步迭代器的next方法"""
        # 检查队列状态
        if self.state_manager.is_terminated():
            raise StopAsyncIteration()

        # 检查是否有错误
        error = self.state_manager.get_error()
        if error:
            raise error

        # 记录开始时间用于性能监控
        start_time = time.perf_counter() if self.enable_metrics else 0

        async with self.lock:
            # 尝试从主缓冲区获取消息
            message = self.primary_buffer.dequeue()
            
            # 如果主缓冲区为空，尝试从副缓冲区获取
            if message is None and not self.secondary_buffer.is_empty():
                self._swap_buffers()
                message = self.primary_buffer.dequeue()
                
            # 如果有消息，直接返回
            if message is not None:
                if self.enable_metrics:
                    self._update_metrics_on_dequeue(start_time)
                return message

        # 如果队列已完成，返回结束标志
        if self.state_manager.is_terminated() or self.state_manager.get_status() == QueueStatus.COMPLETED:
            raise StopAsyncIteration()

        # 等待新消息
        try:
            # 设置超时
            if self.timeout_ms > 0:
                wait_task = asyncio.create_task(self.read_event.wait())
                timeout_task = asyncio.create_task(asyncio.sleep(self.timeout_ms / 1000))
                
                done, pending = await asyncio.wait(
                    {wait_task, timeout_task},
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                if timeout_task in done:
                    raise TimeoutError(f"消息队列读取超时，超过 {self.timeout_ms}ms")
                else:
                    timeout_task.cancel()
                    wait_task.cancel()
            else:
                await self.read_event.wait()

            async with self.lock:
                # 检查是否有错误
                if self.read_error:
                    error = self.read_error
                    self.read_error = None
                    raise error

                # 获取结果
                if self.read_result is not None:
                    message = self.read_result
                    self.read_result = None
                    self.read_event.clear()
                    
                    if self.enable_metrics:
                        self._update_metrics_on_dequeue(start_time)
                    return message

                # 检查是否已终止
                if self.state_manager.is_terminated():
                    raise StopAsyncIteration()

                raise Exception("未知错误：没有消息但事件已触发")

        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._handle_error(e)
            raise
    def complete(self) -> None:
        """完成队列，不再接受新消息"""
        if self.state_manager.get_status() != QueueStatus.RUNNING:
            return

        self.state_manager.set_status(QueueStatus.COMPLETED)
        
        # 如果有等待的读取者且队列已空，通知完成
        if not self.read_event.is_set() and self._get_total_size() == 0:
            self.read_event.set()

    def abort(self, error: Optional[Exception] = None) -> None:
        """中断队列并清理资源"""
        if self.state_manager.is_terminated():
            return

        if error:
            self.state_manager.set_error(error)
            self.read_error = error
        else:
            self.state_manager.set_status(QueueStatus.COMPLETED)

        # 通知等待的读取者
        self.read_event.set()
        
        # 清理缓冲区
        self.primary_buffer.clear()
        self.secondary_buffer.clear()

    def get_status(self) -> QueueStatus:
        """获取队列当前状态"""
        return self.state_manager.get_status()

    def get_size(self) -> int:
        """获取队列当前大小"""
        return self._get_total_size()

    def get_metrics(self) -> QueueMetrics:
        """获取性能指标"""
        # 返回指标的副本
        metrics = QueueMetrics()
        metrics.__dict__ = {**self.metrics.__dict__}
        metrics.current_size = self._get_total_size()
        return metrics

    def _get_total_size(self) -> int:
        """获取总大小"""
        return self.primary_buffer.get_size() + self.secondary_buffer.get_size()

    def _update_max_queue_size(self) -> None:
        """更新最大队列大小"""
        current_size = self._get_total_size()
        if current_size > self.metrics.max_queue_size:
            self.metrics.max_queue_size = current_size

    def _update_metrics_on_dequeue(self, start_time: float = 0) -> None:
        """出队时更新指标"""
        self.metrics.dequeue_count += 1
        self.metrics.current_size = self._get_total_size()
        
        if start_time > 0:
            wait_time = (time.perf_counter() - start_time) * 1000  # 转换为毫秒
            self.metrics.total_wait_time += wait_time
            self.metrics.avg_latency = self.metrics.total_wait_time / self.metrics.dequeue_count

    def _handle_error(self, error: Exception) -> None:
        """处理错误"""
        if self.enable_metrics:
            self.metrics.error_count += 1
            
        if self.on_error:
            try:
                self.on_error(error)
            except Exception:
                pass
        else:
            # 如果没有错误处理函数，将错误设置到状态管理器
            self.state_manager.set_error(error)


# 示例使用
async def example_usage():
    """示例用法"""
    print("=== 基础异步迭代示例 ===")
    
    # 创建消息队列
    queue = AsyncMessageQueue[str](
        buffer_size=5,
        enable_metrics=True
    )

    # 模拟生产者
    async def producer():
        print("生产者开始工作...")
        for i in range(1, 6):
            await queue.enqueue(f"消息 {i}")
            print(f"📤 发送: 消息 {i}")
            await asyncio.sleep(0.1)
        queue.complete()
        print("✅ 生产者完成")

    # 模拟消费者
    async def consumer():
        print("消费者开始工作...")
        async for message in queue:
            print(f"📥 接收: {message}")
            await asyncio.sleep(0.05)
        print("✅ 消费者完成")

    # 并行运行生产者和消费者
    await asyncio.gather(producer(), consumer())
    
    # 显示性能指标
    metrics = queue.get_metrics()
    print("📊 性能指标:", metrics.to_dict())


async def backpressure_example():
    """背压控制示例"""
    print("\n=== 背压控制示例 ===")
    
    queue = AsyncMessageQueue[int](
        buffer_size=3,
        backpressure_strategy=BackpressureStrategy.DROP_OLDEST,
        enable_metrics=True
    )

    print("缓冲区容量: 3")
    print("背压策略: 丢弃最旧消息")

    # 快速发送超出容量的消息
    for i in range(1, 7):
        await queue.enqueue(i)
        print(f"📤 尝试发送: {i}")

    print(f"📋 队列状态: 大小 {queue.get_size()}")
    
    # 消费剩余消息
    remaining_messages = []
    async for message in queue:
        print("222", message)
        remaining_messages.append(message)
    queue.complete()

    print(f"📥 剩余消息: {remaining_messages}")
    print(f"🗑️ 丢弃消息数: {queue.get_metrics().dropped_messages}")


if __name__ == "__main__":
    asyncio.run(example_usage())
    asyncio.run(backpressure_example())