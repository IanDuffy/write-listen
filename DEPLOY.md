# Deploying the tester demo

One DigitalOcean droplet runs everything: the web app, the conversations' connection offers and the
chapter builds. Caddy sits in front for HTTPS (browsers only allow the microphone over HTTPS). About an
hour the first time, most of it waiting for apt.

What runs there:

- **write-listen.service**: `server.py` on 127.0.0.1:8765, as user `writelisten`, from `/srv/write-listen`,
  restarted on failure. There is **no separate job runner**: each chapter build is a child process the app
  starts (at most `demo.max_builds` at once, the rest queued), and it carries on across a restart.
- **caddy**: HTTPS for the domain, certificates renewed by itself, a password on `/admin`.
- **write-listen-log.timer**: one app log a day, `/var/log/write-listen/app-YYYY-MM-DD.log`.
- Keys in `/etc/write-listen/env` (mode 600, owned by `writelisten`), never in the repository.
- Data in `/srv/write-listen/listeners/` (one folder per tester) and `shared/` (the tester links).

The live voice's audio goes from the tester's browser to OpenAI directly (WebRTC); only the connection
offer passes through the droplet, so there are no UDP ports or TURN server to set up. The browser never
gets an API key.

## Tonight, step by step

### 1. Push the branch

The droplet clones from GitHub. On the Mac: `git push origin feature/tester-demo`.

### 2. Create the droplet

DigitalOcean > Create > Droplets:

- **Image:** Ubuntu 24.04 (LTS) x64.
- **Size:** Basic, Regular, 4 GB / 2 vCPU.
- **Region:** the one nearest you and the testers.
- **Authentication:** SSH key: add your Mac's public key (`cat ~/.ssh/id_ed25519.pub`).
- **Backups:** turn on weekly backups here (or later: the droplet > Backups > Enable).
- **Hostname:** `write-listen`.

Note its IP address.

### 3. Point the domain at it

Wherever ianduffy.com's DNS lives, add an **A record**: name `listen`, value the droplet's IP, TTL 300.
Check from the Mac until it answers with the IP:

```bash
dig +short listen.ianduffy.com
```

Caddy can only get the certificate once this works.

### 4. Run setup

```bash
scp deploy/setup.sh root@<ip>:
ssh root@<ip>
bash setup.sh
```

It installs the packages (Python 3.12, ffmpeg, poppler, git, Caddy, uv), adds 2 GB of swap, creates the
`writelisten` user, and stops the first time to show a **deploy key**. On GitHub: the repository > Settings >
Deploy keys > Add deploy key, title `droplet`, read access only, paste the key. Then `bash setup.sh` again.

It clones to `/srv/write-listen`, installs the Python packages, and stops a second time after creating
`/etc/write-listen/env`. Fill it in:

```bash
nano /etc/write-listen/env
```

- `OPENAI_API_KEY`, and `WRITE_LISTEN_ANTHROPIC_API_KEY` (or `ANTHROPIC_API_KEY`).
- `ADMIN_USER` and `ADMIN_PASSWORD`: the admin page's login (a long password of letters and digits).
- `WL_BASE_URL`: `https://listen.ianduffy.com`.
- `TZ`: your time zone, e.g. `America/New_York`.
- Leave `WL_DEMO=1`: it is tester mode. Without it the site would be the whole app, open to anyone;
  setup and `wl deploy` stop the app if they find it off.

Then `bash setup.sh` once more. This run downloads the Whisper model the archive uses for timing
(about 500 MB, once; a build checks it is there before it starts), installs and starts the services,
hashes the admin password into Caddy's config (`caddy hash-password`), opens the firewall for ssh, http
and https, and checks the app: it must answer, and a visitor without a link must get only the dot.

### 5. Make a tester link

Open `https://listen.ianduffy.com/admin`, log in, and create a link for yourself (label "Ian test").
Copy it.

### 6. Check it on a phone

- In a private window, `https://listen.ianduffy.com`: only the ground and the dot, no text.
- Open your tester link on the phone: the address bar ends at `/`, the gate shows "Tap to begin".
- Tap: the phone asks for the microphone (it only does over HTTPS). Talk to the interviewer through the
  handover to the guide.
- Repeat on iPhone Safari, Android Chrome and a laptop (acceptance check 11), and with two testers at
  once (check 12). Then reset your test tester from the admin page.

### 7. Backups

- **DigitalOcean weekly backups:** on if you ticked the box in step 2 (20% of the droplet's price).
- **Nightly copy to the Mac** of `listeners/` and `shared/`, into `~/write-listen-backups/<folder>/<date>/`
  (unchanged files are hard links, so a night costs only what changed). Try it by hand first:

```bash
WL_DROPLET=root@listen.ianduffy.com bash deploy/backup.sh
```

Then nightly at 03:30 with launchd (a Mac asleep then runs it when it wakes). launchd can't read scripts
on the Desktop, so copy the script out first:

```bash
mkdir -p ~/bin ~/write-listen-backups
cp deploy/backup.sh ~/bin/write-listen-backup.sh
cp deploy/local.write-listen.backup.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/local.write-listen.backup.plist
```

The ssh key it uses must work without a passphrase prompt (macOS's keychain counts). The first run to
`listen.ianduffy.com` records the droplet's host key by itself (`StrictHostKeyChecking=accept-new`: you may
only have ssh'd to the IP so far); if the droplet is rebuilt, its key changes and the backup stops with
"Host key verification failed" until you run `ssh-keygen -R listen.ianduffy.com`. Its log is
`~/write-listen-backups/backup.log`.

## Updating

```bash
ssh root@<ip>
wl deploy
```

It pulls the checked-out branch, runs `uv sync`, makes sure `listeners/` and `shared/` exist (a fresh
droplet needs no data migration), checks that the code imports, installs any changed systemd unit,
restarts the app, checks it again and shows its status. Chapters being made carry on; open pages lose
the server for a few seconds. To follow another branch:
`sudo -u writelisten -H git -C /srv/write-listen fetch origin`, then
`sudo -u writelisten -H git -C /srv/write-listen checkout <branch>`, then `wl deploy` (the fetch: a branch
pushed after the clone isn't known on the droplet yet; `-H`: git reads the deploy key from the app user's home).

Other `wl` commands run as the app's user with its keys and `WL_BASE_URL` (read from `/srv/write-listen/.env`,
setup.sh's link to `/etc/write-listen/env`), e.g. `wl study create "Sam"` prints a link on the domain, and
`wl study report` (writes
`/srv/write-listen/study/report.md`; copy it off with `scp`).

## Troubleshooting

| Symptom | Look at |
| --- | --- |
| Anything odd in the app | `journalctl -u write-listen -f` (live), `journalctl -u write-listen --since "1 hour ago"`, or `/var/log/write-listen/app-<date>.log` for a past day |
| A chapter stalled or failed | its `run.log` and `status.json` under `/srv/write-listen/listeners/<tester>/chapters/<run>/` |
| Rate limits | the app and conversations: `journalctl -u write-listen \| grep -i -e 429 -e "rate limit"`; chapter builds print to their own `run.log`, not the journal: `grep -i -e 429 -e "rate limit" /srv/write-listen/listeners/*/chapters/*/run.log` |
| The site doesn't load, or a certificate error | `journalctl -u caddy -n 50`. Usually DNS not pointing at the droplet yet (`dig +short listen.ianduffy.com`) or port 80/443 closed (`ufw status`). Access log: `/var/log/caddy/write-listen.log` |
| 502 from Caddy | the app is down: `systemctl status write-listen`, then the journal |
| Every page is blank, even with a link | the cookie didn't stick: open the tester link again in that browser (a private window forgets it) |
| The admin login keeps asking | the hash in `/etc/caddy/Caddyfile` doesn't match. Change `ADMIN_PASSWORD` in the env file and run `bash /srv/write-listen/deploy/setup.sh` again; or by hand: `caddy hash-password`, paste the hash on the login line in `/etc/caddy/Caddyfile`, `systemctl reload caddy`, and `systemctl restart write-listen` if you changed the env file |
| No microphone prompt | the page must be `https://` on the domain; a plain `http://` address or the bare IP never gets the mic. On iPhone, Settings > Safari > Microphone must allow it |
| Voice connects but no sound, or drops | the audio is between the browser and OpenAI, not the droplet; nothing to open on the server. Try another network (some office Wi-Fi blocks WebRTC) |
| "Tester mode is OFF" and the app stopped | `WL_DEMO=1` is missing from `/etc/write-listen/env`; add it, `wl deploy` |
| "The Whisper model ... is not on this machine" | run setup.sh again, or the download command in the message as `sudo -u writelisten -H` from `/srv/write-listen` |
| Out of memory, builds killed | `free -h`, `journalctl -k \| grep -i oom`. Resize the droplet (DigitalOcean > Resize, CPU and RAM only, so it can go back) |
| Disk filling | `df -h`, `du -sh /srv/write-listen/listeners/*` |

Restarting by hand: `systemctl restart write-listen` (chapters being made carry on). Caddy's config:
`sudo -u caddy caddy validate --adapter caddyfile --config /etc/caddy/Caddyfile`, then `systemctl reload caddy`
(as Caddy's user, as setup.sh does: validating as root can leave a root-owned access log Caddy can't write).
