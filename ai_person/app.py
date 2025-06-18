import os
import json
import asyncio
import websockets
from openai import OpenAI
from dotenv import load_dotenv
from typing import Dict, Any, Optional

# 加载环境变量
load_dotenv()

# 获取配置
QIANFAN_AK = os.getenv("QIANFAN_AK", "")
QIANFAN_SK = os.getenv("QIANFAN_SK", "")
SERVER_HOST = os.getenv("SERVER_HOST", "localhost")
SERVER_PORT = int(os.getenv("SERVER_PORT", 5000))

# 检查凭证是否存在
if not QIANFAN_AK or not QIANFAN_SK:
    print("警告: 未设置百度千帆API的AK和SK环境变量，将使用模拟回复")

# 初始化千帆大模型客户端
def create_qianfan_client() -> OpenAI:
    """创建千帆大模型客户端"""
    return OpenAI(
        base_url="https://qianfan.baidubce.com/api/v2",
        api_key=QIANFAN_AK,
    )

# 模拟流式回复生成器
async def generate_mock_stream_response(prompt: str) -> str:
    """模拟流式回复生成器，用于在没有API凭证时测试"""
    mock_response = (
        "感谢您的提问。这个问题涉及到人工智能和自然语言处理的核心技术。"
        "百度的ERNIE模型在多个基准测试中表现优异，能够理解复杂的语义并生成高质量的回复。"
        "对于实时对话场景，我们建议使用流式API以获得更低的延迟体验。"
        "您可以通过配置temperature参数来控制回复的创造性，较低的值会使回复更加确定性，"
        "而较高的值则会使回复更加多样化和创造性。"
    )
    
    # 按词分块生成回复
    words = mock_response.split("。")
    print("模拟流式回复生成器已启动: ", words)
    partial_text = ""
    
    for word in words:
        partial_text += word + " "
        yield partial_text
        print(f"模拟输出: {partial_text.strip()}")
        await asyncio.sleep(1)  # 控制输出速度
    

# 处理WebSocket连接
async def handle_websocket(websocket):  # 移除 path 参数
    """处理WebSocket连接"""
    print("客户端已连接")
    
    try:
        # 发送连接成功消息
        await websocket.send(json.dumps({
            "type": "system",
            "message": "WebSocket连接已建立"
        }))
        print("WebSocket连接已建立")
        
        # 持续接收消息
        async for message in websocket:
            try:
                # 解析消息
                data = json.loads(message)
                print(f"收到消息: {data}")
                
                if data.get("type") != "chat":
                    continue
                    
                text = data.get("text", "")
                model = data.get("model", "ERNIE-Bot")
                
                if not text:
                    await websocket.send(json.dumps({
                        "type": "error",
                        "message": "缺少text参数"
                    }))
                    continue
                    
                # 发送开始处理的消息
                await websocket.send(json.dumps({
                    "type": "system",
                    "message": "开始处理请求..."
                }))
                
                # 检查是否有API凭证
                if QIANFAN_AK and QIANFAN_SK:
                    # 使用真实API
                    client = create_qianfan_client()
                    
                    # 准备对话消息
                    messages = [{"role": "user", "content": text}]
                    
                    # 调用API (实际应用中应使用流式API)
                    try:
                        response = client.chat.completions.create(
                            model=model,
                            messages=messages,
                            temperature=0.7,
                            stream=False  # 非流式请求，后续会模拟流式输出
                        )
                        
                        result = response.choices[0].message.content
                        
                        # 模拟流式输出
                        words = result.split()
                        partial_text = ""
                        
                        for word in words:
                            partial_text += word + " "
                            await websocket.send(json.dumps({
                                "type": "stream",
                                "text": partial_text
                            }))
                            await asyncio.sleep(0.1)  # 控制输出速度
                            
                        # 发送完整结果
                        await websocket.send(json.dumps({
                            "type": "response",
                            "text": result,
                            "model": model
                        }))
                        
                    except Exception as e:
                        await websocket.send(json.dumps({
                            "type": "error",
                            "message": f"API调用错误: {str(e)}"
                        }))
                else:
                    # 使用模拟回复
                    print("使用模拟回复")
                    async for partial_text in generate_mock_stream_response(text):
                        await websocket.send(json.dumps({
                            "type": "stream",
                            "text": partial_text
                        }))
                    await websocket.send(json.dumps({
                            "type": "stream_end",
                            "text": "[done]"
                        }))
                    # 发送完整结果
                    await websocket.send(json.dumps({
                        "type": "response",
                        "text": "这是一个模拟回复，实际使用时请配置百度千帆API凭证。",
                        "model": model
                    }))
                    
            except json.JSONDecodeError:
                await websocket.send(json.dumps({
                    "type": "error",
                    "message": "无效的JSON格式"
                }))
            except Exception as e:
                await websocket.send(json.dumps({
                    "type": "error",
                    "message": f"处理消息时出错: {str(e)}"
                }))
                
    except websockets.exceptions.ConnectionClosedOK:
        print("客户端已断开连接")
    except Exception as e:
        print(f"WebSocket连接错误: {e}")
    finally:
        print("WebSocket连接已关闭")

# 启动ASGI服务器
async def main() -> None:
    """启动WebSocket服务器"""
    print(f"服务器启动中，访问 ws://{SERVER_HOST}:{SERVER_PORT}")
    
    # 创建WebSocket服务器
    async with websockets.serve(
        handle_websocket, 
        SERVER_HOST, 
        SERVER_PORT,
        ping_interval=30,  # 30秒发送一次ping
        ping_timeout=10    # 10秒超时
    ):
        await asyncio.Future()  # 保持服务器运行

if __name__ == "__main__":
    # 启动异步事件循环
    asyncio.run(main())