import os


def login(client):
    client.post("/login", data={"username": "admin", "password": "testpass"})


def test_directory_contains_supported_files_follows_symlinks(tmp_path, app_and_server):
    """A folder whose videos live only under a nested symlink still counts as
    containing supported files, matching get_directory_structure's own walk."""
    from utils import directory_contains_supported_files

    _app, _server, video_dir = app_and_server

    parent = video_dir / "parent"
    parent.mkdir()
    real_sub = tmp_path / "real_sub"
    real_sub.mkdir()
    (real_sub / "movie.mp4").write_bytes(b"fake")
    os.symlink(real_sub, parent / "sub")

    assert directory_contains_supported_files(str(parent), [".mp4"], False) is True


def test_get_directory_structure_follows_symlinked_roots(tmp_path, app_and_server):
    _app, server, video_dir = app_and_server

    real_root = tmp_path / "real_root"
    real_root.mkdir()
    (real_root / "movie.mp4").write_bytes(b"fake")

    symlink_root = video_dir / "linked"
    os.symlink(real_root, symlink_root)

    structure = server.get_directory_structure(str(video_dir))
    names = {entry["name"] for entry in structure}

    assert "linked" in names
    assert "movie.mp4" in names


def test_serve_file_serves_a_file_behind_a_symlinked_root(tmp_path, client, app_and_server):
    """End-to-end: a file reached through a roots/ symlink must actually be served.

    safe_join() must not resolve symlinks — the whole point of the roots/
    mechanism is that the real file lives outside VIDEO_DIR.
    """
    _app, _server, video_dir = app_and_server

    real_root = tmp_path / "real_root"
    real_root.mkdir()
    (real_root / "movie.mp4").write_bytes(b"symlinked payload")
    os.symlink(real_root, video_dir / "linked")

    login(client)
    response = client.get("/video/linked/movie.mp4")

    assert response.status_code == 200
    assert response.data == b"symlinked payload"


def test_play_video_renders_for_a_file_behind_a_symlinked_root(tmp_path, client, app_and_server):
    _app, _server, video_dir = app_and_server

    real_root = tmp_path / "real_root"
    real_root.mkdir()
    (real_root / "movie.mp4").write_bytes(b"symlinked payload")
    os.symlink(real_root, video_dir / "linked")

    login(client)
    response = client.get("/play/linked/movie.mp4")

    assert response.status_code == 200


def test_related_videos_lists_files_behind_a_symlinked_root(tmp_path, client, app_and_server):
    _app, _server, video_dir = app_and_server

    real_root = tmp_path / "real_root"
    real_root.mkdir()
    (real_root / "movie.mp4").write_bytes(b"symlinked payload")
    os.symlink(real_root, video_dir / "linked")

    login(client)
    response = client.get("/api/related-videos?folder=linked")

    assert response.status_code == 200
    assert [v["name"] for v in response.get_json()] == ["movie.mp4"]


def test_subtitle_cache_path_for_symlinked_root_stays_inside_the_cache(tmp_path, app_and_server):
    """The lexical safe_join must keep extract_subtitles' relpath escape-free.

    With a realpath-resolving safe_join, video_path for a symlinked file would
    resolve outside VIDEO_DIR and os.path.relpath() would produce a "../"-laden
    path, writing the .vtt outside the subtitle cache directory.
    """
    from utils import safe_join

    _app, server, video_dir = app_and_server

    real_root = tmp_path / "real_root"
    real_root.mkdir()
    (real_root / "movie.mkv").write_bytes(b"fake")
    os.symlink(real_root, video_dir / "linked")

    full_path = safe_join(str(video_dir), "linked/movie.mkv")
    rel_path = os.path.relpath(full_path, server.video_dir)

    assert rel_path == os.path.join("linked", "movie.mkv")
    assert not rel_path.startswith("..")

    output_path = os.path.abspath(
        os.path.join(server.subtitle_cache_dir, os.path.splitext(rel_path)[0] + ".vtt")
    )
    cache_root = os.path.abspath(server.subtitle_cache_dir)
    assert output_path.startswith(cache_root + os.sep)
