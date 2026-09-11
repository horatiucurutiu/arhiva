# Numa Film Archive Browser — Design Spec

Date: 2026-09-04

## Background

`horatiucurutiu/arhiva` is a fork of [`1999AZZAR/video-browser`](https://github.com/1999AZZAR/video-browser)
(`upstream` remote), a small Flask app that browses a directory tree of video
files and streams them with HTML5 `<video>`, generating thumbnails and
extracting subtitles via `ffmpeg`. We're extending it into "Numa Film
Archive": a branded, authenticated, internet-facing browser for the
`#Norman Manea - Archive` folder (mounted at `/mnt/norman-manea`, a CIFS
share on the NAS at 192.168.0.90) and any other local mounts added later.

## Goals

- Browse and play video files from one or more root folders (multi-root,
  extensible without code changes).
- Play back files in formats the browser can't decode natively (ProRes,
  MXF, etc.) by transcoding on demand to H.264 MP4, with the result cached
  so repeat plays are instant.
- Gate all access behind a login page branded as "Numa Film Archive"
  (existing Numa Film logo), not the browser's native Basic Auth prompt.
- Reachable at `https://arhiva.numafilm.ro` from anywhere, following the
  same Apache + Let's Encrypt reverse-proxy pattern already used for
  `n8n.numafilm.ro`, `ai.numafilm.ro`, etc.

## Non-goals

- No multi-user accounts — one shared login.
- No transcoding format beyond H.264/AAC MP4 proxies (no HLS/adaptive
  bitrate, no hardware-accelerated transcoding pipeline beyond ffmpeg's
  `-hwaccel auto` best-effort).
- No editing/tagging/organizing metadata — pure browse-and-play.
- No changes to how the upstream project's directory-scanning/caching
  logic works beyond what's needed for multi-root and format handling.

## Architecture overview

```
Browser (anywhere on the internet)
   │  HTTPS
   ▼
Apache vhost arhiva.numafilm.ro (Let's Encrypt cert)
   │  reverse proxy, plain HTTP
   ▼
gunicorn on 127.0.0.1:8093 (systemd --user service "arhiva")
   │
   ▼
Flask app (this fork)
   ├── auth.py         — login/session gate (all routes protected except /login, /static)
   ├── services.py      — existing directory scan/browse/play/thumbnail routes (upstream)
   ├── transcode.py     — NEW: format probe, on-demand ffmpeg job queue, cache eviction
   └── roots/            — NEW: symlink folder, one symlink per browsable mount
         └── norman-manea -> /mnt/norman-manea

Cache: /mnt/SSD2/arhiva_cache/
   ├── thumbnails/   (existing THUMBNAIL_DIR, redirected here instead of inside the NAS mount)
   ├── subtitles/    (redirected extract_subtitles() output here instead of writing .vtt next to source files on the NAS)
   └── transcoded/   (H.264 MP4 proxies, keyed by source path + mtime)
```

Rationale for redirecting thumbnails/subtitles into the local cache dir
(upstream default writes them next to the source file): we don't want a
read-mostly network archive gaining stray `.vtt`/thumbnail files, and
writes to `/mnt/norman-manea` are slower than local NVMe.

## Components

### 1. Multi-root browsing (`roots/` symlink folder)

`VIDEO_DIR` in `config.ini` points at `~/arhiva/roots/`. Each mount to
browse gets a symlink placed there, e.g.:

```
ln -s /mnt/norman-manea ~/arhiva/roots/norman-manea
```

Adding/removing a root is just adding/removing a symlink + restarting the
service (`os.walk` follows symlinks by default, so no code change is
needed in the existing directory-scan logic). The top level of the archive
UI becomes one folder per symlink.

### 2. Format compatibility & on-demand transcoding (`transcode.py`, new)

On each `/play/<filename>` request:

1. Probe the file with `ffmpeg.probe()` (via the already-vendored
   `ffmpeg-python` dependency) to get container + video codec + audio
   codec. Cache the probe result (keyed by path + mtime) so repeat visits
   don't re-probe.
2. **Compatible** if container is `mp4` or `webm` AND video codec is one
   of `h264`, `hevc`, `vp9`, `av1` AND audio codec is one of `aac`,
   `opus`, `mp3`. → template embeds `<video src="/video/<filename>">`
   (existing `serve_file` route, unchanged, range-request streaming via
   Werkzeug's `send_file`).
3. **Not compatible** → template shows a "Preparing video…" state and:
   - `POST /transcode/<filename>` — enqueue a transcode job if none
     exists yet for this (path, mtime) key; returns immediately.
   - `GET /transcode-status/<filename>` — polled every ~2s by the page;
     returns `not_started | processing | ready | error`.
   - Once `ready`, page sets `<video src="/video-proxy/<filename>">`.
   - `GET /video-proxy/<filename>` — serves the cached MP4 proxy from
     `/mnt/SSD2/arhiva_cache/transcoded/` via `send_file` (range-request
     support is the same Werkzeug mechanism as the existing route).

Job queue: a single background worker thread consuming a `queue.Queue`,
one `ffmpeg` transcode at a time (avoids CPU contention on the server;
acceptable since this is low-concurrency personal use). Each job runs:

```
ffmpeg -hwaccel auto -i <source> -c:v libx264 -preset veryfast -crf 20 \
       -c:a aac -movflags +faststart <cache_dir>/<hash>.mp4
```

Cache key: `sha1(source_absolute_path + ":" + source_mtime)` — a source
file changing invalidates its cached proxy automatically.

**Cache eviction:** after each completed transcode, if
`/mnt/SSD2/arhiva_cache/transcoded/` exceeds `MAX_CACHE_GB` (config,
default 350), delete files ordered by last-access time (`atime`) until
back under the cap. Runs synchronously at the end of the transcode job
(simple, no separate cron needed).

### 3. Authentication (`auth.py`, new)

- `config.ini` gains an `[Auth]` section: `USERNAME`, `PASSWORD_HASH`
  (bcrypt), `SECRET_KEY` (for Flask session signing).
- `POST /login` — validates against `PASSWORD_HASH`, sets a signed
  session cookie (`flask.session`) with a long-ish expiry (30 days,
  "remember me" implicitly — single shared login, low risk).
- `GET /login` — renders `templates/login.html`: Numa Film logo
  (`logo-pe-fundal-inchis.png`, copied into `static/image/`), "NUMA FILM
  ARCHIVE" heading, username/password form, dark theme matching the
  logo's dark-background variant.
- `GET /logout` — clears session.
- `before_request` hook on the Flask app: any route other than
  `/login`, `/logout`, and `/static/*` redirects to `/login` if no valid
  session.
- Password is set once during deployment via a small one-off script
  (`scripts/set_password.py`) that prompts for a password and writes its
  bcrypt hash into `config.ini` — the plaintext password is never stored
  or logged, and isn't a file I need to see.

### 4. Deployment

- **App**: `systemd --user` unit `arhiva.service`, running
  `gunicorn -w 2 -b 127.0.0.1:8093 main:app` (matches `gunicorn` already
  being a listed dependency), `WorkingDirectory=/home/numafilm/PROJECTS/arhiva`,
  `Restart=on-failure`, enabled so it survives reboot (same pattern as
  `casting.service` / `cotatii.service`).
- **Reverse proxy**: new Apache vhost pair
  `/etc/httpd/conf.d/arhiva.conf` (port 80 → redirect to HTTPS) and
  `/etc/httpd/conf.d/arhiva-le-ssl.conf` (port 443, `ProxyPass` /
  `ProxyPassReverse` to `http://127.0.0.1:8093/`), certificate obtained
  via `certbot` the same way as the other `numafilm.ro` subdomains.
- **DNS**: `arhiva.numafilm.ro` A/CNAME record added wherever the other
  `numafilm.ro` subdomains are managed (Cloudflare, per existing
  `remoteip-cloudflare.conf`).
- **Firewall**: none needed beyond what's already open for 80/443 — the
  app port 8093 stays bound to `127.0.0.1` only, never exposed directly.

## Error handling

- Missing/unreadable source file → existing `404.html` (upstream
  behavior, unchanged).
- `ffprobe`/`ffmpeg` failure during transcode → job status becomes
  `error`; the page shows an error message with a "download original"
  fallback link (`/video/<filename>`, which will simply not play in most
  browsers for truly incompatible formats, but at least isn't a dead
  end).
- Cache directory full even after eviction (disk genuinely out of space)
  → transcode job fails fast with a clear error status rather than
  filling the disk.
- Auth: invalid login shows an inline error on `login.html`; no user
  enumeration (generic "invalid username or password").

## Testing plan

Manual verification (no existing automated test suite in the upstream
project to extend, and given single-user low-stakes usage, adding a full
test harness is out of scope for v1):

1. Browse into `roots/norman-manea`, confirm folder tree matches the NAS
   contents.
2. Play an existing browser-compatible file directly (no transcode
   triggered) — confirm seeking works.
3. Play a ProRes/MOV file — confirm "Preparing…" state, then playback
   once ready, then confirm the second play of the same file is
   instant (served from cache, no re-transcode).
4. Confirm cache eviction logic with a temporary low `MAX_CACHE_GB` in a
   test config — old proxies get deleted once the cap is exceeded.
5. Confirm `/login` gates every route; confirm logout works; confirm
   session persists across browser restarts (cookie).
6. Confirm `https://arhiva.numafilm.ro` resolves, redirects HTTP→HTTPS,
   and the cert is valid.

## Addendum: dedicated ffmpeg build (found during implementation planning)

The system `ffmpeg` on this host (also used by the `casting`/`cotatii` apps)
has HEVC/H.265 decoding entirely unavailable: no software decoder is
compiled in, and `-hwaccel auto` can't help because there's no decoder to
attach hardware acceleration to in the first place (verified directly —
`ffmpeg -decoders | grep hevc` returns nothing, and attempting to decode an
HEVC file fails with "no decoder found for: hevc"). ProRes decode works
fine on the system build; only HEVC sources are affected.

Rather than modify the shared system `ffmpeg`, this app vendors its own
static build (BtbN's GPL Linux build, which includes full HEVC decode) at
`vendor/ffmpeg/`, installed via `scripts/install_ffmpeg.sh` and referenced
via `[Transcode] FFMPEG_BIN` / `FFPROBE_BIN` in `config.ini`. This keeps
the system `ffmpeg` untouched for other apps while giving this app's
transcode pipeline full format coverage.

## Open questions / follow-ups (not blocking v1)

- If archive usage grows, may eventually want a proper multi-user login
  — out of scope for now (single shared login was the explicit choice).
- Hardware-accelerated transcoding (VAAPI/NVENC) isn't configured;
  `-hwaccel auto` will use it opportunistically if available on this
  machine, otherwise falls back to software encoding.
