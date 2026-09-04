import io
import os
import zipfile

from tests.test_images import make_app_with_images


def login(client):
    client.post("/login", data={"username": "admin", "password": "testpass"})


def test_download_file_requires_login(tmp_path):
    app, _server, video_dir = make_app_with_images(tmp_path)
    (video_dir / "movie.mp4").write_bytes(b"fake video")
    client = app.test_client()

    response = client.get("/download/movie.mp4")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_download_file_serves_as_attachment(tmp_path):
    app, _server, video_dir = make_app_with_images(tmp_path)
    (video_dir / "movie.mp4").write_bytes(b"fake video bytes")
    client = app.test_client()
    login(client)

    response = client.get("/download/movie.mp4")

    assert response.status_code == 200
    assert "attachment" in response.headers["Content-Disposition"]
    assert response.data == b"fake video bytes"


def test_download_file_rejects_traversal(tmp_path):
    app, _server, _video_dir = make_app_with_images(tmp_path)
    client = app.test_client()
    login(client)

    response = client.get("/download/../../../../etc/passwd")

    assert response.status_code == 404


def test_download_folder_zips_recursively(tmp_path):
    app, _server, video_dir = make_app_with_images(tmp_path)
    (video_dir / "top.mp4").write_bytes(b"top level video")
    sub = video_dir / "sub"
    sub.mkdir()
    (sub / "nested.mp4").write_bytes(b"nested video")
    client = app.test_client()
    login(client)

    response = client.get("/download-folder/")

    assert response.status_code == 200
    assert "attachment" in response.headers["Content-Disposition"]
    zf = zipfile.ZipFile(io.BytesIO(response.data))
    names = set(zf.namelist())
    assert "top.mp4" in names
    assert os.path.join("sub", "nested.mp4").replace("\\", "/") in {n.replace("\\", "/") for n in names}
    assert zf.read("top.mp4") == b"top level video"


def test_download_folder_of_subfolder_only_includes_that_subtree(tmp_path):
    app, _server, video_dir = make_app_with_images(tmp_path)
    (video_dir / "outside.mp4").write_bytes(b"should not appear")
    sub = video_dir / "sub"
    sub.mkdir()
    (sub / "inside.mp4").write_bytes(b"should appear")
    client = app.test_client()
    login(client)

    response = client.get("/download-folder/sub")

    zf = zipfile.ZipFile(io.BytesIO(response.data))
    names = set(zf.namelist())
    assert "inside.mp4" in names
    assert "outside.mp4" not in names
    assert "sub.zip" in response.headers["Content-Disposition"]


def test_download_folder_respects_exclude_dirs(tmp_path):
    app, _server, video_dir = make_app_with_images(tmp_path, exclude_dirs="junk")
    (video_dir / "keep.mp4").write_bytes(b"keep me")
    junk = video_dir / "junk"
    junk.mkdir()
    (junk / "skip.mp4").write_bytes(b"skip me")
    client = app.test_client()
    login(client)

    response = client.get("/download-folder/")

    zf = zipfile.ZipFile(io.BytesIO(response.data))
    names = set(zf.namelist())
    assert "keep.mp4" in names
    assert not any("skip.mp4" in n for n in names)


def test_download_folder_rejects_traversal(tmp_path):
    app, _server, _video_dir = make_app_with_images(tmp_path)
    client = app.test_client()
    login(client)

    response = client.get("/download-folder/../../../../etc")

    assert response.status_code == 404
