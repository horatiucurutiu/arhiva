import configparser
import os

import bcrypt
import pytest

from main import create_app

# Use the vendored ffmpeg/ffprobe (same binaries production points at via
# config.ini's FFMPEG_BIN/FFPROBE_BIN) instead of whatever ffmpeg happens to
# be on the system PATH — the system ffmpeg on this host lacks a libx264
# encoder, while the vendored build has it.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VENDORED_FFMPEG = os.path.join(_REPO_ROOT, "vendor", "ffmpeg", "ffmpeg")
_VENDORED_FFPROBE = os.path.join(_REPO_ROOT, "vendor", "ffmpeg", "ffprobe")


def write_config(config_path, video_dir, thumbnail_dir, cache_dir, password="testpass"):
    config = configparser.ConfigParser()
    config["Paths"] = {"VIDEO_DIR": str(video_dir), "THUMBNAIL_DIR": str(thumbnail_dir)}
    config["Subtitles"] = {"EXTENSIONS": ".vtt,.srt"}
    config["Videos"] = {"EXTENSIONS": ".mp4,.mkv,.mov,.avi,.webm"}
    config["Server"] = {"HOST": "127.0.0.1", "PORT": "5000", "BASE_URL": "/video/"}
    config["Display"] = {"SHOW_HIDDEN": "false"}
    config["Auth"] = {
        "USERNAME": "admin",
        "PASSWORD_HASH": bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
        "SECRET_KEY": "test-secret-key",
        "SECURE_COOKIES": "false",
    }
    config["Transcode"] = {
        "CACHE_DIR": str(cache_dir),
        "MAX_CACHE_GB": "1",
        "FFMPEG_BIN": _VENDORED_FFMPEG,
        "FFPROBE_BIN": _VENDORED_FFPROBE,
    }
    with open(config_path, "w") as f:
        config.write(f)


@pytest.fixture
def app_and_server(tmp_path):
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    thumbnail_dir = tmp_path / "thumbnails"
    cache_dir = tmp_path / "cache"
    config_path = tmp_path / "config.ini"
    write_config(config_path, video_dir, thumbnail_dir, cache_dir)

    app, video_server = create_app(str(config_path))
    app.testing = True
    yield app, video_server, video_dir


@pytest.fixture
def client(app_and_server):
    app, _server, _video_dir = app_and_server
    return app.test_client()
