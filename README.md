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
- 🐳 **Docker 部署** — 一键启动，支持 AMD64 + ARM64 双架构

## 🚀 快速开始

### 方式一：Docker Compose（推荐）

```bash
# 克隆
git clone https://github.com/yyzq-cf/ywsj-tts-studio.git
cd ywsj-tts-studio

# 直接启动（零配置，内置默认值 admin/admin123）
docker-compose up -d

# 访问
# http://你的IP:5100
# 默认账号: admin / admin123
```

如需修改密码，三种方式任选：

```bash
# 方式1: 用 .env 文件覆盖
cp .env.example .env
# 编辑 .env 修改密码
docker-compose up -d

# 方式2: 命令行环境变量覆盖
ADMIN_PASSWORD=*** docker-compose up -d

# 方式3: 启动后在 Web 界面 ⚙️ 设置中修改
```

### 方式二：Docker Run

```bash
docker run -d --name tts-studio --restart unless-stopped \
  -p 5100:5100 \
  -e ADMIN_USERNAME=admin \
  -e ADMIN_PASSWORD=*** \
  -v $(pwd)/output:/app/output \
  ywsj/tts-studio:latest
```

## 🐳 Docker 镜像

镜像已发布至 Docker Hub，支持 **AMD64 (x86_64)** 和 **ARM64** 双平台架构：

```
ywsj/tts-studio:latest
```

| 平台 | 架构 | 适用场景 |
|:-----|:-----|:--------|
| linux/amd64 | x86_64 | VPS、PC服务器、Intel/AMD |
| linux/arm64 | ARM64 | 树莓派、Mac M系列、ARM 云服务器 |

直接拉取即可，Docker 会自动匹配当前平台架构：

```bash
docker pull ywsj/tts-studio:latest
```

> 💡 多架构镜像由 GitHub Actions 自动构建，代码推送到 master 后自动触发

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
- **容器**: Docker + ffmpeg（AMD64 + ARM64 双架构）

## 📜 License

MIT
