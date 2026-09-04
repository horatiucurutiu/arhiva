import configparser
import shutil

import bcrypt
import pytest

from main import create_app


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
        "FFMPEG_BIN": shutil.which("ffmpeg") or "ffmpeg",
        "FFPROBE_BIN": shutil.which("ffprobe") or "ffprobe",
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
