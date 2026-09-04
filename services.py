import os
import configparser
import logging
import warnings
from typing import List, Dict
from concurrent.futures import ThreadPoolExecutor
from flask import render_template, send_file, abort, jsonify, request
import urllib.parse
from utils import (
    get_ip_addresses,
    get_thumbnail_path,
    extract_subtitles,
    generate_thumbnail,
    generate_image_thumbnail,
    safe_join,
)


class VideoServer:
    def __init__(self, app, cache, config_path: str = "config.ini"):
        self.app = app
        self.cache = cache
        self.config = self._load_config(config_path)
        self._configure_logging()
        self._configure_routes()
        self._ensure_thumbnail_dir()
        self.executor = ThreadPoolExecutor(max_workers=os.cpu_count() or 4)

    def _load_config(self, config_path: str) -> configparser.ConfigParser:
        config = configparser.ConfigParser()
        if not config.read(config_path):
            logging.error(f"Failed to read configuration file: {config_path}")
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
        return config

    def _configure_logging(self):
        logging.getLogger("werkzeug").setLevel(
            logging.CRITICAL
        )  # Suppress Werkzeug logs
        warnings.filterwarnings("ignore", category=UserWarning, module="flask_caching")

    def _configure_routes(self):
        self.app.add_url_rule("/", "index", self.index)
        self.app.add_url_rule("/api/structure", "api_structure", self.api_structure)
        self.app.add_url_rule("/play/<path:filename>", "play_video", self.play_video)
        self.app.add_url_rule("/video/<path:filename>", "serve_file", self.serve_file)
        self.app.add_url_rule(
            "/thumbnail/<path:filename>", "serve_thumbnail", self.serve_thumbnail
        )
        self.app.add_url_rule(
            "/api/related-videos", "api_related_videos", self.api_related_videos
        )
        self.app.add_url_rule(
            "/subtitle-cache/<path:filename>", "serve_cached_subtitle", self.serve_cached_subtitle
        )

    def _ensure_thumbnail_dir(self):
        try:
            os.makedirs(self.thumbnail_dir, exist_ok=True)
        except OSError as e:
            logging.error(f"Failed to create thumbnail directory: {e}")
            raise

    @property
    def video_dir(self) -> str:
        return self.config.get("Paths", "VIDEO_DIR")

    @property
    def thumbnail_dir(self) -> str:
        return self.config.get(
            "Paths",
            "THUMBNAIL_DIR",
            fallback=os.path.join(self.video_dir, ".thumbnails"),
        )

    @property
    def subtitle_cache_dir(self) -> str:
        return os.path.join(
            self.config.get("Transcode", "CACHE_DIR", fallback=self.thumbnail_dir), "subtitles"
        )

    @property
    def subtitle_extensions(self) -> List[str]:
        return self.config.get("Subtitles", "EXTENSIONS").split(",")

    @property
    def show_hidden(self) -> bool:
        return self.config.getboolean("Display", "SHOW_HIDDEN")

    @property
    def image_extensions(self):
        # Guard against endswith(("",)) matching every filename when the config
        # key is absent/empty — an empty tuple correctly matches nothing instead.
        raw = self.config.get("Images", "EXTENSIONS", fallback="")
        return tuple(ext.strip() for ext in raw.split(",") if ext.strip())

    @property
    def exclude_dir_names(self):
        raw = self.config.get("Display", "EXCLUDE_DIRS", fallback="")
        return {name.strip().lower() for name in raw.split(",") if name.strip()}

    def run(self):
        host = self.config.get("Server", "HOST")
        port = self.config.getint("Server", "PORT")

        logging.info(f"Starting server on port {port}")
        ip_addresses = get_ip_addresses()
        for ip in ip_addresses:
            logging.info(f"Running on http://{ip}:{port}")

        self.app.run(host=host, port=port, debug=False, use_reloader=False)

    def get_directory_structure(self, path: str) -> List[Dict[str, str]]:
        @self.cache.memoize(300)
        def _get_directory_structure(path: str) -> List[Dict[str, str]]:
            structure = []
            video_extensions = tuple(self.config.get("Videos", "EXTENSIONS").split(","))
            image_extensions = self.image_extensions
            exclude_names = self.exclude_dir_names
            walked = []
            try:
                # Single top-down pass: this is the only place directories can be
                # pruned from actual disk traversal (mutating `dirs` in-place only
                # affects walking under topdown=True) — used both for hidden dirs
                # and for EXCLUDE_DIRS, so an excluded subtree (e.g. an old PC's
                # full disk backup mixed into a footage archive) costs zero I/O
                # instead of being walked and then discarded.
                for root, dirs, files in os.walk(path, followlinks=True):
                    if not self.show_hidden:
                        dirs[:] = [d for d in dirs if not d.startswith(".")]
                        files = [f for f in files if not f.startswith(".")]
                    dirs[:] = [d for d in dirs if d.lower() not in exclude_names]
                    walked.append((root, list(dirs), files))

                # Second pass over the already-walked (in-memory) results, in
                # reverse: reversing a top-down (pre-order) traversal list yields
                # every directory after all of its descendants, so "does this
                # subtree contain anything" becomes an O(1) lookup against
                # already-decided immediate children — no repeat disk I/O, unlike
                # the original code's per-directory nested os.walk (effectively
                # O(n^2) and unusable on a real tens-of-thousands-of-files archive).
                dirs_with_content = set()
                for root, dirs, files in reversed(walked):
                    has_video_file = any(f.lower().endswith(video_extensions) for f in files)
                    has_image_file = any(f.lower().endswith(image_extensions) for f in files)
                    has_content_subdir = any(
                        os.path.join(root, d) in dirs_with_content for d in dirs
                    )
                    contains_content = has_video_file or has_image_file or has_content_subdir

                    if contains_content:
                        dirs_with_content.add(root)

                    rel_path = os.path.relpath(root, self.video_dir)

                    if rel_path != "." and (
                        self.show_hidden or not os.path.basename(root).startswith(".")
                    ):
                        if contains_content:
                            structure.append(
                                {
                                    "type": "folder",
                                    "name": os.path.basename(root),
                                    "path": rel_path,
                                }
                            )

                    for file in files:
                        file_rel_path = os.path.join(rel_path, file)
                        if file.lower().endswith(video_extensions):
                            structure.append(
                                {
                                    "type": "file",
                                    "name": file,
                                    "path": file_rel_path,
                                    "thumbnail": get_thumbnail_path(
                                        file_rel_path, self.thumbnail_dir
                                    ),
                                }
                            )
                        elif file.lower().endswith(image_extensions):
                            structure.append(
                                {
                                    "type": "image",
                                    "name": file,
                                    "path": file_rel_path,
                                    "thumbnail": get_thumbnail_path(
                                        file_rel_path, self.thumbnail_dir
                                    ),
                                }
                            )
            except Exception as e:
                logging.error(f"Error generating directory structure: {e}")
            return structure

        return _get_directory_structure(path)

    def index(self):
        return render_template("index.html")

    def api_structure(self):
        return jsonify(self.get_directory_structure(self.video_dir))

    def play_video(self, filename):
        try:
            full_path = safe_join(self.video_dir, filename)
        except ValueError:
            abort(404)
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

    def serve_file(self, filename):
        try:
            full_path = safe_join(self.video_dir, filename)
        except ValueError:
            abort(404)
        try:
            return send_file(full_path)
        except FileNotFoundError:
            abort(404)

    def serve_cached_subtitle(self, filename):
        try:
            full_path = safe_join(self.subtitle_cache_dir, filename)
        except ValueError:
            abort(404)
        if os.path.isfile(full_path):
            return send_file(full_path)
        abort(404)

    def serve_thumbnail(self, filename):
        try:
            full_path = safe_join(self.video_dir, urllib.parse.unquote_plus(filename))
        except ValueError:
            abort(404)
        thumbnail_path = get_thumbnail_path(full_path, self.thumbnail_dir)
        if full_path.lower().endswith(self.image_extensions):
            thumbnail_path = self.executor.submit(
                generate_image_thumbnail, full_path, thumbnail_path
            ).result()
        else:
            thumbnail_path = self.executor.submit(
                generate_thumbnail,
                full_path,
                thumbnail_path,
                self.config.get("Transcode", "FFMPEG_BIN", fallback="ffmpeg"),
            ).result()
        if thumbnail_path:
            return send_file(thumbnail_path)
        else:
            abort(404)

    def api_related_videos(self):
        folder = urllib.parse.unquote(request.args.get("folder", ""))
        if folder.startswith(self.config.get("Server", "BASE_URL")):
            folder = folder[len(self.config.get("Server", "BASE_URL")) :]
        try:
            folder_path = safe_join(self.video_dir, folder)
        except ValueError:
            abort(404)
        related_videos = []

        if os.path.isdir(folder_path):
            for file in os.listdir(folder_path):
                if self.show_hidden or not file.startswith("."):
                    if file.lower().endswith(
                        tuple(self.config.get("Videos", "EXTENSIONS").split(","))
                    ):
                        video_path = os.path.join(folder, file)
                        related_videos.append(
                            {
                                "name": file,
                                "path": video_path,
                                "thumbnail": get_thumbnail_path(
                                    video_path, self.thumbnail_dir
                                ),
                            }
                        )
        return jsonify(related_videos)
