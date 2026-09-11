import logging
import os

from flask import Flask
from flask_caching import Cache
from services import VideoServer

DEFAULT_CONFIG_PATH = os.environ.get("ARHIVA_CONFIG", "config.ini")


def create_app(config_path=DEFAULT_CONFIG_PATH):
    app = Flask(__name__)

    # Configure logging to suppress most logs
    logging.basicConfig(level=logging.CRITICAL)  # Only show critical logs

    # In-memory cache: safe because the deployment runs a single worker process
    # (see deploy/arhiva.service) — this is what actually makes
    # VideoServer.get_directory_structure's @cache.memoize(300) do anything.
    # ("NullCache" is a real Flask-Caching backend whose entire job is to never
    # cache — using it here silently disabled caching entirely.)
    cache = Cache(app, config={"CACHE_TYPE": "SimpleCache"})

    # Initialize VideoServer
    try:
        video_server = VideoServer(app, cache, config_path)
    except Exception as e:
        logging.error(f"Failed to initialize VideoServer: {e}")
        raise

    from auth import init_auth

    init_auth(app, video_server.config)

    from transcode import init_transcode

    init_transcode(app, video_server.config, video_server.video_dir)

    from downloads import init_downloads

    init_downloads(app, video_server.config, video_server.video_dir)

    return app, video_server


# Module-level WSGI entry point so a WSGI server can import it as `main:app`
# (see deploy/arhiva.service, which runs `gunicorn ... main:app` from
# WorkingDirectory=/home/numafilm/PROJECTS/arhiva, where config.ini lives).
#
# It is built only when the default config file actually exists in the working
# directory: the test suite imports `create_app` from this module and calls it
# with its own temporary config, and a source checkout has no config.ini
# (it is gitignored), so an unconditional call here would make every test error
# out at import time with FileNotFoundError.
app = None
video_server = None
if os.path.exists(DEFAULT_CONFIG_PATH):
    app, video_server = create_app(DEFAULT_CONFIG_PATH)


if __name__ == "__main__":
    try:
        if video_server is None:
            app, video_server = create_app(DEFAULT_CONFIG_PATH)
        video_server.run()
    except Exception as e:
        logging.error(f"Application failed to start: {e}")
