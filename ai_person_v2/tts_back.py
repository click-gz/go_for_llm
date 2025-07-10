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
        
        # WebSocket协议头部定义
        # version: b0001 (4 bits)
        # header size: b0001 (4 bits)
        # message type: b0001 (完整客户端请求) (4bits)
        # message type specific flags: b0000 (无) (4bits)
        # message serialization method: b0001 (JSON) (4 bits)
        # message compression: b0001 (gzip) (4bits)
        # reserved data: 0x00 (1 byte)
        self.default_header = bytearray(b'\x11\x10\x11\x00')
        
        # 请求JSON模板
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

    async def generate_audio(self, text):
        """使用WebSocket API生成语音"""
        try:
            # 构建请求
            reqid = str(uuid.uuid4())
            request_data = copy.deepcopy(self.request_json)
            request_data["request"]["reqid"] = reqid
            request_data["request"]["text"] = text
            request_data["request"]["operation"] = "submit"
            
            # 序列化和压缩请求
            payload_bytes = str.encode(json.dumps(request_data))
            payload_bytes = gzip.compress(payload_bytes)  # 使用gzip压缩
            
            # 构建完整请求
            full_request = bytearray(self.default_header)
            full_request.extend((len(payload_bytes)).to_bytes(4, 'big'))  #  payload大小(4字节)
            full_request.extend(payload_bytes)  # payload内容
            
            # 收集音频数据
            audio_data = bytearray()
            
            # 建立WebSocket连接并发送请求
            headers = {"Authorization": f"Bearer; {self.token}"}
            async with websockets.connect(
                self.api_url, 
                additional_headers=headers, 
                ping_interval=None
            ) as ws:
                await ws.send(full_request)
                
                # 接收响应
                while True:
                    response = await ws.recv()
                    done = self.parse_response(response, audio_data)
                    if done:
                        break
            
            return bytes(audio_data)
                    
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
            
            # 处理音频响应
            if message_type == 0xb:  # 音频服务器响应
                if message_type_specific_flags == 0:  # 无序列号作为ACK
                    return False
                else:
                    # 解析序列号和负载大小
                    sequence_number = int.from_bytes(payload[:4], "big", signed=True)
                    payload_size = int.from_bytes(payload[4:8], "big", signed=False)
                    audio_payload = payload[8:]
                    
                    # 将音频数据添加到缓冲区
                    audio_buffer.extend(audio_payload)
                    
                    # 如果是最后一条消息，返回True
                    return sequence_number < 0
            
            # 处理错误消息
            elif message_type == 0xf:
                code = int.from_bytes(payload[:4], "big", signed=False)
                msg_size = int.from_bytes(payload[4:8], "big", signed=False)
                error_msg = payload[8:]
                
                if message_compression == 1:
                    error_msg = gzip.decompress(error_msg)
                    
                error_msg = str(error_msg, "utf-8")
                raise Exception(f"TTS服务错误: {code} - {error_msg}")
            
            # 处理其他类型消息
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
        self.active_sessions = {}
        self.mianshi_instances = {}

    async def cleanup_session(self, session_id):
        """清理会话资源"""
        if session_id in self.active_sessions:
            del self.active_sessions[session_id]
        if session_id in self.mianshi_instances:
            del self.mianshi_instances[session_id]

    async def handle_message(self, websocket, message):
        """处理来自客户端的消息"""
        try:
            data = json.loads(message)
            session_id = id(websocket)
            
            if data.get("type") == "chat":
                if session_id not in self.mianshi_instances:
                    self.mianshi_instances[session_id] = mianshi_chat(
                        'fnbrgwv6_ai_mianshi', 
                        data.get("user_info", "")
                    )
                
                mianshi = self.mianshi_instances[session_id]
                mianshi.generate_response(data["text"])
                question = mianshi.generate_question()
                
                # 使用火山TTS生成语音
                tts_client = VolcanoTTS()
                audio_data = await tts_client.generate_audio(question)
                
                # 发送完整响应
                response = {
                    "type": "response",
                    "text": question,
                    "audio_data": base64.b64encode(audio_data).decode('utf-8'),
                    "mime_type": "audio/mpeg"
                }
                await websocket.send(json.dumps(response))
                
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
            await self.cleanup_session(id(websocket))

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