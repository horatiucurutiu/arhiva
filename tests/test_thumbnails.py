import os
import subprocess

import services

# Vendored ffmpeg, same as the rest of the suite and as production's FFMPEG_BIN.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FFMPEG = os.path.join(_REPO_ROOT, "vendor", "ffmpeg", "ffmpeg")


def login(client):
    client.post("/login", data={"username": "admin", "password": "testpass"})


def make_video(video_dir, filename="clip.mp4"):
    path = video_dir / filename
    subprocess.run(
        [
            FFMPEG, "-hide_banner", "-y",
            "-f", "lavfi", "-i", "testsrc=size=160x120:rate=1:duration=6",
            "-c:v", "libx264", str(path),
        ],
        check=True, capture_output=True,
    )
    return path


def test_serve_thumbnail_uses_the_configured_ffmpeg_binary(client, app_and_server, monkeypatch):
    """Thumbnails must use FFMPEG_BIN, not a hardcoded "ffmpeg" off the PATH —
    only the vendored build can decode HEVC sources."""
    _app, server, video_dir = app_and_server
    make_video(video_dir)
    login(client)

    seen = {}
    real = services.generate_thumbnail

    def spy(video_path, thumbnail_path, ffmpeg_bin="ffmpeg"):
        seen["ffmpeg_bin"] = ffmpeg_bin
        return real(video_path, thumbnail_path, ffmpeg_bin)

    monkeypatch.setattr(services, "generate_thumbnail", spy)

    response = client.get("/thumbnail/clip.mp4")

    assert response.status_code == 200
    assert seen["ffmpeg_bin"] == server.config.get("Transcode", "FFMPEG_BIN")


def test_serve_thumbnail_generates_a_real_jpeg(client, app_and_server):
    _app, _server, video_dir = app_and_server
    make_video(video_dir)
    login(client)

    response = client.get("/thumbnail/clip.mp4")

    assert response.status_code == 200
    assert response.data.startswith(b"\xff\xd8")  # JPEG SOI marker


def make_short_video(video_dir, filename, duration):
    path = video_dir / filename
    subprocess.run(
        [
            FFMPEG, "-hide_banner", "-y",
            "-f", "lavfi", "-i", f"testsrc=size=160x120:rate=25:duration={duration}",
            "-c:v", "libx264", str(path),
        ],
        check=True, capture_output=True,
    )
    return path


def test_serve_thumbnail_for_a_clip_shorter_than_the_seek_offset(client, app_and_server):
    """Clips of 5 s or less have no frame at 00:00:05; the thumbnail must still
    be generated (81 of the archive's videos were 404/500 before)."""
    _app, _server, video_dir = app_and_server
    make_short_video(video_dir, "short.mp4", 2.5)
    login(client)

    response = client.get("/thumbnail/short.mp4")

    assert response.status_code == 200
    assert response.data.startswith(b"\xff\xd8")


def test_generate_thumbnail_returns_none_when_ffmpeg_writes_nothing(tmp_path):
    """ffmpeg can exit 0 without writing a frame; returning the missing path made
    send_file raise a 500 instead of a clean 404."""
    from utils import generate_thumbnail

    source = tmp_path / "clip.mp4"
    source.write_bytes(b"")
    thumb = tmp_path / "thumb.jpg"

    assert generate_thumbnail(str(source), str(thumb), ffmpeg_bin="true") is None
