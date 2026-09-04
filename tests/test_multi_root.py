import os


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
