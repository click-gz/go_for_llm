import asyncio
import websockets
import json
import time

async def handle_connection(websocket, path):
    print("客户端已连接")
    counter = 0
    
    try:
        while True:
            counter += 1
            message = {
                "timestamp": time.time(),
                "count": counter,
                "message": f"这是第 {counter} 条测试消息"
            }
            
            await websocket.send(json.dumps(message))
            print(f"已发送: {message}")
            await asyncio.sleep(1)  # 1秒间隔
            
    except websockets.exceptions.ConnectionClosed:
        print("客户端断开连接")

start_server = websockets.serve(handle_connection, "localhost", 8080)

print("WebSocket服务器启动，监听 ws://localhost:8080")
asyncio.get_event_loop().run_until_complete(start_server)
asyncio.get_event_loop().run_forever()