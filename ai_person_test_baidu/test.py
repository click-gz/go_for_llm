import asyncio
import websockets
import json
import time

import websocket
import threading
import uuid
import logging
import pyaudio
import sys
import const
import queue

from openai import OpenAI




logger = logging.getLogger()

# 音频参数
CHUNK = 3200  # 每次读取的音频数据量(160ms)
FORMAT = pyaudio.paInt16  # 16bit PCM格式
CHANNELS = 1  # 单声道
RATE = 16000  # 采样率16kHz

class RealtimeASR:
    def __init__(self):
        self.stream = None
        self.p = None
        self.is_running = False
        self.audio_queue = queue.Queue()  # 添加音频数据队列
        self.asr_thread = None  # ASR发送线程

    def init_microphone(self):
        """初始化麦克风"""
        self.p = pyaudio.PyAudio()
        self.stream = self.p.open(format=FORMAT,
                                 channels=CHANNELS,
                                 rate=RATE,
                                 input=True,
                                 frames_per_buffer=CHUNK)
        logger.info("麦克风初始化完成")

    def close_microphone(self):
        """关闭麦克风"""
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
        if self.p:
            self.p.terminate()
        logger.info("麦克风已关闭")

    def send_start_params(self):
        """发送开始参数帧"""
        req = {
            "type": "START",
            "data": {
                "appid": const.APPID,
                "appkey": const.APPKEY,
                "dev_pid": const.DEV_PID,
                "cuid": str(uuid.uuid1()),
                "sample": RATE,
                "format": "pcm"
            }
        }
        body = json.dumps(req)
        self.ws.send(body, websocket.ABNF.OPCODE_TEXT)
        logger.info("发送START参数: " + body)

    def asr_sender_thread(self):
        """专门负责发送ASR信息的线程"""
        logger.info("ASR发送线程启动")
        try:
            while self.is_running or not self.audio_queue.empty():
                try:
                    # 从队列获取音频数据，设置超时避免线程无法退出
                    data = self.audio_queue.get(timeout=0.1)
                    if data and hasattr(self, 'ws') and self.ws.sock and self.ws.sock.connected:
                        self.ws.send(data, websocket.ABNF.OPCODE_BINARY)
                except queue.Empty:
                    continue
                except Exception as e:
                    logger.error(f"ASR发送错误: {str(e)}")
                    break
        finally:
            logger.info("ASR发送线程结束")

    def send_audio(self, data):
        """将音频数据放入队列，由专用线程发送"""
        self.is_running = True
        logger.info("开始发送音频数据...")
        try:
            # 启动ASR发送线程(如果未启动)
            if not self.asr_thread or not self.asr_thread.is_alive():
                self.asr_thread = threading.Thread(target=self.asr_sender_thread)
                self.asr_thread.daemon = True
                self.asr_thread.start()
            
            # 将数据放入队列
            self.audio_queue.put(data)
        except Exception as e:
            logger.error(f"音频发送错误: {str(e)}")

    def send_finish(self):
        """发送结束帧"""

        req = {"type": "FINISH"}
        if hasattr(self, 'ws') and self.ws.sock and self.ws.sock.connected:
            self.ws.send(json.dumps(req), websocket.ABNF.OPCODE_TEXT)
        logger.info("发送FINISH帧")
        

    def on_open(self, ws):
        """WebSocket连接成功回调"""
        print("asr 连接成功!")
        self.ws = ws
        
        # def run(*args):
        #     self.send_start_params(ws)
        #     self.send_audio(ws)
        #     self.send_finish(ws)
        
        # threading.Thread(target=run).start()

    def on_message(self, ws, message):
        """接收识别结果回调"""
        try:
            result = json.loads(message)
            
            if 'result' in result:
                self.result = result
                print("\r识别结果:", result['result'], end='', flush=True)
                # 将结果通过WebSocket发回前端
                if hasattr(self, 'websocket') and self.websocket:
                    if hasattr(self, 'websocket') and self.websocket:
                        asyncio.run(self.websocket.send(json.dumps({
                            "role": "user",
                            "content": {
                                "result": result['result'],
                                "is_incremental": True  # 标记为增量结果
                            }
                        })))
                    
        except Exception as e:
            logger.error(f"结果解析错误: {str(e)}")

    def on_error(self, ws, error):
        """错误回调"""
        logger.error(f"发生错误: {str(error)}")
        self.is_running = False
        # 清空队列避免线程阻塞
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break

    def on_close(self, ws, a, b):
        """关闭回调"""
        logger.info(f"连接关闭: {ws} --- {a} ----{b}")
        self.is_running = False
        # 清空队列避免线程阻塞
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break

    def run(self):
        """启动实时识别"""
        logging.basicConfig(format='[%(asctime)s] %(message)s', level=logging.INFO)
        
        try:
            # self.init_microphone()
            
            uri = const.URI + "?sn=" + str(uuid.uuid1())
            logger.info(f"连接URI: {uri}")
            
            ws_app = websocket.WebSocketApp(uri,
                                          on_open=self.on_open,
                                          on_message=self.on_message,
                                          on_error=self.on_error,
                                          on_close=self.on_close)
            
            print("请开始说话... (按Ctrl+C停止)")
            ws_app.run_forever()
            
        except KeyboardInterrupt:
            print("\n停止识别")
            self.is_running = False
        finally:
            self.close_microphone()


async def handle_connection(websocket, path):
    print("客户端已连接")
    audio_data = bytearray()  # 用于存储拼接的音频数据
    is_recording = False
    asr = RealtimeASR()
    asr.websocket = websocket  # 保存websocket引用
    is_asr = False
    

    try:
        async for message in websocket:
            if isinstance(message, bytes):
                if is_asr:
                    if not is_recording:
                        asr.send_start_params()
                    else:
                        asr.send_audio(message)
                    # 二进制消息是音频数据
                    audio_data.extend(message)
                    # print(f"收到音频数据块: {len(message)}字节 (总计: {len(audio_data)}字节)")
                    is_recording = True
                
            elif isinstance(message, str):
                try:
                    
                    msg = json.loads(message)
                    if msg.get("status") == "say":
                        is_asr = True
                        asr_thread = threading.Thread(target=asr.run)
                        asr_thread.daemon = True
                        asr_thread.start()
                        print("收到START信号，开始录音")
                        audio_data = bytearray()  # 重置音频缓冲区
                        if msg.get("api_key"):
                            asr.client = OpenAI(
                                base_url='https://qianfan.baidubce.com/v2',
                                api_key=msg.get("api_key")
                            )
                        else:
                            asr.client = None

                        import time
                        time.sleep(0.2)
                    elif msg.get("status") == "over":
                        is_asr = False
                        print("收到STOP信号，停止录音")
                        asr.send_finish()
                        if asr.client:
                            messages = [
                                {"role": "system", "content": "你是一个专业的面试官，请根据用户回答生成新的问题，推进面试流程，只需要返回你的问题即可。"},
                                {"role": "user", "content": f"用户说：{result['result']}"},
                            ]
                            response = asr.client.chat.completions.create(
                                model="deepseek-v3", 
                                messages=messages, 
                                temperature=0.5, 
                                top_p=0.5,
                                extra_body={ 
                                    "penalty_score":1, 
                                }
                            )
                            await websocket.send(json.dumps({
                                "role": "assistant",
                                "content": response.choices[0].message.content
                            }))
                        else:
                            await websocket.send(json.dumps({
                                "role": "assistant",
                                "content": "请提供API key以继续对话"
                            }))
                        # if is_recording and audio_data:
                        #     # 保存音频文件
                        #     timestamp = time.strftime("%Y%m%d_%H%M%S")
                        #     filename = f"recordings/1.pcm"
                        #     import os
                        #     os.makedirs("recordings", exist_ok=True)
                        #     with open(filename, "wb") as f:
                        #         f.write(audio_data)
                        #     print(f"音频已保存到: {filename} (大小: {len(audio_data)}字节)")
                        is_recording = False
                except json.JSONDecodeError:
                    print("收到非JSON格式文本消息:", message)
            
    except websockets.exceptions.ConnectionClosed:
        print("客户端断开连接")
        if is_recording and audio_data:
            # 意外断开时也保存已接收的音频
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filename = f"recordings/1.pcm"
            with open(filename, "wb") as f:
                f.write(audio_data)
            print(f"保存未完成的录音: {filename}")

async def run_server():
    server = await websockets.serve(handle_connection, "localhost", 8080)
    print("WebSocket服务器启动，监听 ws://localhost:8080")
    await server.wait_closed()

asyncio.run(run_server())