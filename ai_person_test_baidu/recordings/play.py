import pyaudio
import time

def play_pcm(pcm_data, sample_rate=16000, channels=1, sample_width=2):
    """直接播放PCM二进制数据"""
    p = pyaudio.PyAudio()
    
    stream = p.open(
        format=p.get_format_from_width(sample_width),
        channels=channels,
        rate=sample_rate,
        output=True,
        frames_per_buffer=2048  # 与前端缓冲区大小一致
    )
    
    try:
        # 分块播放（适合大文件）
        chunk_size = 512
        for i in range(0, len(pcm_data), chunk_size):
            stream.write(pcm_data[i:i+chunk_size])
        
        # 等待播放完成
        while stream.is_active():
            time.sleep(0.1)
            
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()

# 使用示例
with open('1.pcm', 'rb') as f:
    play_pcm(f.read())