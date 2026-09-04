import json
import logging
import os
import subprocess

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
