# Deploying Numa Film Archive

Run once, in order, on the server:

1. `cd ~/arhiva && python3.14 -m venv venv && source venv/bin/activate && pip install -r requirements.txt`
2. `./scripts/install_ffmpeg.sh`
3. `cp config.ini.example config.ini` and fill in `[Paths] VIDEO_DIR = /home/numafilm/arhiva/roots` (absolute path).
4. `ln -s /mnt/norman-manea ~/arhiva/roots/norman-manea` (repeat per mount you want browsable).
5. `./venv/bin/python scripts/set_password.py --username <your-username>` — sets the login password.
6. `mkdir -p ~/.config/systemd/user && cp deploy/arhiva.service ~/.config/systemd/user/`
7. `systemctl --user daemon-reload && systemctl --user enable --now arhiva.service`
8. `systemctl --user status arhiva.service` — confirm `active (running)`.
9. Add a DNS record for `arhiva.numafilm.ro` pointing at this server (same place the other `numafilm.ro` subdomains are managed).
10. `sudo cp deploy/arhiva.conf /etc/httpd/conf.d/arhiva.conf && sudo systemctl reload httpd`
11. `sudo certbot --apache -d arhiva.numafilm.ro` — obtains the cert and can auto-write the SSL vhost; if it doesn't match `deploy/arhiva-le-ssl.conf` exactly, replace the generated file's `ProxyPass`/`ProxyPassReverse` block with the one from `deploy/arhiva-le-ssl.conf`.
12. `sudo systemctl reload httpd`
13. Enable lingering so the user service survives reboots without a login session: `sudo loginctl enable-linger numafilm`

## Notes

- The service runs gunicorn with a single worker process on purpose
  (`-w 1 --threads 4`): the transcode queue, its job table and its ffmpeg
  worker thread live in process memory, so a second worker would duplicate
  transcodes and report inconsistent job status. Scale with threads, not
  workers. `--timeout 120` covers requests that shell out to ffmpeg/ffprobe.
- `main:app` is only defined when `config.ini` exists in the service's
  `WorkingDirectory`. If gunicorn fails with
  `AppImportError: Failed to find application object: 'app'`, the config file
  is missing or unreadable — re-check steps 3 and 5.
