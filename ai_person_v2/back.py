import asyncio
import websockets
import json
import time
import threading
import uuid
import logging
import queue
from openai import OpenAI
from tts_test.baidutts import BaiduTTS
from mianshi import mianshi_chat

# 初始化百度TTS客户端
tts_client = BaiduTTS()
logger = logging.getLogger()

class ChatHandler:
    def __init__(self):
        self.active_sessions = {}
        self.mianshi_instances = {}

    async def handle_message(self, websocket, message):
        """处理来自客户端的消息"""
        try:
            data = json.loads(message)
            
            if data.get("type") == "chat":
                # 处理聊天消息
                session_id = id(websocket)
                if session_id not in self.mianshi_instances:
                    self.mianshi_instances[session_id] = mianshi_chat(
                        'fnbrgwv6_ai_mianshi', 
                        data.get("user_info", "")
                    )
                
                mianshi = self.mianshi_instances[session_id]
                
                # 生成响应
                mianshi.generate_response(data["text"])
                question = mianshi.generate_question()
                
                # 使用TTS生成语音
                tts_client.create_tts_task(question)
                tts_client.query_tts_task()
                
                # 发送完整响应
                await websocket.send(json.dumps({
                    "type": "response",
                    "text": question,
                    "audio_url": tts_client.url
                }))
                
            elif data.get("type") == "system":
                # 处理系统消息
                logger.info(f"系统消息: {data.get('text')}")
                
        except json.JSONDecodeError:
            logger.error("无效的JSON格式消息")
        except Exception as e:
            logger.error(f"处理消息时出错: {str(e)}")
            await websocket.send(json.dumps({
                "type": "error",
                "text": f"处理请求时出错: {str(e)}"
            }))

    async def handle_connection(self, websocket, path):
        """处理WebSocket连接"""
        session_id = id(websocket)
        self.active_sessions[session_id] = websocket
        logger.info(f"新客户端连接, session_id: {session_id}")
        
        try:
            async for message in websocket:
                if isinstance(message, str):
                    await self.handle_message(websocket, message)
                else:
                    logger.warning("收到非文本消息，已忽略")
                    
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"客户端断开连接, session_id: {session_id}")
        finally:
            self.active_sessions.pop(session_id, None)
            self.mianshi_instances.pop(session_id, None)

async def run_server():
    """启动WebSocket服务器"""
    handler = ChatHandler()
    server = await websockets.serve(
        handler.handle_connection, 
        "0.0.0.0", 
        8080
    )
    logger.info("WebSocket服务器启动，监听 ws://0.0.0.0:8080")
    await server.wait_closed()

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(levelname)s: %(message)s'
    )
    asyncio.run(run_server())