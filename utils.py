import os
import socket
import hashlib
import subprocess
from typing import List, Optional
import logging

# Suppress logging in utils.py
logging.basicConfig(level=logging.CRITICAL)


def safe_join(base_dir: str, *paths: str) -> str:
    """Join paths under base_dir, raising ValueError if the result would escape base_dir.

    Uses lexical normalization (abspath/normpath), not realpath — this deliberately
    does NOT resolve symlinks, so a legitimate symlink placed inside base_dir (e.g.
    roots/norman-manea -> /mnt/norman-manea, the multi-root browsing mechanism) is
    followed transparently by the OS at actual file-access time, while a malicious
    "../" or an absolute-path injection in the URL-supplied component is still
    rejected lexically before any resolution happens.

    Keeping the path lexical also keeps os.path.relpath(target, base_dir) meaningful
    for callers such as extract_subtitles, which derive a cache-relative path from
    the result; a realpath'd target under a symlinked root would sit outside
    base_dir and yield a "../"-laden relpath that escapes the cache directory.
    """
    base_dir = os.path.abspath(base_dir)
    target = os.path.abspath(os.path.join(base_dir, *paths))
    if target != base_dir and not target.startswith(base_dir + os.sep):
        raise ValueError(f"Path escapes base directory: {paths!r}")
    return target


def get_ip_addresses() -> List[str]:
    try:
        hostname = socket.gethostname()
        return socket.gethostbyname_ex(hostname)[2]
    except Exception as e:
        logging.error(f"Error getting IP addresses: {e}")
        return []


def directory_contains_supported_files(
    path: str, extensions: List[str], show_hidden: bool
) -> bool:
    try:
        for root, dirs, files in os.walk(path, followlinks=True):
            if not show_hidden:
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                files = [f for f in files if not f.startswith(".")]
            if any(f.lower().endswith(tuple(extensions)) for f in files):
                return True
        return False
    except Exception as e:
        logging.error(f"Error checking directory for supported files: {e}")
        return False


def get_thumbnail_path(video_path: str, thumbnail_dir: str) -> str:
    try:
        unique_id = hashlib.md5(video_path.encode("utf-8")).hexdigest()
        thumbnail_filename = (
            f"{os.path.splitext(os.path.basename(video_path))[0]}_{unique_id}.jpg"
        )
        return os.path.join(thumbnail_dir, thumbnail_filename)
    except Exception as e:
        logging.error(f"Error generating thumbnail path: {e}")
        return ""


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
                timeout=300,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logging.error(f"Error extracting subtitles: {e}")
            # A killed/failed ffmpeg can leave a truncated .vtt behind, which the
            # os.path.exists() check above would then treat as a valid cache hit.
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except OSError:
                    pass
            return None
    return output_path


def generate_image_thumbnail(image_path: str, thumbnail_path: str) -> Optional[str]:
    if not os.path.exists(thumbnail_path):
        try:
            from PIL import Image

            with Image.open(image_path) as img:
                img = img.convert("RGB")
                img.thumbnail((320, 320))
                img.save(thumbnail_path, "JPEG")
        except Exception as e:
            logging.error(f"Error generating image thumbnail: {e}")
            return None
    return thumbnail_path


def generate_thumbnail(
    video_path: str, thumbnail_path: str, ffmpeg_bin: str = "ffmpeg"
) -> Optional[str]:
    if not os.path.exists(thumbnail_path):
        try:
            subprocess.run(
                [
                    ffmpeg_bin,
                    "-hwaccel",
                    "auto",
                    "-i",
                    video_path,
                    "-ss",
                    "00:00:05",
                    "-vframes",
                    "1",
                    "-vf",
                    "scale=320:-1",
                    thumbnail_path,
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError as e:
            logging.error(f"Error generating thumbnail: {e}")
            return None
    return thumbnail_path
