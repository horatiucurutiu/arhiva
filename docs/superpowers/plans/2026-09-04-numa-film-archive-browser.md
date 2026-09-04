# Numa Film Archive Browser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the `1999AZZAR/video-browser` fork at `~/arhiva` into "Numa Film Archive" — a branded, authenticated, internet-facing browser/player for video archives (starting with `/mnt/norman-manea`), with on-demand transcoding for formats the browser can't play natively.

**Architecture:** Flask app (existing upstream `services.py`/`utils.py` untouched except where noted) gains two new modules — `auth.py` (session-cookie login gate) and `transcode.py` (ffprobe-based compatibility check + background ffmpeg transcode queue with a size-capped disk cache) — wired in from `main.py`. Multi-root browsing is achieved by pointing `VIDEO_DIR` at a folder of symlinks rather than changing the directory-scan code. Deployment follows the existing Apache-reverse-proxy-with-Let's-Encrypt pattern used for this server's other `numafilm.ro` subdomains.

**Tech Stack:** Python 3.14, Flask, Flask-Caching (existing), bcrypt (new), pytest (new), a dedicated static ffmpeg/ffprobe build (BtbN GPL build, for HEVC decode — the system `ffmpeg` has HEVC decoding disabled), gunicorn (existing dependency, used for deployment), systemd `--user` service, Apache + Let's Encrypt.

**Spec:** `docs/superpowers/specs/2026-09-04-numa-film-archive-browser-design.md`

## Global Constraints

- Single shared login only — no multi-user accounts (spec: Non-goals).
- Transcoded proxies are always H.264 video / AAC audio in an MP4 container (spec: Non-goals — no HLS/adaptive bitrate).
- Transcode cache lives at `/mnt/SSD2/arhiva_cache` with a default 350GB cap (spec: Component 2).
- App binds to `127.0.0.1:8093` only; internet exposure is via Apache reverse proxy at `arhiva.numafilm.ro`, never a directly-exposed app port (spec: Component 4 / Error handling).
- Login page must show the Numa Film logo and read "NUMA FILM ARCHIVE" (approved in brainstorming, prior to spec write-up).
- The system-wide `ffmpeg` (used by other apps on this box) is never modified or upgraded — this app uses its own vendored binary (spec: HEVC gap resolution).
- Follow existing repo conventions: routes/logic added to the smallest new file that owns that responsibility; upstream `services.py`/`utils.py` changes are minimal, surgical, and noted inline.

---

### Task 1: Test harness and dependencies

**Files:**
- Modify: `requirements.txt`
- Create: `tests/__init__.py` (empty)
- Create: `tests/conftest.py`
- Create: `pytest.ini`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `tests/conftest.py::write_config(path, video_dir, thumbnail_dir, cache_dir, password="testpass")` — writes a complete valid `config.ini` to `path`.
- Produces: `tests/conftest.py::app_and_server` pytest fixture — yields `(app, video_server, video_dir)` with `video_dir` as a `pathlib.Path` under `tmp_path`.
- Produces: `tests/conftest.py::client` pytest fixture — yields a Flask test client built from `app_and_server`.
- Consumes: `main.create_app(config_path)` (existing, from `main.py`) — must keep working with just a `config_path` positional string argument, returning `(app, video_server)`.

- [ ] **Step 1: Create and activate a project virtualenv, install current deps**

```bash
cd /home/numafilm/arhiva
python3.14 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Expected: installs cleanly (Flask, Flask-Caching, Werkzeug, gunicorn, pillow, ffmpeg-python).

- [ ] **Step 2: Add new dependencies to `requirements.txt`**

Append these two lines to `requirements.txt`:

```
bcrypt
pytest
```

- [ ] **Step 3: Install the new dependencies**

```bash
pip install bcrypt pytest
```

- [ ] **Step 4: Create `pytest.ini`**

```ini
[pytest]
testpaths = tests
pythonpath = .
```

- [ ] **Step 5: Create `tests/__init__.py`**

Empty file (makes `tests` importable as a package; not strictly required by pytest but matches how `cotatii`'s test suite is laid out).

- [ ] **Step 6: Create `tests/conftest.py`**

```python
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
```

- [ ] **Step 7: Add `venv/`, `vendor/`, and `roots/*` ignores to `.gitignore`**

Append to `.gitignore`:

```
venv/
__pycache__/
vendor/
roots/*
!roots/.gitkeep
```

- [ ] **Step 8: Verify the harness runs (even with zero tests yet)**

```bash
pytest
```

Expected: `no tests ran` (or similar) with no import errors — confirms `create_app` import path and fixture setup are wired correctly before any real tests are added.

- [ ] **Step 9: Commit**

```bash
git add requirements.txt pytest.ini tests/__init__.py tests/conftest.py .gitignore
git commit -m "Add pytest harness and auth/transcode dependencies"
```

---

### Task 2: Dedicated ffmpeg/ffprobe build (HEVC decode)

**Files:**
- Create: `scripts/install_ffmpeg.sh`

**Interfaces:**
- Produces: `vendor/ffmpeg/ffmpeg` and `vendor/ffmpeg/ffprobe` executables (gitignored, downloaded on demand) — later tasks' production config points `FFMPEG_BIN`/`FFPROBE_BIN` at these paths.

**Context:** the system `ffmpeg` on this host has native H.264/HEVC software decoding disabled (`--disable-decoder='h264,hevc,vc1,vvc'`) and there is no HEVC decoder at all (verified: `ffmpeg -decoders | grep hevc` returns nothing, and `-hwaccel auto` doesn't help because there's no decoder to attach hardware acceleration to). ProRes decode works fine on the system build. Rather than touch the system `ffmpeg` other apps depend on, this app vendors its own static build.

- [ ] **Step 1: Write `scripts/install_ffmpeg.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$SCRIPT_DIR/../vendor/ffmpeg"
URL="https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz"

mkdir -p "$DEST"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Downloading $URL ..."
curl -L "$URL" -o "$TMP/ffmpeg.tar.xz"

echo "Extracting ..."
tar -xf "$TMP/ffmpeg.tar.xz" -C "$TMP"

EXTRACTED_DIR="$(find "$TMP" -maxdepth 1 -type d -name 'ffmpeg-*')"
if [ -z "$EXTRACTED_DIR" ]; then
    echo "Could not find extracted ffmpeg directory" >&2
    exit 1
fi

cp "$EXTRACTED_DIR/bin/ffmpeg" "$DEST/ffmpeg"
cp "$EXTRACTED_DIR/bin/ffprobe" "$DEST/ffprobe"
chmod +x "$DEST/ffmpeg" "$DEST/ffprobe"

echo "Installed to $DEST:"
"$DEST/ffmpeg" -version | head -1
"$DEST/ffprobe" -version | head -1
```

- [ ] **Step 2: Make it executable and run it**

```bash
chmod +x scripts/install_ffmpeg.sh
./scripts/install_ffmpeg.sh
```

Expected: prints an ffmpeg/ffprobe version line, and `vendor/ffmpeg/ffmpeg` + `vendor/ffmpeg/ffprobe` exist.

- [ ] **Step 3: Verify HEVC decode actually works with the vendored build**

```bash
vendor/ffmpeg/ffmpeg -hide_banner -decoders 2>/dev/null | grep -E "^\s*[VAS].*\bhevc\b"
```

Expected: a line for the native `hevc` decoder (unlike the system ffmpeg, which prints nothing here).

Then do an end-to-end round trip:

```bash
vendor/ffmpeg/ffmpeg -hide_banner -f lavfi -i testsrc=size=320x240:rate=1:duration=1 -c:v libx265 -f mp4 -y /tmp/sample_hevc_vendor.mp4
vendor/ffmpeg/ffmpeg -hide_banner -i /tmp/sample_hevc_vendor.mp4 -f null -
rm -f /tmp/sample_hevc_vendor.mp4
```

Expected: both commands succeed (no "no decoder found" error) — confirms the vendored build can both encode and decode HEVC, closing the gap the system ffmpeg has.

- [ ] **Step 4: Commit**

```bash
git add scripts/install_ffmpeg.sh
git commit -m "Add script to install a dedicated ffmpeg build with HEVC decode support"
```

(The downloaded binaries under `vendor/` stay untracked, per `.gitignore` from Task 1 — anyone deploying this app runs the script once.)

---

### Task 3: Config schema for Auth and Transcode

**Files:**
- Modify: `config.ini.example`

**Interfaces:**
- Produces: documented `[Auth]` keys `USERNAME`, `PASSWORD_HASH`, `SECRET_KEY`, `SECURE_COOKIES` and `[Transcode]` keys `CACHE_DIR`, `MAX_CACHE_GB`, `FFMPEG_BIN`, `FFPROBE_BIN` — Tasks 5 and 8 read these via `configparser`.
- Produces: `[Paths] THUMBNAIL_DIR` pointed at the cache dir (redirects thumbnails off the network share — no code change needed since `services.py` already reads this key).

- [ ] **Step 1: Rewrite `config.ini.example` with the new sections**

```ini
[Paths]
VIDEO_DIR = roots
THUMBNAIL_DIR = /mnt/SSD2/arhiva_cache/thumbnails

[Subtitles]
EXTENSIONS = .vtt,.stt

[Videos]
EXTENSIONS = .ts,.mp4,.avi,.mov,.mkv,.webm

[Server]
HOST = 127.0.0.1
PORT = 8093
BASE_URL = /video/

[Display]
SHOW_HIDDEN = false

[Auth]
; Run `python scripts/set_password.py` to fill in USERNAME/PASSWORD_HASH/SECRET_KEY.
; Never edit PASSWORD_HASH by hand.
USERNAME =
PASSWORD_HASH =
SECRET_KEY =
; Set to false only for local testing over plain HTTP. Must be true in production
; (the app is only ever reached over HTTPS via the Apache reverse proxy).
SECURE_COOKIES = true

[Transcode]
CACHE_DIR = /mnt/SSD2/arhiva_cache
MAX_CACHE_GB = 350
FFMPEG_BIN = vendor/ffmpeg/ffmpeg
FFPROBE_BIN = vendor/ffmpeg/ffprobe
```

- [ ] **Step 2: Commit**

```bash
git add config.ini.example
git commit -m "Extend config schema with Auth and Transcode sections"
```

---

### Task 4: Password hashing and bootstrap script

**Files:**
- Create: `scripts/set_password.py`
- Create: `tests/test_set_password.py`

**Interfaces:**
- Produces: `scripts.set_password.hash_password(password: str) -> str`
- Produces: `scripts.set_password.write_credentials(config_path: str, username: str, password: str) -> None` — creates `[Auth]` section if missing, sets `USERNAME`/`PASSWORD_HASH`, generates `SECRET_KEY` only if not already present (so re-running the script to change the password doesn't invalidate existing sessions' signing key... actually it does invalidate sessions either way since it's a fresh process, but avoids needlessly rotating the key on every password change).
- Consumes: nothing outside the standard library + `bcrypt` (from Task 1).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_set_password.py
import configparser

from scripts.set_password import hash_password, write_credentials


def test_hash_password_produces_verifiable_bcrypt_hash():
    import bcrypt

    hashed = hash_password("hunter2")
    assert bcrypt.checkpw(b"hunter2", hashed.encode())


def test_write_credentials_creates_auth_section(tmp_path):
    config_path = tmp_path / "config.ini"
    config_path.write_text("[Paths]\nVIDEO_DIR = /tmp\n")

    write_credentials(str(config_path), "alice", "hunter2")

    config = configparser.ConfigParser()
    config.read(config_path)
    assert config["Auth"]["USERNAME"] == "alice"
    assert config["Auth"]["SECRET_KEY"]
    import bcrypt
    assert bcrypt.checkpw(b"hunter2", config["Auth"]["PASSWORD_HASH"].encode())


def test_write_credentials_preserves_existing_secret_key(tmp_path):
    config_path = tmp_path / "config.ini"
    config_path.write_text("[Auth]\nSECRET_KEY = keep-me\n")

    write_credentials(str(config_path), "alice", "hunter2")

    config = configparser.ConfigParser()
    config.read(config_path)
    assert config["Auth"]["SECRET_KEY"] == "keep-me"
```

Also create `scripts/__init__.py` (empty) so `scripts.set_password` is importable from tests.

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_set_password.py -v
```

Expected: `ModuleNotFoundError: No module named 'scripts.set_password'` (or similar import failure).

- [ ] **Step 3: Write `scripts/set_password.py`**

```python
#!/usr/bin/env python3
"""Set the Numa Film Archive login credentials.

Prompts for a password on stdin, hashes it with bcrypt, and writes the
hash into config.ini's [Auth] section (creating the section if needed).
The plaintext password is never written to disk or logged.
"""
import argparse
import configparser
import getpass
import os
import secrets
import sys

import bcrypt

DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config.ini")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def write_credentials(config_path: str, username: str, password: str) -> None:
    config = configparser.ConfigParser()
    config.read(config_path)
    if not config.has_section("Auth"):
        config.add_section("Auth")
    config.set("Auth", "USERNAME", username)
    config.set("Auth", "PASSWORD_HASH", hash_password(password))
    if not config.has_option("Auth", "SECRET_KEY") or not config.get("Auth", "SECRET_KEY"):
        config.set("Auth", "SECRET_KEY", secrets.token_hex(32))
    if not config.has_option("Auth", "SECURE_COOKIES"):
        config.set("Auth", "SECURE_COOKIES", "true")
    with open(config_path, "w") as f:
        config.write(f)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default="admin")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    args = parser.parse_args()

    password = getpass.getpass("New password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords do not match.", file=sys.stderr)
        sys.exit(1)
    if len(password) < 8:
        print("Password must be at least 8 characters.", file=sys.stderr)
        sys.exit(1)

    write_credentials(args.config, args.username, password)
    print(f"Password set for user '{args.username}' in {args.config}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_set_password.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/__init__.py scripts/set_password.py tests/test_set_password.py
git commit -m "Add bcrypt-based credential bootstrap script"
```

---

### Task 5: Login/session auth gate

**Files:**
- Create: `auth.py`
- Modify: `main.py:7-23` (the `create_app` function)
- Create: `tests/test_auth.py`

**Interfaces:**
- Produces: `auth.init_auth(app: Flask, config: configparser.ConfigParser) -> None` — registers the `auth` blueprint, sets `app.secret_key`, session cookie config, and a `before_request` hook gating every route except `auth.login`, `auth.logout`, and `static`.
- Produces: blueprint routes `GET/POST /login` (endpoint `auth.login`), `GET /logout` (endpoint `auth.logout`).
- Consumes: `config["Auth"]["USERNAME"]`, `config["Auth"]["PASSWORD_HASH"]`, `config["Auth"]["SECRET_KEY"]`, `config.getboolean("Auth", "SECURE_COOKIES", fallback=True)` (from Task 3's schema).
- Consumes (for the template, wired properly in Task 6 — for now `templates/login.html` just needs to exist with a `{{ error }}` placeholder so this task's tests can pass): a minimal `templates/login.html` stub.

- [ ] **Step 1: Write a minimal `templates/login.html` stub**

(Task 6 replaces this with the fully branded version — this unblocks Task 5's tests without forward-referencing unwritten work.)

```html
<!DOCTYPE html>
<html>
<head><title>Numa Film Archive</title></head>
<body>
  <h1>NUMA FILM ARCHIVE</h1>
  {% if error %}<p class="error">{{ error }}</p>{% endif %}
  <form method="post">
    <input type="text" name="username">
    <input type="password" name="password">
    <button type="submit">Log in</button>
  </form>
</body>
</html>
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_auth.py
def test_root_redirects_to_login_when_not_authenticated(client):
    response = client.get("/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_login_page_accessible_without_session(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert b"NUMA FILM ARCHIVE" in response.data


def test_login_with_correct_credentials_grants_access(client):
    response = client.post(
        "/login",
        data={"username": "admin", "password": "testpass"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert response.request.path == "/"


def test_login_with_wrong_password_shows_error(client):
    response = client.post("/login", data={"username": "admin", "password": "wrong"})
    assert response.status_code == 200
    assert b"Invalid username or password" in response.data


def test_logout_clears_session(client):
    client.post("/login", data={"username": "admin", "password": "testpass"})
    client.get("/logout")
    response = client.get("/")
    assert response.status_code == 302
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
pytest tests/test_auth.py -v
```

Expected: failures (root currently returns 200 with no redirect — `auth.py` doesn't exist yet).

- [ ] **Step 4: Write `auth.py`**

```python
from datetime import timedelta

import bcrypt
from flask import Blueprint, current_app, redirect, render_template, request, session, url_for

auth_bp = Blueprint("auth", __name__)


def init_auth(app, config):
    app.secret_key = config.get("Auth", "SECRET_KEY")
    app.config["AUTH_USERNAME"] = config.get("Auth", "USERNAME")
    app.config["AUTH_PASSWORD_HASH"] = config.get("Auth", "PASSWORD_HASH").encode("utf-8")
    app.config["SESSION_COOKIE_SECURE"] = config.getboolean("Auth", "SECURE_COOKIES", fallback=True)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

    app.register_blueprint(auth_bp)

    @app.before_request
    def require_login():
        if request.endpoint in ("auth.login", "auth.logout", "static"):
            return None
        if not session.get("logged_in"):
            return redirect(url_for("auth.login"))
        return None


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "").encode("utf-8")
        expected_username = current_app.config["AUTH_USERNAME"]
        expected_hash = current_app.config["AUTH_PASSWORD_HASH"]
        if username == expected_username and bcrypt.checkpw(password, expected_hash):
            session["logged_in"] = True
            session.permanent = True
            return redirect(url_for("index"))
        error = "Invalid username or password"
    return render_template("login.html", error=error)


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
```

- [ ] **Step 5: Wire `init_auth` into `main.py`**

In `main.py`, inside `create_app`, right after `video_server = VideoServer(app, cache, config_path)` succeeds (before the `return app, video_server` line):

```python
    from auth import init_auth
    init_auth(app, video_server.config)
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
pytest tests/test_auth.py -v
```

Expected: 5 passed.

- [ ] **Step 7: Commit**

```bash
git add auth.py main.py templates/login.html tests/test_auth.py
git commit -m "Add session-cookie login gate protecting all routes"
```

---

### Task 6: Branded login page

**Files:**
- Modify: `templates/login.html` (replace Task 5's stub)
- Modify: `static/styles.css` (append login styles)
- Create: `static/image/numa-film-logo.png` (copied asset)
- Modify: `tests/test_auth.py` (one more assertion)

**Interfaces:**
- Consumes: `logo-pe-fundal-inchis.png` from `/home/numafilm/projectsend-v2/branding/` (existing Numa Film brand asset, dark-background variant, 1080x165 PNG).
- No new Python interfaces — this is template/CSS/asset only.

- [ ] **Step 1: Copy the logo asset into the app's static folder**

```bash
cp /home/numafilm/projectsend-v2/branding/logo-pe-fundal-inchis.png \
   /home/numafilm/arhiva/static/image/numa-film-logo.png
```

- [ ] **Step 2: Replace `templates/login.html` with the branded version**

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Numa Film Archive</title>
    <link rel="stylesheet" href="{{ url_for('static', filename='styles.css') }}">
    <link href="https://fonts.googleapis.com/css2?family=Kalnia:wght@100..700&display=swap" rel="stylesheet">
    <link rel="icon" type="image/x-icon" href="{{ url_for('static', filename='image/icon.png') }}">
</head>
<body>
    <div class="login-container">
        <img src="{{ url_for('static', filename='image/numa-film-logo.png') }}"
             alt="Numa Film" class="login-logo">
        <h1>NUMA FILM ARCHIVE</h1>
        {% if error %}
        <p class="login-error">{{ error }}</p>
        {% endif %}
        <form method="post" class="login-form">
            <input type="text" name="username" placeholder="Username" autofocus required>
            <input type="password" name="password" placeholder="Password" required>
            <button type="submit">Log in</button>
        </form>
    </div>
</body>
</html>
```

- [ ] **Step 3: Append login styles to `static/styles.css`**

```css
.login-container {
    max-width: 360px;
    margin: 10vh auto 0;
    padding: 2rem;
    text-align: center;
    background-color: var(--secondary-color);
    border-radius: 8px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
}

.login-logo {
    max-width: 100%;
    height: auto;
    margin-bottom: 1.5rem;
}

.login-error {
    color: #e07a5f;
    margin-bottom: 1rem;
}

.login-form {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
}

.login-form input {
    padding: 0.6rem;
    border-radius: 4px;
    border: 1px solid var(--tertiary-color);
    background-color: var(--nonary-color);
    font-family: inherit;
}

.login-form button {
    padding: 0.6rem;
    border-radius: 4px;
    border: none;
    background-color: var(--senary-color);
    color: var(--primary-color);
    font-family: inherit;
    cursor: pointer;
}

.login-form button:hover {
    background-color: var(--septenary-color);
}
```

- [ ] **Step 4: Add a logo-presence assertion to `tests/test_auth.py`**

Add to `test_login_page_accessible_without_session`:

```python
def test_login_page_accessible_without_session(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert b"NUMA FILM ARCHIVE" in response.data
    assert b"numa-film-logo.png" in response.data
```

(Replace the existing shorter version of this test with the above.)

- [ ] **Step 5: Run tests to verify they still pass**

```bash
pytest tests/test_auth.py -v
```

Expected: 5 passed.

- [ ] **Step 6: Manually verify the page renders correctly**

```bash
source venv/bin/activate
python scripts/set_password.py --config /tmp/manual_test_config.ini --username admin
# (enter a test password when prompted)
```

Then start the app pointed at a scratch config and open `http://127.0.0.1:8093/login` in a browser to confirm the logo and dark theme render as expected before moving on. Stop the server (Ctrl+C) once confirmed.

- [ ] **Step 7: Commit**

```bash
git add templates/login.html static/styles.css static/image/numa-film-logo.png tests/test_auth.py
git commit -m "Add branded Numa Film Archive login page"
```

---

### Task 7: Format compatibility probing

**Files:**
- Create: `transcode.py`
- Create: `tests/test_transcode_probe.py`

**Interfaces:**
- Produces: `transcode.probe_streams(ffprobe_bin: str, path: str) -> dict` — returns `{"video_codec": str | None, "audio_codec": str | None}`.
- Produces: `transcode.is_browser_compatible(ffprobe_bin: str, path: str) -> bool`.
- Produces: `transcode.DIRECT_PLAY_RULES: dict[str, dict]` — extension → allowed video/audio codec sets, used by `is_browser_compatible`.

**Design note:** compatibility is decided by **file extension + actual codec**, not by ffprobe's reported container name — `.mov` and `.mp4` both report the same ffprobe `format_name` (`mov,mp4,m4a,3gp,3g2,mj2`) since they share the QuickTime/ISO-BMFF family, so container name alone can't tell a browser-playable `.mp4` apart from a ProRes-in-`.mov` file. Trusting the extension (which in practice matches how the file is actually authored) plus the real codec is the reliable signal.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_transcode_probe.py
import shutil
import subprocess

import pytest

from transcode import is_browser_compatible, probe_streams

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"


def make_sample(tmp_path, filename, video_codec="libopenh264", audio_codec=None):
    path = tmp_path / filename
    cmd = [
        FFMPEG, "-hide_banner", "-y",
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=1:duration=1",
    ]
    if audio_codec:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-shortest", "-c:a", audio_codec]
    cmd += ["-c:v", video_codec, str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def test_probe_streams_detects_video_codec(tmp_path):
    sample = make_sample(tmp_path, "sample.mp4")
    result = probe_streams(FFPROBE, str(sample))
    assert result["video_codec"] == "h264"


def test_compatible_mp4_h264_is_browser_compatible(tmp_path):
    sample = make_sample(tmp_path, "sample.mp4")
    assert is_browser_compatible(FFPROBE, str(sample)) is True


def test_avi_container_is_never_browser_compatible(tmp_path):
    sample = make_sample(tmp_path, "sample.avi")
    assert is_browser_compatible(FFPROBE, str(sample)) is False


def test_unreadable_file_is_not_compatible(tmp_path):
    bogus = tmp_path / "not_a_video.mp4"
    bogus.write_text("not actually a video")
    assert is_browser_compatible(FFPROBE, str(bogus)) is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_transcode_probe.py -v
```

Expected: `ModuleNotFoundError: No module named 'transcode'`.

- [ ] **Step 3: Write `transcode.py` (probing portion only for now)**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_transcode_probe.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add transcode.py tests/test_transcode_probe.py
git commit -m "Add ffprobe-based browser-compatibility detection"
```

---

### Task 8: Transcode job queue and cache eviction

**Files:**
- Modify: `transcode.py` (append `TranscodeManager`)
- Create: `tests/test_transcode_manager.py`

**Interfaces:**
- Produces: `transcode.TranscodeManager(ffmpeg_bin: str, ffprobe_bin: str, cache_dir: str, max_cache_bytes: int)`.
- Produces: `TranscodeManager.cache_path(source_path: str) -> str`.
- Produces: `TranscodeManager.status(source_path: str) -> str` — one of `"not_started"`, `"processing"`, `"ready"`, `"error"`.
- Produces: `TranscodeManager.enqueue(source_path: str) -> str` — returns the resulting status (`"ready"` if already cached, `"processing"` otherwise); idempotent for concurrent calls on the same source.
- Consumes: `transcode.probe_streams`/`is_browser_compatible` are unaffected by this task (kept as free functions from Task 7).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_transcode_manager.py
import os
import shutil
import subprocess
import time

from transcode import TranscodeManager

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"


def make_sample(tmp_path, filename="incompatible.avi"):
    path = tmp_path / filename
    subprocess.run(
        [
            FFMPEG, "-hide_banner", "-y",
            "-f", "lavfi", "-i", "testsrc=size=160x120:rate=1:duration=1",
            "-c:v", "libopenh264", str(path),
        ],
        check=True, capture_output=True,
    )
    return path


def wait_for_status(manager, source_path, target, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = manager.status(str(source_path))
        if status == target:
            return status
        time.sleep(0.2)
    return manager.status(str(source_path))


def test_status_is_not_started_before_enqueue(tmp_path):
    sample = make_sample(tmp_path)
    manager = TranscodeManager(FFMPEG, FFPROBE, str(tmp_path / "cache"), max_cache_bytes=10**9)
    assert manager.status(str(sample)) == "not_started"


def test_enqueue_transcodes_to_ready(tmp_path):
    sample = make_sample(tmp_path)
    manager = TranscodeManager(FFMPEG, FFPROBE, str(tmp_path / "cache"), max_cache_bytes=10**9)

    manager.enqueue(str(sample))
    status = wait_for_status(manager, sample, "ready")

    assert status == "ready"
    assert os.path.isfile(manager.cache_path(str(sample)))


def test_enqueue_is_idempotent_while_processing(tmp_path):
    sample = make_sample(tmp_path)
    manager = TranscodeManager(FFMPEG, FFPROBE, str(tmp_path / "cache"), max_cache_bytes=10**9)

    first = manager.enqueue(str(sample))
    second = manager.enqueue(str(sample))

    assert first in ("processing", "ready")
    assert second in ("processing", "ready")
    wait_for_status(manager, sample, "ready")


def test_cache_eviction_removes_oldest_when_over_cap(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    # Two fake cached proxies, "old" touched before "new".
    old = cache_dir / "old.mp4"
    new = cache_dir / "new.mp4"
    old.write_bytes(b"0" * 1000)
    new.write_bytes(b"0" * 1000)
    old_time = time.time() - 1000
    os.utime(old, (old_time, old_time))

    manager = TranscodeManager(FFMPEG, FFPROBE, str(cache_dir), max_cache_bytes=1500)
    manager._evict_if_needed()

    assert not old.exists()
    assert new.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_transcode_manager.py -v
```

Expected: `ImportError: cannot import name 'TranscodeManager' from 'transcode'`.

- [ ] **Step 3: Append `TranscodeManager` to `transcode.py`**

```python
import hashlib
import queue
import threading


class TranscodeManager:
    def __init__(self, ffmpeg_bin: str, ffprobe_bin: str, cache_dir: str, max_cache_bytes: int):
        self.ffmpeg_bin = ffmpeg_bin
        self.ffprobe_bin = ffprobe_bin
        self.cache_dir = cache_dir
        self.max_cache_bytes = max_cache_bytes
        os.makedirs(cache_dir, exist_ok=True)
        self._jobs = {}
        self._lock = threading.Lock()
        self._queue = queue.Queue()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

    def _cache_key(self, source_path: str) -> str:
        mtime = os.path.getmtime(source_path)
        raw = f"{os.path.abspath(source_path)}:{mtime}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def cache_path(self, source_path: str) -> str:
        return os.path.join(self.cache_dir, self._cache_key(source_path) + ".mp4")

    def status(self, source_path: str) -> str:
        if os.path.isfile(self.cache_path(source_path)):
            return "ready"
        key = self._cache_key(source_path)
        with self._lock:
            job = self._jobs.get(key)
        return job["status"] if job else "not_started"

    def enqueue(self, source_path: str) -> str:
        if os.path.isfile(self.cache_path(source_path)):
            return "ready"
        key = self._cache_key(source_path)
        with self._lock:
            existing = self._jobs.get(key)
            if existing:
                return existing["status"]
            self._jobs[key] = {"status": "processing", "source": source_path}
        self._queue.put(source_path)
        return "processing"

    def _worker_loop(self):
        while True:
            source_path = self._queue.get()
            key = self._cache_key(source_path)
            dest = self.cache_path(source_path)
            tmp_dest = dest + ".tmp"
            try:
                subprocess.run(
                    [
                        self.ffmpeg_bin, "-y", "-hwaccel", "auto", "-i", source_path,
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                        "-c:a", "aac", "-movflags", "+faststart", tmp_dest,
                    ],
                    check=True, capture_output=True, timeout=3600,
                )
                os.replace(tmp_dest, dest)
                with self._lock:
                    self._jobs[key]["status"] = "ready"
                self._evict_if_needed()
            except Exception as e:
                logger.error(f"Transcode failed for {source_path}: {e}")
                with self._lock:
                    self._jobs[key]["status"] = "error"
                if os.path.exists(tmp_dest):
                    os.remove(tmp_dest)

    def _evict_if_needed(self):
        entries = []
        total = 0
        for name in os.listdir(self.cache_dir):
            full = os.path.join(self.cache_dir, name)
            if not os.path.isfile(full):
                continue
            st = os.stat(full)
            total += st.st_size
            entries.append((st.st_atime, st.st_size, full))
        if total <= self.max_cache_bytes:
            return
        entries.sort()
        for _atime, size, full in entries:
            if total <= self.max_cache_bytes:
                break
            os.remove(full)
            total -= size
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_transcode_manager.py -v
```

Expected: 4 passed. (The eviction test calls `manager._evict_if_needed()` directly — acceptable here since it's testing that specific internal policy in isolation; the end-to-end eviction-after-transcode path is covered manually in Task 12.)

- [ ] **Step 5: Commit**

```bash
git add transcode.py tests/test_transcode_manager.py
git commit -m "Add background transcode job queue with size-capped cache eviction"
```

---

### Task 9: Wire transcode routes, playback UI, and subtitle cache redirect

**Files:**
- Modify: `main.py` (call `init_transcode`)
- Create: `transcode.py` addition — `init_transcode` (append to existing file)
- Modify: `services.py:144-173` (`play_video`, rewritten), `services.py:41-51` (`_configure_routes`, one route added), plus a new `subtitle_cache_dir` property and `serve_cached_subtitle` method (the `/transcode/*`, `/transcode-status/*`, `/video-proxy/*` routes themselves are registered separately by `init_transcode` directly on `app`, not in this class)
- Modify: `utils.py:49-71` (`extract_subtitles`)
- Modify: `templates/video.html`
- Create: `tests/test_transcode_routes.py`

**Interfaces:**
- Produces: `transcode.init_transcode(app, config, video_dir) -> TranscodeManager` — reads `[Transcode]` config, builds a `TranscodeManager`, registers `POST /transcode/<path:filename>`, `GET /transcode-status/<path:filename>`, `GET /video-proxy/<path:filename>`, and stashes `app.config["IS_COMPATIBLE_FN"]` (a `str -> bool` closure over the configured `ffprobe_bin`).
- Consumes: `TranscodeManager` (Task 8), `is_browser_compatible` (Task 7).
- Modifies: `utils.extract_subtitles(video_path: str, video_dir: str, subtitle_dir: str, ffmpeg_bin: str) -> Optional[str]` — **signature change** from the upstream `extract_subtitles(video_path: str) -> Optional[str]`. Output now goes to `subtitle_dir` (mirroring the file's path relative to `video_dir`) instead of next to the source file, and uses the configured `ffmpeg_bin` instead of assuming `ffmpeg` is on `PATH`.
- Modifies: `services.py`'s `play_video` now passes `needs_transcode: bool` to `templates/video.html`.

- [ ] **Step 1: Write the failing route tests**

```python
# tests/test_transcode_routes.py
import subprocess
import shutil

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"


def make_incompatible_video(video_dir, filename="clip.avi"):
    path = video_dir / filename
    subprocess.run(
        [
            FFMPEG, "-hide_banner", "-y",
            "-f", "lavfi", "-i", "testsrc=size=160x120:rate=1:duration=1",
            "-c:v", "libopenh264", str(path),
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_transcode_routes.py -v
```

Expected: 404s / failures — routes and `needs_transcode` don't exist yet.

- [ ] **Step 3: Append `init_transcode` to `transcode.py`**

```python
from flask import abort, jsonify, send_file


def init_transcode(app, config, video_dir):
    ffmpeg_bin = config.get("Transcode", "FFMPEG_BIN", fallback="ffmpeg")
    ffprobe_bin = config.get("Transcode", "FFPROBE_BIN", fallback="ffprobe")
    cache_dir = os.path.join(config.get("Transcode", "CACHE_DIR"), "transcoded")
    max_cache_gb = config.getfloat("Transcode", "MAX_CACHE_GB", fallback=350)
    manager = TranscodeManager(ffmpeg_bin, ffprobe_bin, cache_dir, int(max_cache_gb * 1024**3))

    def resolve(filename):
        full_path = os.path.join(video_dir, filename)
        if not os.path.isfile(full_path):
            abort(404)
        return full_path

    @app.route("/transcode/<path:filename>", methods=["POST"])
    def start_transcode(filename):
        status = manager.enqueue(resolve(filename))
        return jsonify({"status": status})

    @app.route("/transcode-status/<path:filename>")
    def transcode_status(filename):
        return jsonify({"status": manager.status(resolve(filename))})

    @app.route("/video-proxy/<path:filename>")
    def video_proxy(filename):
        cache_path = manager.cache_path(resolve(filename))
        if not os.path.isfile(cache_path):
            abort(404)
        return send_file(cache_path)

    app.config["IS_COMPATIBLE_FN"] = lambda path: is_browser_compatible(ffprobe_bin, path)
    app.config["TRANSCODE_MANAGER"] = manager
    return manager
```

- [ ] **Step 4: Wire `init_transcode` into `main.py`**

Add alongside the `init_auth` call from Task 5:

```python
    from transcode import init_transcode
    init_transcode(app, video_server.config, video_server.video_dir)
```

- [ ] **Step 5: Update `services.py`: add a `subtitle_cache_dir` property, a `serve_cached_subtitle` route, and rewrite `play_video`**

The `.mkv` branch's subtitle output now lives under the cache dir (Task 9's whole point, per the spec, is to stop writing generated files onto the NAS), not next to the source file in `self.video_dir` — so it can no longer be served through the existing `/video/<path>` route (which resolves paths relative to `video_dir`). It needs its own small route. Externally-provided `.vtt` files that already sit beside the source video are unaffected and keep using `/video/<path>` as before.

First, add this property near the other properties (after `thumbnail_dir`, around line 70):

```python
    @property
    def subtitle_cache_dir(self) -> str:
        return os.path.join(
            self.config.get("Transcode", "CACHE_DIR", fallback=self.thumbnail_dir), "subtitles"
        )
```

Add the new route registration in `_configure_routes` (currently lines 41-51), alongside the existing `add_url_rule` calls:

```python
        self.app.add_url_rule(
            "/subtitle-cache/<path:filename>", "serve_cached_subtitle", self.serve_cached_subtitle
        )
```

Add the route handler itself, next to `serve_file`:

```python
    def serve_cached_subtitle(self, filename):
        full_path = os.path.join(self.subtitle_cache_dir, filename)
        if os.path.isfile(full_path):
            return send_file(full_path)
        abort(404)
```

Finally, replace the whole `play_video` method (currently at lines 144-173) with:

```python
    def play_video(self, filename):
        full_path = os.path.join(self.video_dir, filename)
        if not os.path.isfile(full_path):
            abort(404)

        is_compatible_fn = self.app.config.get("IS_COMPATIBLE_FN")
        needs_transcode = not is_compatible_fn(full_path) if is_compatible_fn else False

        subs = []
        if filename.lower().endswith(".mkv"):
            subtitle_path = self.executor.submit(
                extract_subtitles,
                full_path,
                self.video_dir,
                self.subtitle_cache_dir,
                self.config.get("Transcode", "FFMPEG_BIN", fallback="ffmpeg"),
            ).result()
            if subtitle_path and os.path.isfile(subtitle_path):
                sub_rel_path = os.path.relpath(subtitle_path, self.subtitle_cache_dir)
                subs = [("serve_cached_subtitle", sub_rel_path)]
        else:
            subtitle_path = os.path.splitext(full_path)[0] + ".vtt"
            if os.path.isfile(subtitle_path):
                sub_rel_path = os.path.join(os.path.dirname(filename), os.path.basename(subtitle_path))
                subs = [("serve_file", sub_rel_path)]

        thumbnail_path = get_thumbnail_path(filename, self.thumbnail_dir)

        return render_template(
            "video.html",
            video_path=filename,
            subs=subs,
            video_title=os.path.basename(filename),
            thumbnail_path=thumbnail_path,
            needs_transcode=needs_transcode,
        )
```

`subs` is now a list of `(endpoint, path)` tuples rather than plain path strings — `templates/video.html`'s subtitle `<track>` loop (Step 7 below) is written to match this shape.

- [ ] **Step 6: Update `utils.extract_subtitles`**

Replace the existing function in `utils.py`:

```python
def extract_subtitles(video_path: str, video_dir: str, subtitle_dir: str, ffmpeg_bin: str = "ffmpeg") -> Optional[str]:
    rel_path = os.path.relpath(video_path, video_dir)
    output_path = os.path.join(subtitle_dir, os.path.splitext(rel_path)[0] + ".vtt")
    if not os.path.exists(output_path):
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        try:
            subprocess.run(
                [ffmpeg_bin, "-hwaccel", "auto", "-i", video_path, "-map", "0:s:0", output_path],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError as e:
            logging.error(f"Error extracting subtitles: {e}")
            return None
    return output_path
```

- [ ] **Step 7: Update `templates/video.html`**

Replace the `<video>` block and add the polling script:

```html
            <video id="video-player" controls
                   data-start-url="{{ url_for('start_transcode', filename=video_path) }}"
                   data-status-url="{{ url_for('transcode_status', filename=video_path) }}"
                   data-proxy-url="{{ url_for('video_proxy', filename=video_path) }}">
                {% if not needs_transcode %}
                <source src="{{ url_for('serve_file', filename=video_path) }}" type="video/mp4">
                {% endif %}
                Your browser does not support the video tag.
                {% for endpoint, sub_path in subs %}
                <track src="{{ url_for(endpoint, filename=sub_path) }}" kind="subtitles" srclang="en" label="English" default>
                {% endfor %}
            </video>
            {% if needs_transcode %}
            <p id="transcode-status">Preparing video for playback&hellip;</p>
            {% endif %}
```

And add this script block before the closing `</body>`, alongside the existing script:

```html
    <script>
        document.addEventListener('DOMContentLoaded', function() {
            const videoPlayer = document.getElementById('video-player');
            const statusEl = document.getElementById('transcode-status');
            if (!statusEl || !videoPlayer) return;

            fetch(videoPlayer.dataset.startUrl, { method: 'POST' }).then(poll);

            function poll() {
                fetch(videoPlayer.dataset.statusUrl)
                    .then((r) => r.json())
                    .then((data) => {
                        if (data.status === 'ready') {
                            statusEl.remove();
                            const source = document.createElement('source');
                            source.src = videoPlayer.dataset.proxyUrl;
                            source.type = 'video/mp4';
                            videoPlayer.appendChild(source);
                            videoPlayer.load();
                        } else if (data.status === 'error') {
                            statusEl.textContent = 'Could not prepare this video for playback.';
                        } else {
                            setTimeout(poll, 2000);
                        }
                    });
            }
        });
    </script>
```

- [ ] **Step 8: Run tests to verify they pass**

```bash
pytest tests/test_transcode_routes.py -v
```

Expected: 3 passed.

- [ ] **Step 9: Run the full test suite**

```bash
pytest -v
```

Expected: all tests across every task so far pass together (confirms the `services.py`/`utils.py` changes didn't break Tasks 1-8's tests).

- [ ] **Step 10: Commit**

```bash
git add main.py transcode.py services.py utils.py templates/video.html tests/test_transcode_routes.py
git commit -m "Wire on-demand transcoding into playback, redirect subtitle cache off the NAS"
```

---

### Task 10: Multi-root symlink folder

**Files:**
- Create: `roots/.gitkeep`
- Create: `tests/test_multi_root.py`

**Interfaces:**
- No new Python interfaces — this validates that the existing (unmodified) `get_directory_structure` in `services.py` follows symlinks, since `VIDEO_DIR` will point at `roots/` in production.

- [ ] **Step 1: Create the symlink folder placeholder**

```bash
mkdir -p /home/numafilm/arhiva/roots
touch /home/numafilm/arhiva/roots/.gitkeep
```

- [ ] **Step 2: Write a test proving symlinked roots are browsed**

```python
# tests/test_multi_root.py
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
```

- [ ] **Step 3: Run the test**

```bash
pytest tests/test_multi_root.py -v
```

Expected: passes immediately — `os.walk` (used by `get_directory_structure`) follows symlinks by default, so no code change is needed. This test exists to lock in that behavior so a future dependency bump or refactor can't silently break multi-root browsing.

- [ ] **Step 4: Commit**

```bash
git add roots/.gitkeep tests/test_multi_root.py
git commit -m "Add roots/ symlink folder for multi-root browsing, with a regression test"
```

---

### Task 11: Deployment — systemd service and Apache vhost

**Files:**
- Create: `deploy/arhiva.service`
- Create: `deploy/arhiva.conf`
- Create: `deploy/arhiva-le-ssl.conf`
- Create: `deploy/README.md`

**Interfaces:** none (infrastructure/config files, installed manually per `deploy/README.md`).

- [ ] **Step 1: Write `deploy/arhiva.service`**

```ini
[Unit]
Description=Numa Film Archive video browser
After=network.target

[Service]
WorkingDirectory=/home/numafilm/arhiva
ExecStart=/home/numafilm/arhiva/venv/bin/gunicorn -w 2 -b 127.0.0.1:8093 main:app
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

- [ ] **Step 2: Write `deploy/arhiva.conf`** (HTTP vhost, redirects to HTTPS — matches `newsletter.conf`'s pattern)

```apache
<VirtualHost *:80>
    ServerName arhiva.numafilm.ro

    RewriteEngine on
    RewriteCond %{SERVER_NAME} =arhiva.numafilm.ro
    RewriteRule ^ https://%{SERVER_NAME}%{REQUEST_URI} [END,NE,R=permanent]
</VirtualHost>
```

- [ ] **Step 3: Write `deploy/arhiva-le-ssl.conf`** (HTTPS vhost — matches `n8n-le-ssl.conf`/`ai-le-ssl.conf`'s reverse-proxy pattern)

```apache
<IfModule mod_ssl.c>
<VirtualHost *:443>
    ServerName arhiva.numafilm.ro

    SSLCertificateFile /etc/letsencrypt/live/arhiva.numafilm.ro/fullchain.pem
    SSLCertificateKeyFile /etc/letsencrypt/live/arhiva.numafilm.ro/privkey.pem
    Include /etc/letsencrypt/options-ssl-apache.conf

    ProxyPreserveHost On
    ProxyPass / http://127.0.0.1:8093/
    ProxyPassReverse / http://127.0.0.1:8093/
</VirtualHost>
</IfModule>
```

- [ ] **Step 4: Write `deploy/README.md`** with the manual install steps

```markdown
# Deploying Numa Film Archive

Run once, in order, on the server:

1. `cd ~/arhiva && python3.14 -m venv venv && source venv/bin/activate && pip install -r requirements.txt`
2. `./scripts/install_ffmpeg.sh`
3. `cp config.ini.example config.ini` and fill in `[Paths] VIDEO_DIR = /home/numafilm/arhiva/roots` (absolute path).
4. `ln -s /mnt/norman-manea ~/arhiva/roots/norman-manea` (repeat per mount you want browsable).
5. `python scripts/set_password.py --username <your-username>` — sets the login password.
6. `mkdir -p ~/.config/systemd/user && cp deploy/arhiva.service ~/.config/systemd/user/`
7. `systemctl --user daemon-reload && systemctl --user enable --now arhiva.service`
8. `systemctl --user status arhiva.service` — confirm `active (running)`.
9. Add a DNS record for `arhiva.numafilm.ro` pointing at this server (same place the other `numafilm.ro` subdomains are managed).
10. `sudo cp deploy/arhiva.conf /etc/httpd/conf.d/arhiva.conf && sudo systemctl reload httpd`
11. `sudo certbot --apache -d arhiva.numafilm.ro` — obtains the cert and can auto-write the SSL vhost; if it doesn't match `deploy/arhiva-le-ssl.conf` exactly, replace the generated file's `ProxyPass`/`ProxyPassReverse` block with the one from `deploy/arhiva-le-ssl.conf`.
12. `sudo systemctl reload httpd`
13. Enable lingering so the user service survives reboots without a login session: `sudo loginctl enable-linger numafilm`
```

- [ ] **Step 5: Commit**

```bash
git add deploy/
git commit -m "Add systemd service and Apache vhost templates for deployment"
```

---

### Task 12: End-to-end manual verification

**Files:** none (verification only).

- [ ] **Step 1: Run the full automated test suite one final time**

```bash
cd /home/numafilm/arhiva && source venv/bin/activate && pytest -v
```

Expected: all tests pass.

- [ ] **Step 2: Follow `deploy/README.md` steps 1-8** to bring the service up locally (before doing DNS/Apache/certbot), pointing `VIDEO_DIR` at a `roots/` folder symlinked to `/mnt/norman-manea`.

- [ ] **Step 3: Manual browser checks against `http://127.0.0.1:8093`** (via an SSH tunnel or on-box browser, since Apache/TLS isn't wired up yet at this point):
  - Confirm `/` redirects to `/login`; log in with the password set in step 1; confirm redirect to `/`.
  - Browse into the `norman-manea` folder; confirm the tree matches the NAS contents.
  - Play a file that direct-plays (browser-compatible codec) — confirm no "Preparing…" message appears and seeking works.
  - Play a file that needs transcoding — confirm "Preparing video for playback…" appears, then playback starts once ready; reload the same `/play/...` page and confirm it plays immediately the second time (served from cache, `IS_COMPATIBLE_FN` still says incompatible but `/video-proxy/...` already has the file).
  - Visit `/logout`, confirm redirected to `/login` and that `/` now redirects again.

- [ ] **Step 4: Complete `deploy/README.md` steps 9-13** (DNS, Apache vhost, certbot) and confirm `https://arhiva.numafilm.ro` serves the same login page with a valid certificate, and that plain `http://arhiva.numafilm.ro` redirects to `https://`.

- [ ] **Step 5: Confirm cache eviction in the real deployment**

```bash
du -sh /mnt/SSD2/arhiva_cache
```

Note the size after a few transcodes; this is a spot check, not a full cap-triggering test (that's covered by Task 8's automated test) — just confirming the directory is actually being written to at the configured path and not, e.g., accidentally left at a stale default.
