# back.py
from flask import Flask, request, jsonify
from baidutts import BaiduTTS
from flask_cors import CORS
import traceback  # 用于获取详细的错误堆栈信息

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# 配置日志
import logging
logging.basicConfig(level=logging.ERROR, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 初始化百度TTS客户端
tts_client = BaiduTTS(
   
)

@app.route('/synthesize', methods=['POST'])
def synthesize():
    text = request.json.get('text')
    if not text:
        return jsonify({'error': '请提供要合成的文本'}), 400
    
    try:
        logger.info(f"开始合成文本: {text[:50]}...")  # 记录请求信息
        
        # 调用百度TTS合成
        tts_client.create_tts_task(text)
        
        import time
        time.sleep(10)
        
        tts_client.query_tts_task()

        
        # 检查返回结果
        if not tts_client.url :
            raise ValueError("百度TTS API未返回有效的音频URL")
            
        logger.info(f"合成成功，返回URL: {tts_client.url}...")
        
        return jsonify({
            'audio_url': tts_client.url,
            'success': True
        })
        
    except Exception as e:
        # 记录详细的错误信息
        error_msg = f"语音合成失败: {str(e)}\n{traceback.format_exc()}"
        logger.error(error_msg)
        
        # 返回友好的错误信息给前端
        return jsonify({
            'error': f'语音合成失败: {str(e)}',
            'success': False
        }), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)