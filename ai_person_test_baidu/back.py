#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
WebSocket服务端，接收前端音频并转发到百度语音识别API
需要安装:
pip install websocket-client websocket-server
"""

import websocket
import threading
import uuid
import json
import logging
import time
import const  # 导入本地配置文件

# 配置日志
logging.basicConfig(
    format='[%(asctime)s] %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger()

class AudioProxyServer:
    def __init__(self):
        self.client_ws = None  # 前端WebSocket连接
        self.baidu_ws = None   # 百度API WebSocket连接
        self.is_running = False
        self.baidu_thread = None
        
    def on_baidu_open(self, ws):
        """连接到百度API成功回调"""
        logger.info("已连接到百度语音识别API")
        self.send_start_params(ws)
        
    def on_baidu_message(self, ws, message):
        """接收百度API的识别结果"""
        print("====: ", message)
        try:
            result = json.loads(message)
            logger.debug(f"百度API返回: {result}")
            
            if 'result' in result:
                logger.info(f"识别结果: {result['result']}")
                # 将结果发送回前端
                if self.client_ws and self.client_ws['handler'] and self.client_ws['client']:
                    self.client_ws['handler'].send_message(
                        self.client_ws['client'], 
                        json.dumps({
                            'type': 'result',
                            'data': result['result']
                        })
                    )
        except Exception as e:
            logger.error(f"结果解析错误: {str(e)}")
            
    def on_baidu_error(self, ws, error):
        """百度API连接错误"""
        logger.error(f"百度API连接错误: {str(error)}")
        self.is_running = False
        
    def on_baidu_close(self, ws, close_status_code, close_msg):
        """百度API连接关闭"""
        logger.info(f"百度API连接已关闭，状态码: {close_status_code}, 消息: {close_msg}")
        self.is_running = False
        
    def send_start_params(self, ws):
        """发送开始参数到百度API"""
        req = {
            "type": "START",
            "data": {
                "appid": const.APPID,
                "appkey": const.APPKEY,
                "dev_pid": const.DEV_PID,
                "cuid": str(uuid.uuid1()),
                "sample": 16000,
                "format": "pcm"
            }
        }
        ws.send(json.dumps(req))
        logger.info("发送START参数到百度API")
        
    def on_client_connect(self, client, server):
        """前端客户端连接回调"""
        logger.info(f"前端客户端已连接: {client['id']}")
        self.client_ws = {
            'client': client,
            'handler': server
        }
        
        # 连接到百度语音识别API
        uri = const.URI + "?sn=" + str(uuid.uuid1())
        logger.info(f"连接到百度API: {uri}")
        
        self.baidu_ws = websocket.WebSocketApp(
            uri,
            on_open=self.on_baidu_open,
            on_message=self.on_baidu_message,
            on_error=self.on_baidu_error,
            on_close=self.on_baidu_close
        )
        
        # 启动百度API连接线程
        self.is_running = True
        self.baidu_thread = threading.Thread(
            target=self.run_baidu_ws,
            daemon=True
        )
        self.baidu_thread.start()
        
    def run_baidu_ws(self):
        """运行百度API的WebSocket连接"""
        try:
            self.baidu_ws.run_forever()
        except Exception as e:
            logger.error(f"百度API线程异常: {str(e)}")
            self.is_running = False
            
    def on_client_message(self, client, server, message):
        """接收前端音频数据"""
        if not self.is_running or not self.baidu_ws:
            return
            
        try:
            # 转发音频数据到百度API
            if isinstance(message, bytes):
                self.baidu_ws.send(message, websocket.ABNF.OPCODE_BINARY)
            else:
                # 处理可能的文本消息，如停止命令
                data = json.loads(message)
                if data.get('type') == 'stop':
                    self.baidu_ws.send(json.dumps({"type": "FINISH"}))
        except Exception as e:
            logger.error(f"音频转发错误: {str(e)}")
            
    def on_client_disconnect(self, client, server):
        """前端客户端断开连接"""
        logger.info(f"前端客户端已断开: {client['id']}")
        self.cleanup_baidu_connection()
        
    def cleanup_baidu_connection(self):
        """清理百度API连接资源"""
        if self.baidu_ws:
            try:
                # 发送结束命令
                self.baidu_ws.send(json.dumps({"type": "FINISH"}))
                time.sleep(0.5)  # 等待服务器处理
                self.baidu_ws.close()
            except Exception as e:
                logger.error(f"关闭百度API连接错误: {str(e)}")
            finally:
                self.baidu_ws = None
                self.is_running = False
                
    def run(self):
        """启动WebSocket服务器"""
        try:
            from websocket_server import WebsocketServer
        except ImportError:
            logger.error("请安装websocket-server库: pip install websocket-server")
            return
            
        # 检查百度API配置
        if const.APPID == "你的百度语音识别APPID":
            logger.error("请在const.py中配置你的百度语音识别API密钥")
            return
            
        logger.info(f"启动WebSocket服务器")
        server = WebsocketServer(
            host="localhost",
            port=8765,
            loglevel=logging.INFO
        )
        
        server.set_fn_new_client(self.on_client_connect)
        server.set_fn_client_left(self.on_client_disconnect)
        server.set_fn_message_received(self.on_client_message)
        
        try:
            server.run_forever()
        except KeyboardInterrupt:
            logger.info("服务器被用户中断")
        finally:
            self.cleanup_baidu_connection()
            logger.info("服务器已停止")

if __name__ == "__main__":
    server = AudioProxyServer()
    server.run()