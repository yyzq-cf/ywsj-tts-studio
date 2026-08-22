# ywsj-tts-studio 🎙️

**字幕转语音工具** — 上传 SRT/VTT 字幕，自动生成多语言语音，支持 YouTube 多音轨制作

## ✨ 功能

- 📝 **字幕转语音** — 上传 SRT/VTT 字幕，自动提取文字内容生成语音
- 🌍 **322 个音色** — 支持 75 种语言（中文/英语/日语/韩语/法语…）
- 🔊 **在线试听** — 每个音色可点击试听语音示例
- ⏱️ **时间轴对齐** — 语音按字幕时间戳精确排列，超长自动加速适配
- 🎚️ **语速/音量调节** — 实时滑块调整
- 🔐 **用户认证** — 登录保护，防暴力破解（5次失败锁定5分钟）
- 📋 **生成历史** — SQLite 数据库记录每次生成
- 🌗 **明暗主题** — 默认亮色，一键切换暗色
- 🐳 **Docker 部署** — 一键启动

## 🚀 快速开始

```bash
# 克隆
git clone https://github.com/yyzq-cf/ywsj-tts-studio.git
cd ywsj-tts-studio

# 配置环境变量（默认 admin/admin123）
cp .env.example .env

# 构建并启动
docker build -t ywsj-tts-studio .
docker run -d --name tts-studio --restart unless-stopped \
  -p 5100:5100 \
  --env-file .env \
  -v $(pwd)/output:/app/output \
  ywsj-tts-studio

# 访问
# http://你的IP:5100
# 默认账号: admin / admin123
```

## 📱 使用流程

1. 上传 SRT/VTT 字幕文件，或在文本框直接输入内容
2. 选择音色（可按语言筛选，点击试听）
3. 调整语速和音量（可选）
4. 点击「开始生成」，等待处理完成
5. 在线试听，下载 MP3

## ⚠️ 端口要求

| 端口 | 用途 |
|:-----|:-----|
| 5100/tcp | Web 界面 |

## 🔧 环境变量

| 变量 | 默认值 | 说明 |
|:-----|:-------|:-----|
| `ADMIN_USERNAME` | admin | 管理员用户名 |
| `ADMIN_PASSWORD` | admin123 | 管理员密码 |
| `SECRET_KEY` | tts-web-secret… | Flask Session 密钥 |

> 密码可在 Web 界面 ⚙️ 设置中修改，存储在 SQLite 数据库中（SHA-256 + salt 加密）

## 🎤 常用中文音色

| 音色ID | 性别 | 风格 |
|:-------|:-----|:-----|
| zh-CN-YunxiNeural | 男声 | 云希（自然，推荐） |
| zh-CN-YunyangNeural | 男声 | 云扬（新闻播报） |
| zh-CN-XiaoxiaoNeural | 女声 | 晓晓（自然，推荐） |
| zh-CN-XiaoyiNeural | 女声 | 晓伊（温柔） |
| zh-CN-YunjianNeural | 男声 | 云健（激情/体育） |
| zh-CN-liaoning-XiaobeiNeural | 女声 | 晓北（东北话） |
| zh-CN-shaanxi-XiaoniNeural | 女声 | 晓妮（陕西话） |
| zh-HK-HiuGaaiNeural | 女声 | 粤语 |
| zh-TW-HsiaoChenNeural | 女声 | 台湾繁体 |

## 🎬 YouTube 多音轨制作

用不同音色生成多语言音频后，用 ffmpeg 合并：

```bash
ffmpeg -i video.mp4 -i chinese.mp3 -i english.mp3 \
  -map 0:v -map 1:a -map 2:a \
  -c:v copy -c:a aac \
  -metadata:s:a:0 language=chi \
  -metadata:s:a:1 language=eng \
  output.mp4
```

## 📐 技术架构

- **后端**: Flask + Flask-SocketIO + edge-tts + pydub
- **数据库**: SQLite（用户管理 + 生成历史）
- **前端**: Bootstrap 5 + Socket.IO + 原生 JS
- **语音引擎**: 微软 Edge TTS（免费，无需 API Key）
- **容器**: Docker + ffmpeg

## 📜 License

MIT
