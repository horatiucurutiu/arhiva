import os
import subprocess

# Use the vendored ffmpeg (the same binary production points at via
# config.ini's FFMPEG_BIN), resolved from this file's own location so the tests
# do not depend on the working directory or on the system PATH.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FFMPEG = os.path.join(_REPO_ROOT, "vendor", "ffmpeg", "ffmpeg")


def make_incompatible_video(video_dir, filename="clip.avi"):
    path = video_dir / filename
    subprocess.run(
        [
            FFMPEG, "-hide_banner", "-y",
            "-f", "lavfi", "-i", "testsrc=size=160x120:rate=1:duration=1",
            "-c:v", "libx264", str(path),
        ],
        check=True, capture_output=True,
    )
    return path


def login(client):
    client.post("/login", data={"username": "admin", "password": "testpass"})


def test_play_video_marks_incompatible_file_as_needing_transcode(client, app_and_server):
    _app, _server, video_dir = app_and_server
    make_incompatible_video(video_dir)
    login(client)

    response = client.get("/play/clip.avi")

    assert response.status_code == 200
    assert b"transcode-status" in response.data


def test_play_video_exposes_a_download_original_fallback(client, app_and_server):
    """Spec: a failed transcode must offer a "download original" link, and a
    failed status poll must surface an error instead of silently stopping."""
    _app, _server, video_dir = app_and_server
    make_incompatible_video(video_dir)
    login(client)

    response = client.get("/play/clip.avi")
    body = response.data.decode()

    assert 'data-original-url="/download/clip.avi"' in body
    assert "Download the original file" in body
    assert body.count(".catch(") >= 2


def test_transcode_status_starts_not_started(client, app_and_server):
    _app, _server, video_dir = app_and_server
    make_incompatible_video(video_dir)
    login(client)

    response = client.get("/transcode-status/clip.avi")

    assert response.status_code == 200
    assert response.get_json()["status"] == "not_started"


def test_transcode_then_video_proxy_serves_file(client, app_and_server):
    import time

    _app, _server, video_dir = app_and_server
    make_incompatible_video(video_dir)
    login(client)

    client.post("/transcode/clip.avi")

    deadline = time.time() + 30
    status = "processing"
    while time.time() < deadline and status != "ready":
        time.sleep(0.2)
        status = client.get("/transcode-status/clip.avi").get_json()["status"]

    assert status == "ready"
    proxy_response = client.get("/video-proxy/clip.avi")
    assert proxy_response.status_code == 200
