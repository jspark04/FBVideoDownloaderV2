# FBVideoDownloaderV2

One small container that downloads a Facebook (private-group) video to a NAS folder
when you share its link from your phone. Reachable over Tailscale; confirms success/
failure back to whoever triggered it; pings you (ntfy) when the Facebook login needs
refreshing.

- Design: `docs/superpowers/specs/2026-06-07-fb-video-downloader-design.md`
- Plan: `docs/superpowers/plans/2026-06-07-fb-video-downloader.md`

## Run
1. `cp .env.example .env` and fill in `AUTH_TOKEN`, `NTFY_URL`, `CANARY_URL`.
2. Put a Facebook `cookies.txt` at `./config/cookies.txt` (or upload via the status page).
3. `docker compose up -d --build` (or import as a Synology Container Manager project).
4. Open `http://<nas>:8080/` for status + cookie refresh.

## Update yt-dlp (when Facebook breaks extraction)
`docker compose build --no-cache && docker compose up -d`

## Test
`pip install -r requirements-dev.txt && pytest -q`
