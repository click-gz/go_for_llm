import asyncio
import aiohttp
import websockets
import json
import uuid
import logging
import base64
import gzip
import copy
from mianshi import mianshi_chat

# 消息类型定义
MESSAGE_TYPES = {11: "音频服务器响应", 12: "前端服务器响应", 15: "服务器错误消息"}
MESSAGE_TYPE_SPECIFIC_FLAGS = {0: "无序列号", 1: "序列号 > 0", 2: "服务器最后一条消息 (seq < 0)", 3: "序列号 < 0"}
MESSAGE_SERIALIZATION_METHODS = {0: "无序列化", 1: "JSON", 15: "自定义类型"}
MESSAGE_COMPRESSIONS = {0: "无压缩", 1: "gzip", 15: "自定义压缩方法"}

class VolcanoTTS:
    def __init__(self):
        self.appid = ""
        self.token = "-sN_YN"
        self.cluster = ""
        self.voice_type = "_mars_bigtts"
        self.host = "openspeech.bytedance.com"
        self.api_url = f"wss://{self.host}/api/v1/tts/ws_binary"
        self.default_header = bytearray(b'\x11\x10\x11\x00')
        self.request_json = {
            "app": {
                "appid": self.appid,
                "token": self.token,
                "cluster": self.cluster
            },
            "user": {
                "uid": "388808087185088"
            },
            "audio": {
                "voice_type": self.voice_type,
                "encoding": "mp3",
                "speed_ratio": 1.0,
                "volume_ratio": 1.0,
                "pitch_ratio": 1.0,
            },
            "request": {
                "reqid": "",
                "text": "",
                "text_type": "plain",
                "operation": ""
            }
        }
        self.active_connections = {}  # 存储活动的TTS连接

    async def generate_audio(self, text, session_id):
        """使用WebSocket API生成语音"""
        try:
            # 如果已有连接，先关闭
            if session_id in self.active_connections:
                await self.active_connections[session_id].close()
                del self.active_connections[session_id]

            # 构建请求
            reqid = str(uuid.uuid4())
            request_data = copy.deepcopy(self.request_json)
            request_data["request"]["reqid"] = reqid
            request_data["request"]["text"] = text
            request_data["request"]["operation"] = "submit"
            
            # 序列化和压缩请求
            payload_bytes = str.encode(json.dumps(request_data))
            payload_bytes = gzip.compress(payload_bytes)
            
            # 构建完整请求
            full_request = bytearray(self.default_header)
            full_request.extend((len(payload_bytes)).to_bytes(4, 'big'))
            full_request.extend(payload_bytes)
            
            # 收集音频数据
            audio_data = bytearray()
            
            # 建立WebSocket连接
            headers = {"Authorization": f"Bearer; {self.token}"}
            ws = await websockets.connect(
                self.api_url, 
                additional_headers=headers, 
                ping_interval=None
            )
            self.active_connections[session_id] = ws
                
            try:
                await ws.send(full_request)
                
                # 接收响应
                while True:
                    try:
                        response = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        done = self.parse_response(response, audio_data)
                        if done:
                            break
                    except asyncio.TimeoutError:
                        # 检查连接是否仍然活跃
                        if session_id not in self.active_connections:
                            raise asyncio.CancelledError("TTS任务被取消")
            
            finally:
                if session_id in self.active_connections:
                    del self.active_connections[session_id]
                await ws.close()
            
            return bytes(audio_data)
                    
        except asyncio.CancelledError:
            logging.info(f"TTS任务被取消: session_id={session_id}")
            raise
        except Exception as e:
            logging.error(f"TTS生成失败: {str(e)}")
            raise Exception(f"语音生成失败: {str(e)}")

    def parse_response(self, response, audio_buffer):
        """解析服务器响应并提取音频数据"""
        try:
            protocol_version = response[0] >> 4
            header_size = response[0] & 0x0f
            message_type = response[1] >> 4
            message_type_specific_flags = response[1] & 0x0f
            serialization_method = response[2] >> 4
            message_compression = response[2] & 0x0f
            reserved = response[3]
            header_extensions = response[4:header_size*4]
            payload = response[header_size*4:]
            
            if message_type == 0xb:  # 音频服务器响应
                if message_type_specific_flags == 0:
                    return False
                else:
                    sequence_number = int.from_bytes(payload[:4], "big", signed=True)
                    payload_size = int.from_bytes(payload[4:8], "big", signed=False)
                    audio_payload = payload[8:]
                    audio_buffer.extend(audio_payload)
                    return sequence_number < 0
            
            elif message_type == 0xf:
                code = int.from_bytes(payload[:4], "big", signed=False)
                msg_size = int.from_bytes(payload[4:8], "big", signed=False)
                error_msg = payload[8:]
                
                if message_compression == 1:
                    error_msg = gzip.decompress(error_msg)
                    
                error_msg = str(error_msg, "utf-8")
                raise Exception(f"TTS服务错误: {code} - {error_msg}")
            
            elif message_type == 0xc:
                msg_size = int.from_bytes(payload[:4], "big", signed=False)
                payload_data = payload[4:]
                
                if message_compression == 1:
                    payload_data = gzip.decompress(payload_data)
                    
                logging.info(f"前端消息: {payload_data}")
                return False
            
            else:
                logging.warning(f"收到未定义的消息类型: {message_type}")
                return True
                
        except Exception as e:
            logging.error(f"解析响应失败: {str(e)}")
            raise

class ChatHandler:
    def __init__(self):
        self.active_sessions = {}  # 存储活跃的WebSocket连接
        self.mianshi_instances = {}  # 存储每个会话的面试实例
        self.current_tasks = {}  # 存储每个会话的当前处理任务
        self.cancel_events = {}  # 存储每个会话的取消事件
        self.tts_client = VolcanoTTS()  # 共享TTS客户端实例

    async def cleanup_session(self, session_id):
        """清理会话资源"""
        if session_id in self.active_sessions:
            del self.active_sessions[session_id]
        if session_id in self.mianshi_instances:
            del self.mianshi_instances[session_id]
        if session_id in self.current_tasks:
            self.current_tasks[session_id].cancel()
            del self.current_tasks[session_id]
        if session_id in self.cancel_events:
            del self.cancel_events[session_id]
        # 取消该会话的任何TTS任务
        if session_id in self.tts_client.active_connections:
            await self.tts_client.active_connections[session_id].close()
            del self.tts_client.active_connections[session_id]

    async def process_chat_message(self, websocket, data, cancel_event):
        """处理聊天消息的核心逻辑"""
        session_id = id(websocket)
        
        try:
            # 初始化面试实例
            if session_id not in self.mianshi_instances:
                self.mianshi_instances[session_id] = mianshi_chat(
                    'fnbrgwv6_ai_mianshi', 
                    data.get("user_info", "")
                )
            
            mianshi = self.mianshi_instances[session_id]
            
            # 检查是否被取消
            if cancel_event.is_set():
                raise asyncio.CancelledError()
            
            # 生成响应
            mianshi.generate_response(data["text"])
            
            # 检查是否被取消
            if cancel_event.is_set():
                raise asyncio.CancelledError()
            
            question = mianshi.generate_question()
            
            # 检查是否被取消
            if cancel_event.is_set():
                raise asyncio.CancelledError()
            
            # 使用TTS生成语音
            audio_data = await self.tts_client.generate_audio(question, session_id)
            
            # 检查是否被取消
            if cancel_event.is_set():
                raise asyncio.CancelledError()
            
            # 发送完整响应
            response = {
                "type": "response",
                "text": question,
                "audio_data": base64.b64encode(audio_data).decode('utf-8'),
                "mime_type": "audio/mpeg"
            }
            await websocket.send(json.dumps(response))
            
        except asyncio.CancelledError:
            logging.info(f"会话 {session_id} 的任务被取消")
            raise
        except Exception as e:
            logging.error(f"处理聊天消息时出错: {str(e)}")
            await websocket.send(json.dumps({
                "type": "error",
                "text": f"处理请求时出错: {str(e)}"
            }))
            raise
        finally:
            # 清理任务引用
            if session_id in self.current_tasks:
                del self.current_tasks[session_id]
            if session_id in self.cancel_events:
                del self.cancel_events[session_id]

    async def handle_message(self, websocket, message):
        """处理来自客户端的消息"""
        session_id = id(websocket)
        
        try:
            data = json.loads(message)
            
            if data.get("type") == "chat":
                # 取消当前正在处理的任务（如果存在）
                if session_id in self.current_tasks:
                    self.current_tasks[session_id].cancel()
                
                # 创建新的事件对象用于取消
                cancel_event = asyncio.Event()
                self.cancel_events[session_id] = cancel_event
                
                # 创建新任务处理消息
                task = asyncio.create_task(
                    self.process_chat_message(websocket, data, cancel_event)
                )
                self.current_tasks[session_id] = task
                
            elif data.get("type") == "system":
                logging.info(f"系统消息: {data.get('text')}")
                
        except json.JSONDecodeError:
            logging.error("无效的JSON格式消息")
            await websocket.send(json.dumps({
                "type": "error",
                "text": "无效的消息格式"
            }))
        except Exception as e:
            logging.error(f"处理消息时出错: {str(e)}")
            await websocket.send(json.dumps({
                "type": "error",
                "text": str(e)
            }))
            await self.cleanup_session(session_id)

    async def handle_connection(self, websocket):
        """处理WebSocket连接"""
        session_id = id(websocket)
        self.active_sessions[session_id] = websocket
        logging.info(f"新客户端连接, session_id: {session_id}")
        
        try:
            async for message in websocket:
                try:
                    if isinstance(message, str):
                        await self.handle_message(websocket, message)
                    else:
                        logging.warning("收到非文本消息，已忽略")
                except Exception as e:
                    logging.error(f"处理消息时内部错误: {str(e)}")
                    break
                    
        except websockets.exceptions.ConnectionClosed:
            logging.info(f"客户端断开连接, session_id: {session_id}")
        finally:
            await self.cleanup_session(session_id)

async def run_server():
    """启动WebSocket服务器"""
    handler = ChatHandler()
    server = await websockets.serve(
        handler.handle_connection, 
        "0.0.0.0", 
        8080,
        ping_interval=30,
        ping_timeout=60
    )
    logging.info("WebSocket服务器启动，监听 ws://0.0.0.0:8080")
    await server.wait_closed()

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(levelname)s: %(message)s',
        handlers=[
            logging.FileHandler('tts_server.log'),
            logging.StreamHandler()
        ]
    )
    
    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        logging.info("服务器正常关闭")
    except Exception as e:
        logging.error(f"服务器异常终止: {str(e)}")