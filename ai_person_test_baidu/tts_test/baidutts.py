import requests
import json
from playsound import playsound
import time

class BaiduTTS:
    """
    百度语音合成API工具类
    封装了语音合成创建和查询功能
    """
    
    def __init__(self, api_key, secret_key):
        """
        初始化百度TTS客户端
        
        :param api_key: 百度API Key
        :param secret_key: 百度Secret Key
        """
        self.API_KEY = api_key
        self.SECRET_KEY = secret_key
    
    def get_access_token(self):
        """
        获取百度API访问令牌
        
        :return: access_token字符串
        """
        url = "https://aip.baidubce.com/oauth/2.0/token"
        params = {
            "grant_type": "client_credentials",
            "client_id": self.API_KEY,
            "client_secret": self.SECRET_KEY
        }
        response = requests.post(url, params=params)
        return str(response.json().get("access_token"))
    
    def create_tts_task(self, text, format="wav", voice=1, lang="zh", 
                       speed=8, pitch=5, volume=5, enable_subtitle=2, 
                       paragraph_break=5000):
        """
        创建语音合成任务
        
        :param text: 待合成的文本
        :param format: 音频格式，默认wav
        :param voice: 音库，默认1
        :param lang: 语言，默认zh
        :param speed: 语速，默认8
        :param pitch: 音调，默认5
        :param volume: 音量，默认5
        :param enable_subtitle: 是否开启字幕时间戳，默认2
        :param paragraph_break: 段落间隔(毫秒)，默认5000
        :return: API响应结果
        """
        url = "https://aip.baidubce.com/rpc/2.0/tts/v1/create?access_token=" + self.get_access_token()
        
        payload = json.dumps({
            "text": text,
            "format": format,
            "voice": voice,
            "lang": lang,
            "speed": speed,
            "pitch": pitch,
            "volume": volume,
            "enable_subtitle": enable_subtitle,
            "break": paragraph_break
        })
        
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        
        response = requests.request("POST", url, headers=headers, data=payload)
        print(response.json())
        self.task_id = response.json().get('task_id', "")
    
    def query_tts_task(self):
        """
        查询语音合成任务状态
        """
        url = "https://aip.baidubce.com/rpc/2.0/tts/v1/query?access_token=" + self.get_access_token()
        
        payload = json.dumps({
            "task_ids": [self.task_id]
        })
        
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        
        response = requests.request("POST", url, headers=headers, data=payload)
        while(response.json().get("tasks_info")[0].get("task_result") == "Running"):
            response = requests.request("POST", url, headers=headers, data=payload)
            print(response.json())
            time.sleep(1)
        self.url = response.json().get("tasks_info")[0].get("task_result").get("speech_url")

        # response = requests.get(url)
        # with open("temp_audio.wav", "wb") as f:
        #     f.write(response.content)
        
        # # 播放音频
        # playsound("temp_audio.wav")
        



# 使用示例
if __name__ == '__main__':
    # 初始化客户端
   
    # 创建语音合成任务
    tts_client.create_tts_task(text="欢迎使用百度语音技术")
    import time
    time.sleep(10)

    tts_client.query_tts_task()