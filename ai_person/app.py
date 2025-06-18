import os
import json
from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit
from qianfan import ChatCompletion, QfError
from dotenv import load_dotenv
import threading
import time

# 加载环境变量
load_dotenv()

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

# 从环境变量获取百度千帆API凭证
QIANFAN_AK = os.getenv("QIANFAN_AK", "")
QIANFAN_SK = os.getenv("QIANFAN_SK", "")

# 检查凭证是否存在
if not QIANFAN_AK or not QIANFAN_SK:
    raise ValueError("请设置百度千帆API的AK和SK环境变量")

# 初始化千帆大模型客户端
def create_qianfan_client():
    """创建千帆大模型客户端"""
    return ChatCompletion(
        ak=QIANFAN_AK,
        sk=QIANFAN_SK
    )

# 处理文本生成请求 (WebSocket版本)
@socketio.on('chat')
def handle_chat(message):
    """处理聊天请求"""
    try:
        text = message.get('text', '')
        model = message.get('model', 'ERNIE-Bot')
        
        if not text:
            emit('error', {'message': '缺少text参数'})
            return
        
        # 创建千帆客户端
        client = create_qianfan_client()
        
        # 准备对话消息
        messages = [
            {"role": "user", "content": text}
        ]
        
        # 发送开始处理的消息
        emit('system', {'message': '开始处理请求...'})
        
        # 调用千帆API
        response = client.do(
            model=model,
            messages=messages
        )
        
        # 提取回复内容
        result = response["body"]["result"]
        
        # 模拟流式输出 (实际应用中应使用真正的流式API)
        words = result.split()
        partial_text = ""
        
        for i, word in enumerate(words):
            partial_text += word + " "
            emit('stream', {'text': partial_text})
            time.sleep(0.1)  # 控制输出速度
        
        # 发送完整结果
        emit('response', {
            'text': result,
            'model': model
        })
    
    except QfError as e:
        # 处理千帆API错误
        emit('error', {'message': f"千帆API错误: {str(e)}"})
    except Exception as e:
        # 处理其他错误
        emit('error', {'message': f"服务器内部错误: {str(e)}"})

# 连接事件
@socketio.on('connect')
def handle_connect():
    """处理客户端连接"""
    print('客户端已连接')
    emit('system', {'message': 'WebSocket连接已建立'})

# 断开连接事件
@socketio.on('disconnect')
def handle_disconnect():
    """处理客户端断开连接"""
    print('客户端已断开连接')

if __name__ == '__main__':
    # 启动服务
    print('服务器启动中，访问 http://localhost:5000')
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)    