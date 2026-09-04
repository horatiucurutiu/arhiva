def login(client):
    client.post("/login", data={"username": "admin", "password": "testpass"})


def make_video(video_dir, filename="sample.mp4", content=b"not really a video"):
    path = video_dir / filename
    path.write_bytes(content)
    return path


def test_serve_file_rejects_path_traversal(client, app_and_server):
    _app, _server, video_dir = app_and_server
    login(client)

    response = client.get("/video/../../../../etc/passwd")

    assert response.status_code == 404


def test_serve_file_serves_legitimate_file(client, app_and_server):
    _app, _server, video_dir = app_and_server
    make_video(video_dir, "sample.mp4", b"hello world")
    login(client)

    response = client.get("/video/sample.mp4")

    assert response.status_code == 200
    assert response.data == b"hello world"


def test_transcode_rejects_path_traversal(client, app_and_server):
    _app, _server, video_dir = app_and_server
    login(client)

    response = client.post("/transcode/../../../../etc/passwd")

    assert response.status_code == 404


def test_transcode_status_rejects_path_traversal(client, app_and_server):
    _app, _server, video_dir = app_and_server
    login(client)

    response = client.get("/transcode-status/../../../../etc/passwd")

    assert response.status_code == 404


def test_video_proxy_rejects_path_traversal(client, app_and_server):
    _app, _server, video_dir = app_and_server
    login(client)

    response = client.get("/video-proxy/../../../../etc/passwd")

    assert response.status_code == 404


def test_serve_thumbnail_rejects_path_traversal(client, app_and_server):
    _app, _server, video_dir = app_and_server
    login(client)

    response = client.get("/thumbnail/../../../../etc/passwd")

    assert response.status_code == 404


def test_serve_cached_subtitle_rejects_path_traversal(client, app_and_server):
    _app, _server, video_dir = app_and_server
    login(client)

    response = client.get("/subtitle-cache/../../../../etc/passwd")

    assert response.status_code == 404


def test_api_related_videos_rejects_path_traversal(client, app_and_server):
    _app, _server, video_dir = app_and_server
    login(client)

    response = client.get("/api/related-videos?folder=../../../../etc")

    assert response.status_code == 404
