"""Pre-transcode every non-browser-compatible video into the app's cache.

Uses the app's own cache key (TranscodeManager.cache_path) and its ffmpeg
encode arguments (but CPU decoding, see the ffmpeg call), so a finished file is
picked up by /video-proxy exactly as if a viewer had
requested it. Runs one file at a time, outside gunicorn, so the in-process
queue stays free for real viewers. Resumable: files already cached are skipped,
and files that failed are recorded and skipped unless --retry-failed.

Meant to run under low priority, e.g.:
    systemd-run --user --unit=arhiva-pretranscode --nice=19 \
        -p IOSchedulingClass=idle -p WorkingDirectory=$PWD \
        venv/bin/python scripts/pretranscode.py

Never evicts anything: it stops before the cache would exceed MAX_CACHE_GB,
because the app's eviction would then delete the oldest (pre-converted) files.
"""
import argparse
import configparser
import logging
import os
import subprocess
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from transcode import TranscodeManager, is_browser_compatible  # noqa: E402

GB = 1024**3


def load_config():
    config = configparser.ConfigParser()
    config.read(os.path.join(REPO_ROOT, "config.ini"))
    return config


def iter_videos(config):
    """Walk VIDEO_DIR the way the browser does: hidden and EXCLUDE_DIRS pruned."""
    video_dir = os.path.abspath(config.get("Paths", "VIDEO_DIR"))
    video_exts = tuple(config.get("Videos", "EXTENSIONS").split(","))
    show_hidden = config.getboolean("Display", "SHOW_HIDDEN", fallback=False)
    raw = config.get("Display", "EXCLUDE_DIRS", fallback="")
    exclude = {n.strip().lower() for n in raw.split(",") if n.strip()}
    for root, dirs, files in os.walk(video_dir, followlinks=True):
        if not show_hidden:
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            files = [f for f in files if not f.startswith(".")]
        dirs[:] = sorted(d for d in dirs if d.lower() not in exclude)
        for f in sorted(files):
            if f.lower().endswith(video_exts):
                yield os.path.join(root, f)


def cache_size(cache_dir):
    total = 0
    for name in os.listdir(cache_dir):
        full = os.path.join(cache_dir, name)
        if os.path.isfile(full):
            total += os.path.getsize(full)
    return total


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="only list what would be done")
    parser.add_argument("--limit", type=int, default=0, help="stop after N transcodes")
    parser.add_argument("--ext", default="", help="comma list, e.g. .vob,.mpg (default: all)")
    parser.add_argument("--path", action="append", default=[],
                        help="only these files (absolute, as under VIDEO_DIR); repeatable")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--ratio", type=float, default=0.6,
                        help="assumed output/source size ratio for the space guard")
    args = parser.parse_args()

    os.chdir(REPO_ROOT)  # FFMPEG_BIN/FFPROBE_BIN in config.ini are repo-relative
    config = load_config()
    ffmpeg_bin = config.get("Transcode", "FFMPEG_BIN", fallback="ffmpeg")
    ffprobe_bin = config.get("Transcode", "FFPROBE_BIN", fallback="ffprobe")
    cache_dir = os.path.join(config.get("Transcode", "CACHE_DIR"), "transcoded")
    max_bytes = int(config.getfloat("Transcode", "MAX_CACHE_GB", fallback=350) * GB)

    # Only cache_path() is needed; skip __init__ so no worker thread starts.
    manager = TranscodeManager.__new__(TranscodeManager)
    manager.cache_dir = cache_dir

    state_dir = os.path.join(config.get("Transcode", "CACHE_DIR"), "pretranscode")
    os.makedirs(state_dir, exist_ok=True)
    failed_file = os.path.join(state_dir, "failed.txt")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        handlers=[logging.FileHandler(os.path.join(state_dir, "pretranscode.log")),
                  logging.StreamHandler()],
        force=True,  # an imported module may already have configured logging
    )
    # A stopped/killed run leaves its in-progress output behind; the app's own
    # temp files end in ".mp4.tmp", so only ours are touched here.
    for name in os.listdir(cache_dir):
        if name.endswith(".pre.tmp"):
            os.remove(os.path.join(cache_dir, name))
            logging.info(f"removed abandoned {name}")
    failed = set()
    if os.path.exists(failed_file) and not args.retry_failed:
        with open(failed_file) as fh:
            failed = {line.rstrip("\n") for line in fh}
    only_exts = tuple(e.strip().lower() for e in args.ext.split(",") if e.strip())

    todo, cached, compatible, skipped_failed = [], 0, 0, 0
    for path in iter_videos(config):
        if only_exts and not path.lower().endswith(only_exts):
            continue
        if args.path and path not in args.path:
            continue
        if os.path.isfile(manager.cache_path(path)):
            cached += 1
        elif path in failed:
            skipped_failed += 1
        elif is_browser_compatible(ffprobe_bin, path):
            compatible += 1
        else:
            todo.append(path)
    todo_bytes = sum(os.path.getsize(p) for p in todo)
    logging.info(
        f"scan: {len(todo)} to transcode ({todo_bytes / GB:.1f} GB source), "
        f"{cached} already cached, {compatible} browser-compatible, "
        f"{skipped_failed} previously failed"
    )
    if args.dry_run:
        for p in todo:
            print(f"{os.path.getsize(p) / GB:7.2f} GB  {p}")
        return

    done = 0
    for path in todo:
        if args.limit and done >= args.limit:
            break
        dest = manager.cache_path(path)
        if os.path.isfile(dest):  # the app may have done it meanwhile
            continue
        src_size = os.path.getsize(path)
        if cache_size(cache_dir) + src_size * args.ratio > max_bytes:
            logging.info("STOP: next file would push the cache past MAX_CACHE_GB "
                         "(the app would then evict the oldest files)")
            break
        tmp_dest = dest + ".pre.tmp"
        started = time.monotonic()
        logging.info(f"start {src_size / GB:.2f} GB {path}")
        try:
            subprocess.run(
                [
                    # Unlike the app, no "-hwaccel auto": the GPU is shared with
                    # Ollama at its VRAM limit, and CPU decoding of these sources
                    # (MPEG-2/MJPEG) is cheap.
                    ffmpeg_bin, "-y", "-i", path,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-c:a", "aac", "-movflags", "+faststart", "-f", "mp4", tmp_dest,
                ],
                check=True, capture_output=True, timeout=4 * 3600,
            )
            os.replace(tmp_dest, dest)
        except Exception as e:
            err = getattr(e, "stderr", b"") or b""
            logging.error(f"FAILED {path}: {e} {err[-500:].decode(errors='replace')}")
            with open(failed_file, "a") as fh:
                fh.write(path + "\n")
            if os.path.exists(tmp_dest):
                os.remove(tmp_dest)
            continue
        done += 1
        secs = time.monotonic() - started
        out_size = os.path.getsize(dest)
        logging.info(
            f"done {done}/{len(todo)} in {secs:.0f}s, {out_size / GB:.2f} GB "
            f"(ratio {out_size / src_size:.2f}, {src_size / GB / (secs / 3600):.1f} GB/h)"
        )
    logging.info(f"finished: {done} transcoded this run")


if __name__ == "__main__":
    main()
