import pandas as pd
import numpy as np
import re
import requests
import json
import random
import time
import subprocess
import sys
import os

from bns import get_bns_server

class mianshi_chat:
    def __init__(self, model_url, info):
        self.model_url = model_url
        self.info = info
        self.b = True
        self.historys = [{"role": "system", "content": "你是一个经验丰富且专业的面试官。"}, {"role": "user", "content": "请开始一场面试，我的简历信息是：{} ，我的面试岗位是：{} 。".format(info, '')}]
    
    def generate_response(self, input_text):
        try:
            if self.b:
                self.b=False
            else:
                self.historys.append({"role": "user", "content": input_text})
        except Exception as e:
            result = ""
            print(e)
    
    def generate_question(self):
        """
        联网意图请求
        """
        url = "https://qianfan.baidubce.com/v2/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer bce-v3/ALTAK-jbx93vI0G3vWf6w0GD5Fu/cd5fb861f893943ac90762fbfb7ab5723998050f"
        }
    
        data = {
            "model": self.model_url, 
            "messages": self.historys
        }
        print(json.dumps(data, ensure_ascii=False))
        response = requests.post(url, headers=headers, json=data)
        print(data, "\n", response.json())
        self.historys.append({"role": "assistant", "content": response.json().get("choices", [])[0].get("message", {}).get("content")})
        return response.json().get("choices", [])[0].get("message", {}).get("content")