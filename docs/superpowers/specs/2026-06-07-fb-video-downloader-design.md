# Facebook Private-Group Video Downloader — Design Spec

**Date:** 2026-06-07
**Status:** Draft for review
**Author:** John Park (with Claude)

---

## 1. Goal

Save videos posted in a **private Facebook daycare group** (videos of my child) to my Synology
NAS with the least possible effort. I trigger each video from my Android phone; it lands as a file
in a folder on the NAS.

**Success looks like:** I see a video in the Facebook app, tap **Share → "Save to NAS"**, and about
a minute later the video is a file in my NAS folder. When something needs my attention (a re-login),
my phone tells me exactly what to do.

**Getting those files into Google Photos / other cloud is explicitly NOT this project's job** — I
handle that myself on the Synology (it watches the download folder and syncs onward). This keeps the
fragile part (Facebook extraction) cleanly separated from the stable part (file sync).

### Non-goals (YAGNI — explicitly out of scope)
- **No cloud upload** (Google Photos, etc.). The NAS folder is the deliverable; I sync it onward
  myself with Synology's own tools.
- **No auto-monitoring/scraping** of the group. I trigger each video manually.
- **No bulk/whole-group download.** One video at a time.
- **No multi-account system.** Single Facebook account; both our phones can trigger downloads.
- **No warm-session auto-cookie browser** in the first build (documented future upgrade in §11).

---

## 2. Honest constraints (read this first)

Structural realities confirmed by research. No design eliminates them; this design *manages* them.

1. **Terms of Service.** Using yt-dlp + my login cookies to fetch group video is against
   Facebook's ToS. It is **not illegal** (contract, not law); the only realistic consequence is
   Facebook could restrict the account used. For personal, low-volume archival of my own child's
   videos from a group I belong to, the practical risk is low. **Accepted.**
2. **Periodic manual re-login is unavoidable.** Facebook rotates/expires session cookies. Expect to
   re-export cookies roughly **weekly** (sometimes longer). The system tells me when, so I never
   guess.
3. **The Facebook extractor breaks every few months.** yt-dlp's Facebook support depends on an
   actively-changing private API. The fix is keeping yt-dlp current and using **impersonation**
   (§3). Occasional "update and wait for a community fix" downtime is expected.
4. **Per-video success is not 100%.** Canonical `/videos/<id>` and `/groups/<g>/posts/<id>/` URLs
   work best; Reels, `/share/v/`, `/share/r/`, and `fb.watch` wrapper links fail more often — and
   Facebook's share sheet often emits those wrapper forms. The service resolves wrappers where it
   can, but some videos may not download.

### The one favorable factor
My **NAS runs at home, on my home internet connection** — the same residential IP my normal
Facebook browsing comes from. Replaying cookies from a *datacenter/foreign* IP is the #1 invalidation
trigger; running from my home IP avoids that, which makes the simple manual-cookie approach viable.

---

## 3. The non-negotiable technical recipe (grounded in research)

Downloading private-group video **requires all three** together. Any one missing → it fails
(usually with a cryptic `Cannot parse data` error):

1. **Valid cookies** from an account that is a member of the group (`--cookies cookies.txt`,
   Netscape format).
2. **Browser impersonation** (`--impersonate chrome`) — defeats Facebook's TLS/HTTP fingerprint
   gating. The single most-confirmed fix in the active yt-dlp bug thread (#15161).
3. **curl_cffi installed** — required for impersonation to do anything. Install
   `yt-dlp[default,curl-cffi]`; verify with `yt-dlp --list-impersonate-targets`.

Canonical working command:
```
yt-dlp --cookies /config/cookies.txt --impersonate chrome \
       --merge-output-format mp4 \
       -o '%(title)s [%(id)s].%(ext)s' --paths /downloads \
       "<facebook-video-url>"
```
(`ffmpeg` is bundled because Facebook often serves separate video+audio streams that yt-dlp merges.)

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
  │                       → 202 + instant cookie status           │
  │   GET  /           ── tiny status page + cookie upload form   │
  │   POST /cookies    ── upload a fresh cookies.txt              │
  │   GET  /health                                               │
  │                                                              │
  │  worker (background, one at a time):                          │
  │   yt-dlp[curl-cffi] --impersonate chrome  ──►  /downloads     │
  │   classify result (ok / stale-cookies / extractor-broke /    │
  │                    video-unsupported / other)                │
  │   notify: instant via /download response + async via ntfy    │
  │                                                              │
  │  bundled: yt-dlp + curl_cffi, ffmpeg                          │
  └───────────────────────────────┬──────────────────────────────┘
                                   │ writes file
                                   ▼
                         /downloads  (NAS shared folder)
                                   │
                                   ▼
                  (my Synology syncs this folder onward — out of scope)
```

**Why one container:** the notifier is one `curl` call and there's no upload step anymore — so a
single small image (FastAPI + yt-dlp + curl_cffi + ffmpeg) is the whole system. One thing to
deploy, update, and reason about.

---

## 5. Components (each unit: what it does / interface / dependencies)

### 5.1 Phone trigger — HTTP Shortcuts app
- **What:** A share-sheet shortcut (app: `ch.rmy.android.http_shortcuts`, free/FOSS). Registers
  directly in Facebook's Android share sheet. On Android 11+ it can appear as a Direct-Share tile
  that fires in ~1 tap.
- **Interface:** `POST {DOWNLOAD_URL}` with header `Authorization: Bearer <token>` and a body
  carrying the shared text/URL (configured via a "Share..." global variable).
- **Instant feedback:** the shortcut reads the HTTP response and shows it as a toast/dialog, so
  tapping Share immediately surfaces `✅ Queued (login OK)` or `🔑 Login expired — re-upload
  cookies` right in the share sheet, before any download finishes.
- **Client setup is light (per phone, one-time):** install Tailscale + HTTP Shortcuts, sign into
  Tailscale, and **import one pre-built shortcut file** I provide (no manual configuration). After
  that, everyday use = Share → "Save to NAS" (two taps). **The wife's phone needs only these two
  apps** (no ntfy, no maintenance role).
- **Depends on:** Tailscale connectivity to the NAS; the service's bearer token.

### 5.2 Transport — Tailscale
- **What:** WireGuard-based mesh VPN. NAS and phones join the same tailnet. The phone reaches the
  service at `http://<nas-magicdns>:<port>` identically on home wifi and cellular.
- **Why:** opens **no firewall ports** on the NAS (no port-forward = no public attack surface).
- **Depends on:** Tailscale package on Synology + Tailscale app on phones, same account.

### 5.3 Downloader service (`fbdl`) — custom, Python/FastAPI
- **Endpoints:**
  - `POST /download` → validate bearer token, extract+normalize the URL from the shared text,
    enqueue a job, and return **202 Accepted immediately with the current cached cookie status**
    (so the phone shortcut shows instant `✅ Queued (login OK)` / `🔑 Login expired` even on
    cellular, without waiting for the download). The final result is reported later via ntfy. The
    cached `cookie_status` (last-known good/stale + timestamp) is maintained by the periodic probe
    and updated after each real download attempt.
  - `POST /cookies` → accept an uploaded `cookies.txt`, validate it's Netscape format, write it
    atomically to `COOKIES_PATH`, then run a validation probe and report pass/fail.
  - `GET /` → minimal status page: last N jobs (ok/failed + reason), the cookies-upload form, and a
    "cookies last refreshed / last validated" indicator.
  - `GET /health` → liveness.
- **Worker:** processes one job at a time (single-user). Steps: download → classify → notify.
- **Depends on:** cookies.txt, yt-dlp+curl_cffi, ffmpeg.

### 5.4 URL normalization
- **What:** The shared text may be a bare URL, a `fb.watch`/`/share/v/`/`/share/r/` wrapper, or
  URL + title text. Extract the first URL; attempt to resolve redirect/wrapper links to a canonical
  `/videos/<id>` or `/groups/<g>/posts/<id>/` form before handing to yt-dlp.
- **Interface:** `normalize(shared_text: str) -> str | None`. Unit-testable.

### 5.5 Result classifier (highest-value testable unit)
- **What:** Maps a yt-dlp failure to a category by matching the error string:
  - contains `Use --cookies` / `log in to continue` / `login_form` → **STALE_COOKIES**
  - contains `Cannot parse data` → **EXTRACTOR_BROKEN** (do NOT rotate cookies; update yt-dlp)
  - contains `is not available` / `registered users`, or wrapped `UnsupportedError` →
    **VIDEO_UNSUPPORTED** (skip this one video)
  - else → **OTHER**
- **Interface:** `classify(error_text: str, exc) -> Category`. Pure function → TDD target.

### 5.6 Cookie management (painless manual)
- **What:** `cookies.txt` lives on the `/config` volume; the service uses it for every download.
- **Stale detection:** a cheap metadata-only probe (`yt-dlp -J --cookies ... --impersonate chrome
  <canary-url>`) run **from the NAS**, on a schedule. If it classifies as STALE_COOKIES → ntfy
  "Facebook login expired — upload fresh cookies," and the cached status flips so the next share-
  sheet trigger shows `🔑` instantly.
- **Refresh:** I export `cookies.txt` from my home browser using the **"Get cookies.txt LOCALLY"**
  (Chrome) or **"cookies.txt"** (Firefox) extension, then upload it via the status page. ~30 sec.
- **Validation on upload:** after writing, run the probe; show pass/fail on the page.

### 5.7 Storage — NAS folder
- **What:** Finished files land in `/downloads` (bind-mounted to a NAS shared folder) — visible in
  File Station. **This folder is the project's deliverable**; my Synology handles syncing it onward.
- **Naming:** `%(title)s [%(id)s].%(ext)s`, container `mp4` (merged via ffmpeg).

### 5.8 Notification — two layers
1. **Instant, in the share sheet (no extra app):** the `/download` response lets the shortcut show
   `✅ Queued (login OK)` / `🔑 Login expired` the moment Share is tapped.
2. **Async push via ntfy:** for later outcomes — download finished, or a hard failure.

**About ntfy:** it is **push notifications, not SMS/texting** — no phone number, no carrier. The NAS
sends one `curl` POST to a public `ntfy.sh` topic with an unguessable name; the ntfy Android app
(subscribed to that topic) shows the notification. It is **free** for personal use, open-source,
zero SDK. Only **my** phone needs the ntfy app (it carries the maintenance alerts); my wife's does
not. (Self-hosting ntfy on the NAS is a future privacy option; not needed now.)

---

## 6. Data flow

**Happy path:**
1. Phone: Share → "Save to NAS" → `POST /download {url}` + Bearer token (over Tailscale).
2. Service: validate token → `normalize()` the URL → enqueue → **202 + cached cookie status**
   (shortcut shows `✅ Queued (login OK)` instantly).
3. Worker: `yt-dlp --cookies --impersonate chrome ... <url>` → file written to `/downloads`.
4. Worker: ntfy "✅ Saved: <title>" (success ping).
5. My Synology picks the file up from the folder and syncs it onward (outside this system).

**Failure paths:**
- **STALE_COOKIES** → ntfy "🔑 Facebook login expired — open <nas-url> and upload fresh cookies";
  cached status flips to stale.
- **EXTRACTOR_BROKEN** → ntfy "⚠️ Facebook download broke (extractor). Try updating yt-dlp." (Do
  not prompt for cookies.)
- **VIDEO_UNSUPPORTED** → ntfy "🚫 That video couldn't be downloaded. Try the video's own 'Copy
  link' instead of a share wrapper."

---

## 7. Security

- **Reachable only over Tailscale** — no public exposure, no port-forwarding.
- **Bearer token** (`AUTH_TOKEN`, 32 random bytes) required on `/download` and `/cookies`. Treat
  like a password.
- **`cookies.txt`** (my live Facebook session) lives on `/config` with tight permissions.
- **Atomic cookie writes** (temp file in same dir → `os.replace`) so the worker never reads a
  half-written cookies file.

---

## 8. Configuration

### Environment variables
| Var | Purpose | Example |
|---|---|---|
| `AUTH_TOKEN` | Bearer token for `/download` and `/cookies` | `(32-byte random)` |
| `NTFY_URL` | ntfy endpoint incl. topic | `https://ntfy.sh/fbdl-7f3a9c2e` |
| `IMPERSONATE_TARGET` | yt-dlp impersonate target | `chrome` |
| `CANARY_URL` | a known login-required FB video URL for the staleness probe | `(user-provided)` |
| `NOTIFY_ON_SUCCESS` | send a ping on success too | `true` |
| `PORT` | service port | `8080` |

### Volumes (bind mounts to NAS shared folders, on one filesystem)
| Container path | NAS path (example) | Mode | Holds |
|---|---|---|---|
| `/config` | `/volume1/docker/fbdl/config` | rw | `cookies.txt`, small app state |
| `/downloads` | `/volume1/docker/fbdl/downloads` | rw | finished videos (the deliverable) |

---

## 9. One-time setup (becomes the runbook in the plan)

1. **Tailscale:** install the Tailscale package from Synology Package Center, sign in; install the
   Tailscale app on each phone with the **same** account. Note the NAS's MagicDNS name.
2. **ntfy:** install the ntfy Android app (my phone); subscribe to an unguessable topic (e.g.
   `fbdl-7f3a9c2e`); set `NTFY_URL` to match.
3. **Build & run the container** via Container Manager → Project (docker-compose), with the env and
   volumes above.
4. **Phones (light, ~2 min each):** install **Tailscale** + **HTTP Shortcuts**, then **import the
   pre-built shortcut file** I provide (URL, bearer token, "Share..." body wiring, and response-
   display already set). Only my phone also installs ntfy.
5. **First Facebook login (cookies):** on my home browser, log into Facebook (the daycare-group
   account), export `cookies.txt` with the extension, and upload it at `http://<nas>:<port>/`.

---

## 10. Testing approach

- **TDD the pure units first:** `classify()` (error string → category — highest value),
  `normalize()` (shared text → canonical URL), and the **Netscape cookies.txt validator** (header
  line present, exactly 7 tab-separated fields, `#HttpOnly_` prefix handling).
- **Integration (manual, documented):** real download of a known group video → file on NAS; a
  deliberately-stale cookies file → ntfy fires the right message + status page shows stale; a
  Reel/share-wrapper URL → graceful VIDEO_UNSUPPORTED notification.
- **Probe canary:** confirm the staleness probe distinguishes expired cookies from extractor
  breakage using captured real error strings as fixtures.

---

## 11. Future upgrade (documented, NOT built now)

**Warm-session auto-cookie keeper.** If weekly cookie refresh becomes annoying, add a second
container running a headless-but-VNC-accessible Chromium via Playwright `launchPersistentContext`
that stays logged into Facebook, is kept "warm" by a light daily visit, and auto-harvests fresh
cookies (serialized to Netscape format, written atomically) into `/config/cookies.txt`. This
stretches manual re-login from ~weekly to ~monthly/quarterly, at the cost of an always-on browser
with its own failure modes. The current `cookies.txt`-on-a-volume interface is deliberately the
same, so this upgrade is drop-in: the keeper simply becomes the writer of the file the downloader
already reads.

---

## 12. Risks & ongoing maintenance (ranked)

1. **Periodic FB re-login** (~weekly with manual cookies). Managed via stale-detection + ntfy so I
   act only when needed. A `/checkpoint/` account lock needs me + my registered device — the system
   alerts, it does not loop.
2. **ToS/account risk.** Low for personal low-volume use; accepted.
3. **Extractor breakage** (`Cannot parse data`) recurs; mitigation is keeping yt-dlp + curl_cffi
   current and impersonation on. Occasional firefighting.
4. **Per-video non-determinism** (Reels/share-wrappers fail more). Prefer the video's own "Copy
   link" over a raw share.
5. **Dynamic home WAN IP changes** (an ISP lease change can force an early re-auth). Surfaced via
   the same notification path.

---

## 13. Open assumptions to confirm during review
- **Primary Facebook account** is used (a dedicated second account is impractical for a vetted
  daycare group and carries its own flagging risk).
- **Public `ntfy.sh`** for async alerts — confirmed (free push, not SMS; non-sensitive text;
  unguessable topic; only my phone subscribes). Plus instant in-share-sheet feedback every trigger.
- **Tech stack: Python + FastAPI** for the service (small, async-friendly, easy to test).
- **Onward cloud/Google Photos sync is handled by me on the Synology** — not part of this build.
