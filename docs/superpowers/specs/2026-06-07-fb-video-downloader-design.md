# Facebook Private-Group Video Downloader — Design Spec

**Date:** 2026-06-07
**Status:** Draft for review
**Author:** John Park (with Claude)

---

## 1. Goal

Let me save videos posted in a **private Facebook daycare group** (videos of my child) to my
own storage with the least possible effort, and have them land in **Google Photos** as well.

**Success looks like:** I see a video in the Facebook app on my Android phone, tap **Share →
"Save to NAS"**, and a minute later the video is (a) saved as a file on my Synology NAS and (b)
uploaded to my Google Photos — with no further action from me. When something needs my attention
(a re-login), my phone gets a notification telling me exactly what to do.

### Non-goals (YAGNI — explicitly out of scope)
- **No auto-monitoring/scraping** of the group. I trigger each video manually. (Continuous
  scraping is far more fragile and higher-risk.)
- **No bulk/whole-group download.** One video at a time.
- **No multi-user support.** Single user (me).
- **No web dashboard beyond the minimum** needed to upload cookies and see recent results.
- **No warm-session auto-cookie browser** in the first build (documented as a future upgrade in §11).
- **No Immich / Synology Photos** layer (decision: Google Photos is the destination).

---

## 2. Honest constraints (read this first)

These are structural realities confirmed by research. No design eliminates them; this design
*manages* them.

1. **Terms of Service.** Using yt-dlp + my login cookies to fetch group video is against
   Facebook's ToS. It is **not illegal** (it's a contract issue, not a law); the only realistic
   consequence is Facebook could restrict the account used. For personal, low-volume archival of
   my own child's videos from a group I'm a member of, the practical risk is low. **Accepted.**
2. **Periodic manual re-login is unavoidable.** Facebook rotates/expires session cookies and can
   throw a security "checkpoint" at any time. With the manual-refresh approach, expect to re-export
   cookies roughly **weekly** (sometimes longer). The system will *tell me* when this is needed so
   I never have to guess.
3. **The Facebook extractor breaks every few months.** yt-dlp's Facebook support depends on an
   actively-changing private API ("Tahoe"). The fix is keeping yt-dlp current and using browser
   **impersonation** (see §6). Occasional "update yt-dlp and wait for a community fix" downtime is
   expected.
4. **Per-video success is not 100%.** Canonical `/videos/<id>` and `/groups/<g>/posts/<id>/` URLs
   work best; Reels, `/share/v/`, `/share/r/`, and `fb.watch` wrapper links fail more often — and
   Facebook's share sheet frequently emits exactly those wrapper forms. The service resolves
   wrappers where it can, but some videos may not download.

### The one favorable factor
My **NAS runs at home, on my home internet connection** — the same residential IP my normal
Facebook browsing comes from. Replaying cookies from a *datacenter/foreign* IP is the #1 trigger
for invalidation; running from my home IP avoids that, which is what makes the simple
manual-cookie approach viable here.

---

## 3. The non-negotiable technical recipe (grounded in research)

Downloading private-group video **requires all three** of these together. Any one missing → it
fails (usually with a cryptic `Cannot parse data` error):

1. **Valid cookies** from an account that is a member of the group (`--cookies cookies.txt`, in
   Netscape format).
2. **Browser impersonation** (`--impersonate chrome`) — defeats Facebook's TLS/HTTP fingerprint
   gating. This is the single most-confirmed fix in the active yt-dlp bug thread (#15161).
3. **curl_cffi installed** — required for impersonation to do anything. Install yt-dlp as
   `yt-dlp[default,curl-cffi]`; verify with `yt-dlp --list-impersonate-targets` (must not show
   "(unavailable)").

Canonical working command:
```
yt-dlp --cookies /config/cookies.txt --impersonate chrome \
       --merge-output-format mp4 \
       -o '%(title)s [%(id)s].%(ext)s' --paths /downloads \
       "<facebook-video-url>"
```

---

## 4. Architecture (one container)

```
  [Facebook app on Android phone]
        │  tap Share → "Save to NAS"  (HTTP Shortcuts app: POST {url} + Bearer token)
        ▼
  [Tailscale]   end-to-end encrypted · works on wifi AND cellular · ZERO open ports on the NAS
        ▼
  ┌──────────────────────────────────────────────────────────────┐
  │  fbdl  — single Docker container on Synology                  │
  │                                                              │
  │  FastAPI service (uvicorn)                                    │
  │   POST /download   ── auth → normalize URL → enqueue          │
  │   GET  /           ── tiny status page + cookie upload form   │
  │   POST /cookies    ── upload a fresh cookies.txt              │
  │   GET  /health                                               │
  │                                                              │
  │  worker (background task, one at a time):                     │
  │   yt-dlp[curl-cffi] --impersonate chrome  ──►  /downloads     │
  │   classify result (ok / stale-cookies / extractor-broke /    │
  │                    video-unsupported / other)                │
  │   on success → rclone copy → Google Photos                   │
  │   on stale/fail → curl → ntfy push to phone                  │
  │                                                              │
  │  bundled: yt-dlp + curl_cffi, rclone, ffmpeg                  │
  └───────────────┬───────────────────────────┬──────────────────┘
                  │ writes file                │ reads file, uploads
                  ▼                            ▼
        /downloads  (NAS shared folder    rclone → Google Photos
         = my personal-cloud copy)         (append-only, durable token)
```

**Why one container:** rclone is a single static binary and the notifier is one `curl` call —
neither warrants its own service. Co-locating yt-dlp, rclone, and ffmpeg in one image means **one
thing to deploy, update, and reason about.**

---

## 5. Components (each unit: what it does / interface / dependencies)

### 5.1 Phone trigger — HTTP Shortcuts app
- **What:** A share-sheet shortcut (app: `ch.rmy.android.http_shortcuts`, free/FOSS). Registers
  directly in Facebook's Android share sheet. On Android 11+ it can appear as a Direct-Share tile
  that fires in ~1 tap.
- **Interface:** `POST {DOWNLOAD_URL}` with header `Authorization: Bearer <token>` and a body
  carrying the shared text/URL (configured via a "Share..." global variable).
- **Depends on:** Tailscale connectivity to the NAS; the service's bearer token.

### 5.2 Transport — Tailscale
- **What:** WireGuard-based mesh VPN. NAS and phone join the same tailnet. The phone reaches the
  service at `http://<nas-magicdns>:<port>` identically on home wifi and cellular.
- **Why:** Opens **no firewall ports** on the NAS (no port-forward = no public attack surface);
  same behavior on/off home network.
- **Depends on:** Tailscale package on Synology + Tailscale app on phone, same account.

### 5.3 Downloader service (`fbdl`) — custom, Python/FastAPI
- **What:** Receives the trigger, runs the download/upload/notify pipeline.
- **Endpoints:**
  - `POST /download` → validate bearer token, extract+normalize the URL from the shared text,
    enqueue a job, return **202 Accepted** immediately (so the phone shortcut gets a fast OK even
    on cellular). Result is reported later via ntfy.
  - `POST /cookies` → accept an uploaded `cookies.txt`, validate it's Netscape format, write it
    atomically to `COOKIES_PATH`. (This is the "painless refresh" path.)
  - `GET /` → minimal status page: last N jobs (ok/failed + reason), a cookies-upload form, and a
    "cookies last refreshed / last validated" indicator.
  - `GET /health` → liveness.
- **Worker:** processes one job at a time (single-user, no concurrency needed). Steps: probe →
  download → classify → upload → notify.
- **Depends on:** cookies.txt, yt-dlp+curl_cffi, ffmpeg, rclone+token, ntfy topic.

### 5.4 URL normalization
- **What:** The shared text from Facebook may be a bare URL, a `fb.watch`/`/share/v/`/`/share/r/`
  wrapper, or URL + title text. Extract the first URL; attempt to resolve redirect/wrapper links
  to a canonical `/videos/<id>` or `/groups/<g>/posts/<id>/` form (follow redirects using the
  impersonated client) before handing to yt-dlp.
- **Interface:** `normalize(shared_text: str) -> str | None`. Pure-ish, unit-testable.

### 5.5 Result classifier (highest-value testable unit)
- **What:** Maps a yt-dlp failure to a category by matching the error string (the failure reason
  is encoded in the string, not the exit code):
  - contains `Use --cookies` / `log in to continue` / `login_form` → **STALE_COOKIES**
  - contains `Cannot parse data` → **EXTRACTOR_BROKEN** (do NOT rotate cookies; update yt-dlp)
  - contains `is not available` / `registered users`, or wrapped `UnsupportedError` →
    **VIDEO_UNSUPPORTED** (skip this one video)
  - else → **OTHER**
- **Interface:** `classify(error_text: str, exc) -> Category`. Pure function → TDD target.

### 5.6 Cookie management (painless manual)
- **What:** `cookies.txt` lives on the `/config` volume. The service uses it for every download.
- **Stale detection:** a cheap metadata-only probe (`yt-dlp -J --cookies ... --impersonate chrome
  <canary-url>`) run **from the NAS** (same egress IP as real downloads), either on a schedule or
  lazily before a download. If it classifies as STALE_COOKIES → ntfy "Facebook login expired —
  upload fresh cookies."
- **Refresh:** I export `cookies.txt` from my home browser using the **"Get cookies.txt LOCALLY"**
  (Chrome) or **"cookies.txt"** (Firefox) extension, then upload it via the status page (`POST
  /cookies`). ~30 seconds.
- **Validation on upload:** after writing, run the probe; show pass/fail on the page so I know it
  worked before I walk away.

### 5.7 Storage — NAS folder
- **What:** Finished files land in `/downloads` (bind-mounted to a NAS shared folder). **This is
  the local/personal-cloud copy** — visible in File Station, backed up by my normal NAS routines.
- **Naming:** `%(title)s [%(id)s].%(ext)s`, container `mp4` (merged via ffmpeg).

### 5.8 Upload — rclone → Google Photos
- **What:** On successful download, `rclone copy <file> gphotos:album/<GPHOTOS_ALBUM>` using a
  durable token.
- **Durable auth (the key trick):** the "token expires every 7 days" problem is caused by leaving
  the Google OAuth app in **"Testing"** status. Setting it to **"In production"** (no Google
  verification required for my own account) gives a long-lived token. Requires rclone **≥ v1.74**
  and the `photoslibrary.appendonly` scope with a **self-created** OAuth client. Full steps in §9.
- **Accepted upload caveats:** counts toward Google storage at original quality; rclone can only
  add to albums it created; the backend is effectively append-only (it can't read my existing
  library); items appear in the timeline dated by capture time.

### 5.9 Notification — ntfy
- **What:** One `curl` POST to a public `ntfy.sh` topic with an unguessable name; the ntfy Android
  app subscribes to it. Used for: stale cookies (high priority), checkpoint/account lock
  (high priority), hard download failures, and optionally success confirmations.
- **Why:** zero SDK, free, best battery profile, one HTTP call. (Self-hosted ntfy on the NAS is a
  future privacy option; not needed now since alert text is non-sensitive.)

---

## 6. Data flow (happy path)

1. Phone: Share → "Save to NAS" → `POST /download {url}` + Bearer token (over Tailscale).
2. Service: validate token → `normalize()` the URL → enqueue → **202 Accepted** to phone.
3. Worker: `yt-dlp --cookies --impersonate chrome ... <url>` → file written to `/downloads`.
4. Worker: `rclone copy` the file to `gphotos:album/<album>`.
5. Worker: ntfy "✅ Saved: <title>" (optional success ping).

### Failure paths
- **STALE_COOKIES** → ntfy "🔑 Facebook login expired — open <nas-url> and upload fresh cookies."
- **EXTRACTOR_BROKEN** → ntfy "⚠️ Facebook download broke (extractor). Try updating yt-dlp." (Do
  not prompt for cookies.)
- **VIDEO_UNSUPPORTED** → ntfy "🚫 That video couldn't be downloaded (unsupported format/link).
  Try the 'Copy link' from the video itself rather than a share wrapper."
- **Upload fails but download succeeded** → file is safe on the NAS; ntfy "Saved to NAS but Google
  Photos upload failed: <reason>." (Retry on next run or manually.)

---

## 7. Security

- **Reachable only over Tailscale** — no public exposure, no port-forwarding (Synology DSM has had
  serious internet-facing RCEs; we avoid that class entirely).
- **Bearer token** (`AUTH_TOKEN`, 32 random bytes) required on `/download` and `/cookies` as
  defense-in-depth so a stray tailnet device can't trigger or tamper. Treat like a password.
- **Sensitive files** (`cookies.txt`, `rclone.conf` with the token) live on `/config` with tight
  permissions; `cookies.txt` holds my live Facebook session, `rclone.conf` holds my Google token.
- **Atomic cookie writes** (temp file in same dir → `os.replace`) so the worker never reads a
  half-written cookies file.

---

## 8. Configuration

### Environment variables
| Var | Purpose | Example |
|---|---|---|
| `AUTH_TOKEN` | Bearer token for `/download` and `/cookies` | `(32-byte random)` |
| `NTFY_URL` | ntfy endpoint incl. topic | `https://ntfy.sh/fbdl-7f3a9c2e` |
| `GPHOTOS_ALBUM` | rclone Google Photos album name | `Daycare Videos` |
| `IMPERSONATE_TARGET` | yt-dlp impersonate target | `chrome` |
| `CANARY_URL` | a known login-required FB video URL for the staleness probe | `(user-provided)` |
| `NOTIFY_ON_SUCCESS` | send a ping on success too | `true` |
| `PORT` | service port | `8080` |

### Volumes (bind mounts to NAS shared folders, all on one filesystem)
| Container path | NAS path (example) | Mode | Holds |
|---|---|---|---|
| `/config` | `/volume1/docker/fbdl/config` | rw | `cookies.txt`, `rclone.conf` |
| `/downloads` | `/volume1/docker/fbdl/downloads` | rw | finished videos (personal-cloud copy) |

---

## 9. One-time setup (will become the runbook in the plan)

1. **Tailscale:** install the Tailscale package from Synology Package Center, sign in; install the
   Tailscale app on the phone with the **same** account. Note the NAS's MagicDNS name.
2. **Google Photos / rclone durable token (on a desktop with a browser):**
   1. Google Cloud Console → new project → **enable "Photos Library API"**.
   2. OAuth consent screen → User type **External**; add app name + my email.
   3. Scopes → add `photoslibrary.appendonly`, `photoslibrary.readonly.appcreateddata`,
      `photoslibrary.edit.appcreateddata`.
   4. **Click "PUBLISH APP" → status "In production"** (this defeats the 7-day token expiry; no
      verification needed for my own account).
   5. Create OAuth client credentials, application type **Desktop app**; copy client_id/secret.
   6. `rclone config` (rclone ≥ v1.74) → new remote type `google photos` → paste client_id/secret
      → read-only = **No** → browser auth → "Advanced → Go to (unsafe) → Allow".
   7. Copy the resulting `[gphotos]` block (with `token = {...}`) into the container's
      `/config/rclone.conf`.
   8. **Durability hygiene:** the normal upload activity keeps the token alive; after any rclone
      major upgrade or an auth error, run `rclone config reconnect gphotos:`.
3. **ntfy:** install the ntfy Android app; subscribe to an unguessable topic name (e.g.
   `fbdl-7f3a9c2e`); set `NTFY_URL` to match.
4. **Build & run the container** via Container Manager → Project (docker-compose), with the env
   and volumes above.
5. **HTTP Shortcuts (phone):** create a global "Share..." variable; create a POST shortcut to the
   `/download` URL with `Authorization: Bearer <token>` and the shared text as the body; enable
   the Direct-Share tile.
6. **First Facebook login (cookies):** on my home browser, log into Facebook (the daycare-group
   account), export `cookies.txt` with the cookie extension, and upload it at `http://<nas>:<port>/`.

---

## 10. Testing approach

- **TDD the pure units first:** `classify()` (error string → category — the highest-value unit),
  `normalize()` (shared text → canonical URL), and the **Netscape cookies.txt validator**
  (header line present, exactly 7 tab-separated fields, `#HttpOnly_` prefix handling).
- **Integration (manual, documented):** a real download of a known group video end-to-end →
  file on NAS → appears in Google Photos; a deliberately-stale cookies file → ntfy fires the
  right message; a Reel/share-wrapper URL → graceful VIDEO_UNSUPPORTED notification.
- **Probe canary:** confirm the staleness probe distinguishes expired cookies from extractor
  breakage using captured real error strings as fixtures.

---

## 11. Future upgrade (documented, NOT built now)

**Warm-session auto-cookie keeper.** If weekly cookie refresh becomes annoying, add a second
container running a headless-but-VNC-accessible Chromium via Playwright `launchPersistentContext`
that stays logged into Facebook, is kept "warm" by a light daily visit, and auto-harvests fresh
cookies (serialized to Netscape format, written atomically) into `/config/cookies.txt`. This
stretches manual re-login from ~weekly to ~monthly/quarterly, at the cost of an always-on browser
with its own failure modes. The current design's `cookies.txt`-on-a-volume interface is
deliberately the same, so this upgrade is drop-in: the keeper simply becomes the writer of the
file the downloader already reads. (Full technical notes captured in research; see git history.)

---

## 12. Risks & ongoing maintenance (ranked)

1. **Periodic FB re-login** (~weekly with manual cookies). Managed via stale-detection + ntfy so
   I act only when needed. A `/checkpoint/` account lock needs me + my registered device — the
   system alerts, it does not loop.
2. **ToS/account risk.** Low for personal low-volume use; accepted.
3. **Extractor breakage** (`Cannot parse data`) recurs; mitigation is keeping yt-dlp + curl_cffi
   current and impersonation on. Occasional firefighting.
4. **Per-video non-determinism** (Reels/share-wrappers fail more). Set expectations; prefer the
   video's own "Copy link" over a raw share.
5. **Google Photos policy drift** + dynamic home WAN IP changes (an ISP lease change can force an
   early re-auth). Best-effort; monitored via the same notification path.

---

## 13. Open assumptions to confirm during review
- **Primary Facebook account** is used (a dedicated second account is impractical for a vetted
  daycare group and carries its own flagging risk). 
- **Public `ntfy.sh`** is acceptable for alerts (text is non-sensitive; topic name is unguessable).
- **rclone uploads into a single album** (`Daycare Videos`); fine that it can't write to a
  pre-existing manually-made album.
- Tech stack: **Python + FastAPI** for the service (small, async-friendly, easy to test).
