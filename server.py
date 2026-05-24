from flask import Flask, send_file, jsonify, request
import os
import threading
import time
from pathlib import Path

from media import get_media_info

app = Flask(__name__)

# MUST MATCH Chromecast app
MEDIA_BASE_PATH = "/tmp/dtr"

# Single active playback state
is_busy = False

# Last playback timestamp
last_request_time = None

LOCK = threading.Lock()

@app.route("/ready", methods=["POST"])
def ready():
    global is_busy
    global last_request_time

    with LOCK:
        if is_busy:
            print("[MEDIA SERVER] Rejecting request: already busy")

            return jsonify({
                "success": False,
                "reason": "busy"
            }), 409

        info = request.get_json()

        if not info.get("file"):
            print("[MEDIA SERVER] Rejecting request: ready for which file?")

            return jsonify({
                "success": False,
                "reason": "no_file"
            }), 404

        if not os.path.exists(info["file"]):
            print("[MEDIA SERVER] Rejecting request: file missing")

            return jsonify({
                "success": False,
                "reason": "media_missing"
            }), 404

        is_busy = True
        last_request_time = time.time()

        print(
            "[MEDIA SERVER] Accepted playback request "
            f"at {last_request_time}"
        )

        return jsonify({
            "success": True
        })


@app.route("/stream/<path:filename>")
def stream(filename):
    global is_busy

    file_path = Path(MEDIA_BASE_PATH) / filename
    
    print(f"[MEDIA SERVER] Chromecast requested stream {file_path}")

    if not os.path.exists(file_path):
        return "Stream not found", 404

    try:
        info = get_media_info(file_path)
        mime = info.get("mime")
    except Exception as e:
        print(f"Warning: failed to determine the format of the file!! {file_path}");

    return send_file(
        file_path,
        mimetype=mime,
        conditional=True
    )


@app.route("/playback_finished", methods=["POST"])
def playback_finished():
    global is_busy

    with LOCK:
        is_busy = False

    print("[MEDIA SERVER] Playback finished")

    return jsonify({
        "success": True
    })


@app.route("/status")
def status():
    return jsonify({
        "busy": is_busy,
        "media_exists": os.path.exists(VIDEO_PATH),
        "last_request_time": last_request_time
    })


@app.route("/")
def index():
    return f"""
    <html>
        <body>
            <h1>Media Server</h1>

            <p><b>Busy:</b> {is_busy}</p>
        </body>
    </html>
    """


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=8080,
        threaded=True
    )
