import os
import subprocess

from transcode import is_browser_compatible, probe_streams

# Use the vendored ffmpeg/ffprobe (the same binaries production points at via
# config.ini's FFMPEG_BIN/FFPROBE_BIN), resolved from this file's own location
# so the tests do not depend on the working directory or on the system PATH.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FFMPEG = os.path.join(_REPO_ROOT, "vendor", "ffmpeg", "ffmpeg")
FFPROBE = os.path.join(_REPO_ROOT, "vendor", "ffmpeg", "ffprobe")


def make_sample(tmp_path, filename, video_codec="libx264", audio_codec=None):
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
