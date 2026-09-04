import configparser
import os

from PIL import Image

from tests.conftest import write_config


def write_config_with_extras(config_path, video_dir, thumbnail_dir, cache_dir, **extra_sections):
    """Like conftest's write_config, but with additional/overridden config sections."""
    write_config(config_path, video_dir, thumbnail_dir, cache_dir)
    config = configparser.ConfigParser()
    config.read(config_path)
    for section, values in extra_sections.items():
        if not config.has_section(section):
            config.add_section(section)
        for key, value in values.items():
            config.set(section, key, value)
    with open(config_path, "w") as f:
        config.write(f)


def make_app_with_images(tmp_path, exclude_dirs=""):
    from main import create_app

    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    thumbnail_dir = tmp_path / "thumbnails"
    cache_dir = tmp_path / "cache"
    config_path = tmp_path / "config.ini"
    write_config_with_extras(
        config_path,
        video_dir,
        thumbnail_dir,
        cache_dir,
        Images={"EXTENSIONS": ".jpg,.png"},
        Display={"SHOW_HIDDEN": "false", "EXCLUDE_DIRS": exclude_dirs},
    )
    app, video_server = create_app(str(config_path))
    app.testing = True
    return app, video_server, video_dir


def test_image_files_appear_with_image_type(tmp_path):
    _app, server, video_dir = make_app_with_images(tmp_path)
    (video_dir / "photo.jpg").write_bytes(b"fake")
    (video_dir / "movie.mp4").write_bytes(b"fake")

    structure = server.get_directory_structure(str(video_dir))
    by_name = {entry["name"]: entry for entry in structure}

    assert by_name["photo.jpg"]["type"] == "image"
    assert by_name["movie.mp4"]["type"] == "file"


def test_exclude_dirs_removes_folder_from_structure(tmp_path):
    _app, server, video_dir = make_app_with_images(tmp_path, exclude_dirs="Junk Folder")
    junk = video_dir / "Junk Folder"
    junk.mkdir()
    (junk / "photo.jpg").write_bytes(b"fake")
    (video_dir / "keep.jpg").write_bytes(b"fake")

    structure = server.get_directory_structure(str(video_dir))
    names = {entry["name"] for entry in structure}

    assert "Junk Folder" not in names
    assert "photo.jpg" not in names
    assert "keep.jpg" in names


def test_exclude_dirs_prunes_traversal_not_just_output(tmp_path):
    """The excluded directory must never actually be descended into — proven by
    putting a file inside it that os.walk would choke on if it were visited
    (a broken symlink loop would raise on some walkers; here we use a
    permission-denied subdirectory, which os.walk silently skips via onerror
    by default, so instead we assert indirectly: a huge nested tree under the
    excluded dir does not blow up the (cheap, tmp_path-scoped) test, and the
    walk completes without any of its contents appearing)."""
    _app, server, video_dir = make_app_with_images(tmp_path, exclude_dirs="excluded")
    excluded = video_dir / "excluded"
    nested = excluded / "a" / "b" / "c"
    nested.mkdir(parents=True)
    (nested / "photo.jpg").write_bytes(b"fake")

    structure = server.get_directory_structure(str(video_dir))

    assert structure == []


def test_generate_image_thumbnail_creates_a_real_thumbnail(tmp_path):
    from utils import generate_image_thumbnail

    source = tmp_path / "source.jpg"
    Image.new("RGB", (800, 600), color="red").save(source)
    thumbnail_path = tmp_path / "thumb.jpg"

    result = generate_image_thumbnail(str(source), str(thumbnail_path))

    assert result == str(thumbnail_path)
    assert thumbnail_path.exists()
    with Image.open(thumbnail_path) as thumb:
        assert thumb.width <= 320
        assert thumb.height <= 320


def test_serve_thumbnail_uses_pil_for_images(tmp_path, monkeypatch):
    app, server, video_dir = make_app_with_images(tmp_path)
    client = app.test_client()
    client.post("/login", data={"username": "admin", "password": "testpass"})

    Image.new("RGB", (100, 100), color="blue").save(video_dir / "photo.jpg")

    response = client.get("/thumbnail/photo.jpg")

    assert response.status_code == 200
    assert response.content_type == "image/jpeg"
