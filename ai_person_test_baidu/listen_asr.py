# -*- coding: utf-8 -*-
"""
实时麦克风语音识别Demo
需要安装:
pip install websocket-client pyaudio
"""

import websocket
import threading
import time
import uuid
import json
import logging
import pyaudio
import sys
import const

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

    def send_start_params(self, ws):
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
        ws.send(body, websocket.ABNF.OPCODE_TEXT)
        logger.info("发送START参数: " + body)

    def send_audio(self, ws):
        """从麦克风实时获取并发送音频数据"""
        self.is_running = True
        logger.info("开始发送音频数据...")
        try:
            while self.is_running:
                data = self.stream.read(CHUNK, exception_on_overflow=False)
                ws.send(data, websocket.ABNF.OPCODE_BINARY)
        except Exception as e:
            logger.error(f"音频发送错误: {str(e)}")
        finally:
            logger.info("音频发送结束")

    def send_finish(self, ws):
        """发送结束帧"""
        req = {"type": "FINISH"}
        ws.send(json.dumps(req), websocket.ABNF.OPCODE_TEXT)
        logger.info("发送FINISH帧")

    def on_open(self, ws):
        """WebSocket连接成功回调"""
        def run(*args):
            self.send_start_params(ws)
            self.send_audio(ws)
            self.send_finish(ws)
        
        threading.Thread(target=run).start()

    def on_message(self, ws, message):
        """接收识别结果回调"""
        try:
            result = json.loads(message)
            if 'result' in result:
                print("\r识别结果:", result['result'], end='', flush=True)
        except Exception as e:
            logger.error(f"结果解析错误: {str(e)}")

    def on_error(self, ws, error):
        """错误回调"""
        logger.error(f"发生错误: {str(error)}")
        self.is_running = False

    def on_close(self, ws):
        """关闭回调"""
        logger.info("连接关闭")
        self.is_running = False

    def run(self):
        """启动实时识别"""
        logging.basicConfig(format='[%(asctime)s] %(message)s', level=logging.INFO)
        
        try:
            self.init_microphone()
            
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

if __name__ == "__main__":
    asr = RealtimeASR()
    asr.run()
    # p = pyaudio.PyAudio()
    # stream = p.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK)
    # print("正在录音...按Ctrl+C停止")
    # try:
    #     while True:
    #         data = stream.read(CHUNK)
    #         print(".", data, end="", flush=True)
    # except KeyboardInterrupt:
    #     stream.stop_stream()
    #     stream.close()
    #     p.terminate()