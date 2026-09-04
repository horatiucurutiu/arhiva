import os

from flask import Response, abort, send_file
from zipstream import ZipStream

from utils import safe_join


def _downloadable_extensions(config):
    video_extensions = tuple(
        ext.strip()
        for ext in config.get("Videos", "EXTENSIONS", fallback="").split(",")
        if ext.strip()
    )
    image_extensions = tuple(
        ext.strip()
        for ext in config.get("Images", "EXTENSIONS", fallback="").split(",")
        if ext.strip()
    )
    return video_extensions + image_extensions


def _iter_downloadable_files(root_dir, extensions, show_hidden, exclude_names):
    for root, dirs, files in os.walk(root_dir, followlinks=True):
        if not show_hidden:
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            files = [f for f in files if not f.startswith(".")]
        dirs[:] = [d for d in dirs if d.lower() not in exclude_names]
        for file in files:
            if file.lower().endswith(extensions):
                full_file_path = os.path.join(root, file)
                arcname = os.path.relpath(full_file_path, root_dir)
                yield full_file_path, arcname


def init_downloads(app, config, video_dir):
    extensions = _downloadable_extensions(config)
    show_hidden = config.getboolean("Display", "SHOW_HIDDEN", fallback=False)
    exclude_names = {
        name.strip().lower()
        for name in config.get("Display", "EXCLUDE_DIRS", fallback="").split(",")
        if name.strip()
    }

    @app.route("/download/<path:filename>")
    def download_file(filename):
        try:
            full_path = safe_join(video_dir, filename)
        except ValueError:
            abort(404)
        if not os.path.isfile(full_path):
            abort(404)
        return send_file(full_path, as_attachment=True)

    @app.route("/download-folder/", defaults={"folder_path": ""})
    @app.route("/download-folder/<path:folder_path>")
    def download_folder(folder_path):
        try:
            full_path = safe_join(video_dir, folder_path)
        except ValueError:
            abort(404)
        if not os.path.isdir(full_path):
            abort(404)

        zip_name = os.path.basename(full_path.rstrip(os.sep)) if folder_path else "archive"

        # ZIP_STORED (no compression, the default): video/image files are
        # already compressed, so re-compressing would just burn CPU for no
        # size benefit. sized=True lets the browser show real download
        # progress (a cheap os.stat per file, not reading file contents) —
        # nothing is buffered in memory or written to a temp file on the
        # server; each file's bytes stream straight from disk to the response
        # as the client reads it.
        zs = ZipStream(sized=True)
        for file_path, arcname in _iter_downloadable_files(
            full_path, extensions, show_hidden, exclude_names
        ):
            zs.add_path(file_path, arcname=arcname, recurse=False)

        return Response(
            zs,
            mimetype="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{zip_name}.zip"',
                "Content-Length": str(len(zs)),
            },
        )
