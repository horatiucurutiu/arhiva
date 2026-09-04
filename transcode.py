import hashlib
import json
import logging
import os
import queue
import subprocess
import threading

from flask import abort, jsonify, send_file

logger = logging.getLogger(__name__)

DIRECT_PLAY_RULES = {
    ".mp4": {"video": {"h264", "vp9", "av1"}, "audio": {"aac", "mp3", "opus", None}},
    ".m4v": {"video": {"h264"}, "audio": {"aac", "mp3", None}},
    ".webm": {"video": {"vp8", "vp9", "av1"}, "audio": {"opus", "vorbis", None}},
}


def probe_streams(ffprobe_bin: str, path: str) -> dict:
    """Return {'video_codec': str|None, 'audio_codec': str|None} for a media file."""
    result = subprocess.run(
        [ffprobe_bin, "-v", "error", "-print_format", "json", "-show_streams", path],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    video_codec = None
    audio_codec = None
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video" and video_codec is None:
            video_codec = stream.get("codec_name")
        elif stream.get("codec_type") == "audio" and audio_codec is None:
            audio_codec = stream.get("codec_name")
    return {"video_codec": video_codec, "audio_codec": audio_codec}


def is_browser_compatible(ffprobe_bin: str, path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    rule = DIRECT_PLAY_RULES.get(ext)
    if rule is None:
        return False
    try:
        streams = probe_streams(ffprobe_bin, path)
    except (RuntimeError, json.JSONDecodeError, subprocess.TimeoutExpired) as e:
        logger.warning(f"Compatibility probe failed for {path}: {e}")
        return False
    if streams["video_codec"] not in rule["video"]:
        return False
    return streams["audio_codec"] in rule["audio"]


class TranscodeManager:
    def __init__(self, ffmpeg_bin: str, ffprobe_bin: str, cache_dir: str, max_cache_bytes: int):
        self.ffmpeg_bin = ffmpeg_bin
        self.ffprobe_bin = ffprobe_bin
        self.cache_dir = cache_dir
        self.max_cache_bytes = max_cache_bytes
        os.makedirs(cache_dir, exist_ok=True)
        self._jobs = {}
        self._lock = threading.Lock()
        self._queue = queue.Queue()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

    def _cache_key(self, source_path: str) -> str:
        mtime = os.path.getmtime(source_path)
        raw = f"{os.path.abspath(source_path)}:{mtime}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def cache_path(self, source_path: str) -> str:
        return os.path.join(self.cache_dir, self._cache_key(source_path) + ".mp4")

    def status(self, source_path: str) -> str:
        if os.path.isfile(self.cache_path(source_path)):
            return "ready"
        key = self._cache_key(source_path)
        with self._lock:
            job = self._jobs.get(key)
        return job["status"] if job else "not_started"

    def enqueue(self, source_path: str) -> str:
        if os.path.isfile(self.cache_path(source_path)):
            return "ready"
        key = self._cache_key(source_path)
        with self._lock:
            existing = self._jobs.get(key)
            if existing:
                return existing["status"]
            self._jobs[key] = {"status": "processing", "source": source_path}
        self._queue.put(source_path)
        return "processing"

    def _worker_loop(self):
        while True:
            source_path = self._queue.get()
            key = self._cache_key(source_path)
            dest = self.cache_path(source_path)
            tmp_dest = dest + ".tmp"
            try:
                subprocess.run(
                    [
                        self.ffmpeg_bin, "-y", "-hwaccel", "auto", "-i", source_path,
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                        "-c:a", "aac", "-movflags", "+faststart", "-f", "mp4", tmp_dest,
                    ],
                    check=True, capture_output=True, timeout=3600,
                )
                os.replace(tmp_dest, dest)
                with self._lock:
                    self._jobs[key]["status"] = "ready"
                self._evict_if_needed()
            except Exception as e:
                logger.error(f"Transcode failed for {source_path}: {e}")
                with self._lock:
                    self._jobs[key]["status"] = "error"
                if os.path.exists(tmp_dest):
                    os.remove(tmp_dest)

    def _evict_if_needed(self):
        entries = []
        total = 0
        for name in os.listdir(self.cache_dir):
            full = os.path.join(self.cache_dir, name)
            if not os.path.isfile(full):
                continue
            st = os.stat(full)
            total += st.st_size
            entries.append((st.st_atime, st.st_size, full))
        if total <= self.max_cache_bytes:
            return
        entries.sort()
        for _atime, size, full in entries:
            if total <= self.max_cache_bytes:
                break
            os.remove(full)
            total -= size


def init_transcode(app, config, video_dir):
    ffmpeg_bin = config.get("Transcode", "FFMPEG_BIN", fallback="ffmpeg")
    ffprobe_bin = config.get("Transcode", "FFPROBE_BIN", fallback="ffprobe")
    cache_dir = os.path.join(config.get("Transcode", "CACHE_DIR"), "transcoded")
    max_cache_gb = config.getfloat("Transcode", "MAX_CACHE_GB", fallback=350)
    manager = TranscodeManager(ffmpeg_bin, ffprobe_bin, cache_dir, int(max_cache_gb * 1024**3))

    def resolve(filename):
        full_path = os.path.join(video_dir, filename)
        if not os.path.isfile(full_path):
            abort(404)
        return full_path

    @app.route("/transcode/<path:filename>", methods=["POST"])
    def start_transcode(filename):
        status = manager.enqueue(resolve(filename))
        return jsonify({"status": status})

    @app.route("/transcode-status/<path:filename>")
    def transcode_status(filename):
        return jsonify({"status": manager.status(resolve(filename))})

    @app.route("/video-proxy/<path:filename>")
    def video_proxy(filename):
        cache_path = manager.cache_path(resolve(filename))
        if not os.path.isfile(cache_path):
            abort(404)
        return send_file(cache_path)

    app.config["IS_COMPATIBLE_FN"] = lambda path: is_browser_compatible(ffprobe_bin, path)
    app.config["TRANSCODE_MANAGER"] = manager
    return manager
