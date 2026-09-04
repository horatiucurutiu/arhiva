import os
import subprocess
import time

from transcode import TranscodeManager

# Use the vendored ffmpeg/ffprobe (same binaries the app itself is
# configured to use via config.ini's FFMPEG_BIN/FFPROBE_BIN), not whatever
# ffmpeg happens to be on the system PATH. The system ffmpeg on this host is
# a patent-restricted "free" build with no libx264 encoder; the vendored
# build does have libx264, matching what production actually runs.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FFMPEG = os.path.join(_REPO_ROOT, "vendor", "ffmpeg", "ffmpeg")
FFPROBE = os.path.join(_REPO_ROOT, "vendor", "ffmpeg", "ffprobe")


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
