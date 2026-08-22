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
from functools import wraps
from flask_socketio import SocketIO
import edge_tts
from db import init_db, verify_user, add_history, update_history, list_history, add_user, list_users, delete_user, update_password, update_username

try:
    from pydub import AudioSegment
    HAS_PYDUB = True
except ImportError:
    HAS_PYDUB = False

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "tts-web-secret-key-change-me")

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
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



def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "未登录"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

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
            import subprocess as sp

            def fit_audio_to_duration(audio, target_ms):
                # If audio is longer than target, speed it up with atempo (no truncation)
                actual = len(audio)
                if actual <= target_ms or target_ms <= 0:
                    return audio
                speed = actual / target_ms
                if speed > 4.0:
                    speed = 4.0
                # atempo chain: 0.5~2.0 per step
                atempos = []
                remaining = speed
                while remaining > 2.0:
                    atempos.append(2.0)
                    remaining = remaining / 2.0
                atempos.append(round(remaining, 3))
                atempo_str = ",".join(f"atempo={a}" for a in atempos)
                tmp_in = f"/tmp/_atempo_in_{task_id}.mp3"
                tmp_out = f"/tmp/_atempo_out_{task_id}.mp3"
                audio.export(tmp_in, format='mp3')
                sp.run(['ffmpeg', '-y', '-i', tmp_in, '-filter:a', atempo_str, tmp_out],
                       capture_output=True)
                result = AudioSegment.from_file(tmp_out)
                for p in [tmp_in, tmp_out]:
                    if os.path.exists(p):
                        os.remove(p)
                return result

            # Generate all voice clips first
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

                audio_clips.append({
                    'audio': audio,
                    'start_ts': sub['start'],
                    'end_ts': sub['end'],
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

            # Place each clip strictly at subtitle start time
            # If too long, speed up with atempo to fit (no truncation, no delay)
            total_duration = subs[-1]['end'] + 1000
            combined = AudioSegment.silent(duration=total_duration)

            for i, clip in enumerate(audio_clips):
                target_pos = clip['start_ts']
                # Max duration: until next subtitle starts
                if i < len(audio_clips) - 1:
                    max_dur = audio_clips[i + 1]['start_ts'] - target_pos
                else:
                    max_dur = clip['end_ts'] - target_pos

                fitted = fit_audio_to_duration(clip['audio'], int(max_dur))
                combined = combined.overlay(fitted, position=target_pos)

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

        # Clean up uploaded file
        if filepath and os.path.exists(filepath):
            os.remove(filepath)

        task['status'] = 'merging'
        socketio.emit('merging', {'task': task_id})

        task['status'] = 'done'
        task['output'] = output_path
        # Update history
        try:
            duration_ms = len(combined) if HAS_PYDUB else 0
            update_history(task_id, total, duration_ms, 'done')
        except:
            pass
        socketio.emit('done', {'task': task_id})

    except Exception as e:
        import traceback
        task['status'] = 'error'
        task['error'] = str(e)
        print(traceback.format_exc())
        socketio.emit('error', {'task': task_id, 'error': str(e)})


# ============ Authentication Routes ============
@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if verify_user(username, password):
            session["logged_in"] = True
            session["username"] = username
            return redirect(url_for("index"))
        error = "用户名或密码错误"
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ============ Routes ============
@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/api/voices")
@login_required
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


@app.route("/api/preview/<voice_id>")
@login_required
def api_preview(voice_id):
    """Generate a short voice preview sample"""
    # Sample text based on language
    voice_lower = voice_id.lower()
    if voice_lower.startswith("zh"):
        sample = "你好，这是语音试听示例。"
    elif voice_lower.startswith("ja"):
        sample = "こんにちは、これは音声サンプルです。"
    elif voice_lower.startswith("ko"):
        sample = "안녕하세요, 음성 샘플입니다."
    elif voice_lower.startswith("en"):
        sample = "Hello, this is a voice sample."
    elif voice_lower.startswith("es"):
        sample = "Hola, esta es una muestra de voz."
    elif voice_lower.startswith("fr"):
        sample = "Bonjour, ceci est un échantillon vocal."
    elif voice_lower.startswith("de"):
        sample = "Hallo, dies ist eine Sprachprobe."
    elif voice_lower.startswith("ru"):
        sample = "Здравствуйте, это образец голоса."
    elif voice_lower.startswith("ar"):
        sample = "مرحبا، هذا عينة صوتية."
    elif voice_lower.startswith("th"):
        sample = "สวัสดี นี่คือตัวอย่างเสียง"
    elif voice_lower.startswith("vi"):
        sample = "Xin chào, đây là mẫu giọng nói."
    elif voice_lower.startswith("it"):
        sample = "Ciao, questo è un campione vocale."
    elif voice_lower.startswith("pt"):
        sample = "Olá, esta é uma amostra de voz."
    else:
        sample = "Hello, this is a voice sample."

    # Check cache first
    import hashlib
    cache_key = hashlib.md5(voice_id.encode()).hexdigest()
    cache_dir = os.path.join(app.config["OUTPUT_FOLDER"], "previews")
    cache_path = os.path.join(cache_dir, f"{cache_key}.mp3")
    os.makedirs(cache_dir, exist_ok=True)

    if os.path.exists(cache_path):
        return send_file(cache_path, mimetype="audio/mpeg")

    # Generate preview
    try:
        asyncio.run(tts_one(sample, cache_path, voice_id, "+0%", "+0%"))
        return send_file(cache_path, mimetype="audio/mpeg")
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/languages")
@login_required
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
@login_required
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

    # Save to history
    filename = None
    if "file" in request.files and request.files["file"].filename:
        filename = request.files["file"].filename
    elif text_input:
        filename = "(text input)"
    add_history(task_id, session.get("username", "unknown"), filename, voice, rate, volume)

    thread = threading.Thread(
        target=run_tts,
        args=(task_id, filepath, voice, rate, volume, text_input),
        daemon=True
    )
    thread.start()

    return jsonify({"task": task_id})


@app.route("/api/status/<task_id>")
@login_required
def api_status(task_id):
    if task_id not in tasks:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify(tasks[task_id])


@app.route("/api/cancel/<task_id>", methods=["POST"])
@login_required
def api_cancel(task_id):
    if task_id in tasks:
        tasks[task_id]["cancelled"] = True
    return jsonify({"ok": True})


@app.route("/api/history")
@login_required
def api_history():
    username = session.get("username")
    return jsonify(list_history(username))

@app.route("/api/users")
@login_required
def api_users():
    return jsonify(list_users())

@app.route("/api/users", methods=["POST"])
@login_required
def api_add_user():
    data = request.json or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    if not username or not password:
        return jsonify({"error": "用户名和密码不能为空"}), 400
    if add_user(username, password):
        return jsonify({"ok": True})
    return jsonify({"error": "用户名已存在"}), 400

@app.route("/api/users/<username>", methods=["DELETE"])
@login_required
def api_del_user(username):
    if username == session.get("username"):
        return jsonify({"error": "不能删除当前登录用户"}), 400
    delete_user(username)
    return jsonify({"ok": True})


@app.route("/api/account/password", methods=["POST"])
@login_required
def api_change_password():
    """Change current user password - requires old password"""
    data = request.json or {}
    old_pwd = data.get("old_password", "")
    new_pwd = data.get("new_password", "")
    username = session.get("username")
    if not old_pwd or not new_pwd:
        return jsonify({"error": "请填写旧密码和新密码"}), 400
    if not verify_user(username, old_pwd):
        return jsonify({"error": "旧密码不正确"}), 400
    if len(new_pwd) < 4:
        return jsonify({"error": "新密码至少4位"}), 400
    update_password(username, new_pwd)
    return jsonify({"ok": True})


@app.route("/api/account/username", methods=["POST"])
@login_required
def api_change_username():
    """Change current username - requires password verification"""
    data = request.json or {}
    new_name = data.get("new_username", "").strip()
    password = data.get("password", "")
    old_name = session.get("username")
    if not new_name or not password:
        return jsonify({"error": "请填写新用户名和密码"}), 400
    if not verify_user(old_name, password):
        return jsonify({"error": "密码不正确"}), 400
    if update_username(old_name, new_name):
        session["username"] = new_name
        return jsonify({"ok": True, "username": new_name})
    return jsonify({"error": "用户名已存在"}), 400

@app.route("/download/<task_id>")
@login_required
def download(task_id):
    if task_id not in tasks or tasks[task_id]["status"] != "done":
        return "文件不存在", 404
    output = tasks[task_id]["output"]
    return send_file(output, as_attachment=True, download_name=f"tts_{task_id}.mp3")


@socketio.on("connect")
def on_connect():
    pass


if __name__ == "__main__":
    init_db()
    os.makedirs("uploads", exist_ok=True)
    os.makedirs("output", exist_ok=True)
    socketio.run(app, host="0.0.0.0", port=5100, debug=False, allow_unsafe_werkzeug=True)
