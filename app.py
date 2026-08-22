#!/usr/bin/env python3
"""
字幕转语音 Web 界面
SRT/VTT → 语音 (edge-tts) → 下载

核心逻辑：
- 只提取字幕文字内容生成语音，跳过序号和时间戳
- TTS 语音完整保留，不截断
- 按字幕时间戳的 start 时间放置语音
- 字幕之间有间隔时自动补静音
- 如果语音比字幕间隔长，后面语音顺延，不重叠
"""
import os
import re
import uuid
import json
import asyncio
import threading
import time
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for, session
from flask_socketio import SocketIO
import edge_tts

try:
    from pydub import AudioSegment
    HAS_PYDUB = True
except ImportError:
    HAS_PYDUB = False

app = Flask(__name__)
app.config["SECRET_KEY"] = "tts-web-secret"
app.config["UPLOAD_FOLDER"] = "uploads"
app.config["OUTPUT_FOLDER"] = "output"


def normalize_pct(val):
    """Ensure rate/volume is like '+0%' not '0%'"""
    val = val.strip()
    if not val.startswith('+') and not val.startswith('-'):
        val = '+' + val
    if not val.endswith('%'):
        val = val + '%'
    return val


socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# 任务状态
tasks = {}


# ============ 字幕解析 ============

def time_to_ms(time_str):
    time_str = time_str.replace(",", ".")
    h, m, s = time_str.split(":")
    return int(float(h) * 3600000 + float(m) * 60000 + float(s) * 1000)


def ms_to_time(ms):
    h = ms // 3600000
    m = (ms % 3600000) // 60000
    s = (ms % 60000) // 1000
    return f"{h:02d}:{m:02d}:{s:02d}.{(ms % 1000):03d}"


def parse_srt(content):
    """解析 SRT，只提取文字和时间戳，跳过序号"""
    blocks = re.split(r'\n\s*\n', content.strip())
    subs = []
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 2:
            continue
        # 跳过序号行
        idx = 0
        if re.match(r'^\d+$', lines[0].strip()):
            idx = 1
        if idx >= len(lines):
            continue
        # 解析时间戳
        time_match = re.match(
            r'(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})',
            lines[idx].strip()
        )
        if not time_match:
            continue
        start = time_to_ms(time_match.group(1))
        end = time_to_ms(time_match.group(2))
        # 提取纯文字内容（跳过序号和时间戳）
        text = ' '.join(lines[idx + 1:]).strip()
        text = re.sub(r'<[^>]+>', '', text)  # 移除HTML标签
        if text:
            subs.append({'start': start, 'end': end, 'text': text})
    return subs


def parse_vtt(content):
    content = re.sub(r'^WEBVTT.*?\n\n', '', content, flags=re.DOTALL)
    return parse_srt(content)


# ============ 语音生成 ============

async def tts_one(text, path, voice, rate, volume):
    communicate = edge_tts.Communicate(text, voice, rate=rate, volume=volume)
    await communicate.save(path)


def run_tts(task_id, filepath, voice, rate, volume, text_input):
    """后台线程：生成语音"""
    task = tasks[task_id]

    try:
        # 读取字幕或纯文本
        if filepath:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            ext = Path(filepath).suffix.lower()
            if ext == '.vtt' or content.strip().startswith('WEBVTT'):
                subs = parse_vtt(content)
            else:
                subs = parse_srt(content)
        elif text_input:
            lines = [l.strip() for l in text_input.strip().split('\n') if l.strip()]
            subs = []
            current_ms = 0
            for line in lines:
                duration = max(len(line) * 120, 2000)
                subs.append({'start': current_ms, 'end': current_ms + duration, 'text': line})
                current_ms += duration + 500
        else:
            task['status'] = 'error'
            task['error'] = '没有输入内容'
            return

        if not subs:
            task['status'] = 'error'
            task['error'] = '未解析到字幕内容'
            return

        total = len(subs)
        task['total'] = total
        task['status'] = 'generating'

        if HAS_PYDUB:
            # ============ 时间轴对齐模式 ============
            # 策略：语音完整保留不截断，按时间戳间隔排列
            # - 每条语音放在字幕的 start 时间
            # - 如果语音比字幕间隔长，下一条顺延，不重叠
            # - 字幕之间有间隔时补静音

            # 先收集所有语音片段
            audio_clips = []
            for i, sub in enumerate(subs):
                if task.get('cancelled'):
                    task['status'] = 'cancelled'
                    return

                clip_path = f"/tmp/tts_{task_id}_{i}.mp3"
                asyncio.run(tts_one(sub['text'], clip_path, voice, rate, volume))
                audio = AudioSegment.from_file(clip_path)
                if os.path.exists(clip_path):
                    os.remove(clip_path)

                actual_dur = len(audio)
                audio_clips.append({
                    'audio': audio,
                    'start_ts': sub['start'],  # 字幕时间戳
                    'end_ts': sub['end'],
                    'actual_dur': actual_dur,
                    'text': sub['text']
                })

                task['progress'] = int((i + 1) / total * 100)
                task['current'] = i + 1
                socketio.emit('progress', {
                    'task': task_id,
                    'progress': task['progress'],
                    'current': i + 1,
                    'total': total,
                    'text': sub['text'][:40]
                })

            # 计算总时长：考虑语音超长顺延
            total_duration = 0
            cursor = 0  # 当前音频推进的位置
            placed = []  # (position, audio, duration)

            for i, clip in enumerate(audio_clips):
                # 目标位置：取字幕 start 和 cursor 的较大值（不重叠）
                target_pos = max(clip['start_ts'], cursor)
                placed.append((target_pos, clip['audio'], clip['actual_dur']))
                cursor = target_pos + clip['actual_dur']
                total_duration = max(total_duration, cursor)

            # 最后一条字幕的 end 也算上
            total_duration = max(total_duration, subs[-1]['end'] + 500)

            # 构建空白底轨，把各片段放到对应位置
            combined = AudioSegment.silent(duration=total_duration)
            for pos, audio, dur in placed:
                combined = combined.overlay(audio, position=pos)

            output_path = os.path.join(app.config["OUTPUT_FOLDER"], f"{task_id}.mp3")
            combined.export(output_path, format='mp3')

        else:
            # ============ 无 pydub：简单拼接 ============
            clips = []
            for i, sub in enumerate(subs):
                if task.get('cancelled'):
                    task['status'] = 'cancelled'
                    return

                clip_path = f"/tmp/tts_{task_id}_{i}.mp3"
                asyncio.run(tts_one(sub['text'], clip_path, voice, rate, volume))
                clips.append(clip_path)

                task['progress'] = int((i + 1) / total * 100)
                task['current'] = i + 1
                socketio.emit('progress', {
                    'task': task_id,
                    'progress': task['progress'],
                    'current': i + 1,
                    'total': total,
                    'text': sub['text'][:40]
                })

            output_path = os.path.join(app.config["OUTPUT_FOLDER"], f"{task_id}.mp3")
            list_file = f"/tmp/tts_{task_id}_list.txt"
            with open(list_file, 'w') as f:
                for clip in clips:
                    f.write(f"file '{clip}'\n")
            os.system(f"ffmpeg -y -f concat -safe 0 -i '{list_file}' -c:a libmp3lame -q:a 2 '{output_path}' 2>/dev/null")
            for clip in clips:
                if os.path.exists(clip):
                    os.remove(clip)
            if os.path.exists(list_file):
                os.remove(list_file)

        task['status'] = 'done'
        task['output'] = output_path
        socketio.emit('done', {'task': task_id})

    except Exception as e:
        import traceback
        task['status'] = 'error'
        task['error'] = str(e)
        print(traceback.format_exc())
        socketio.emit('error', {'task': task_id, 'error': str(e)})


# ============ 路由 ============

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/voices")
def api_voices():
    lang_filter = request.args.get("lang", "")
    voices = asyncio.run(edge_tts.list_voices())
    result = []
    for v in voices:
        if lang_filter and not v["Locale"].startswith(lang_filter):
            continue
        result.append({
            "id": v["ShortName"],
            "name": v["FriendlyName"],
            "gender": "女声" if v["Gender"] == "Female" else "男声",
            "locale": v["Locale"],
            "lang": v["Locale"].split("-")[0],
        })
    return jsonify(result)


@app.route("/api/languages")
def api_languages():
    voices = asyncio.run(edge_tts.list_voices())
    langs = {}
    for v in voices:
        code = v["Locale"].split("-")[0]
        if code not in langs:
            langs[code] = 0
        langs[code] += 1
    return jsonify([{"code": k, "count": v} for k, v in sorted(langs.items())])


@app.route("/api/generate", methods=["POST"])
def api_generate():
    voice = request.form.get("voice", "zh-CN-YunxiNeural")
    rate = normalize_pct(request.form.get("rate", "+0%"))
    volume = normalize_pct(request.form.get("volume", "+0%"))
    text_input = request.form.get("text", "")
    task_id = str(uuid.uuid4())[:8]

    filepath = None
    if "file" in request.files:
        file = request.files["file"]
        if file.filename:
            filename = f"{task_id}_{file.filename}"
            filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
            file.save(filepath)

    tasks[task_id] = {
        "status": "queued",
        "progress": 0,
        "total": 0,
        "current": 0,
    }

    thread = threading.Thread(
        target=run_tts,
        args=(task_id, filepath, voice, rate, volume, text_input),
        daemon=True
    )
    thread.start()

    return jsonify({"task": task_id})


@app.route("/api/status/<task_id>")
def api_status(task_id):
    if task_id not in tasks:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify(tasks[task_id])


@app.route("/api/cancel/<task_id>", methods=["POST"])
def api_cancel(task_id):
    if task_id in tasks:
        tasks[task_id]["cancelled"] = True
    return jsonify({"ok": True})


@app.route("/download/<task_id>")
def download(task_id):
    if task_id not in tasks or tasks[task_id]["status"] != "done":
        return "文件不存在", 404
    output = tasks[task_id]["output"]
    return send_file(output, as_attachment=True, download_name=f"tts_{task_id}.mp3")


@socketio.on("connect")
def on_connect():
    pass


if __name__ == "__main__":
    os.makedirs("uploads", exist_ok=True)
    os.makedirs("output", exist_ok=True)
    socketio.run(app, host="0.0.0.0", port=5100, debug=False, allow_unsafe_werkzeug=True)
