---
paths:
  - "Dockerfile"
  - "entrypoint.sh"
  - "docker-compose.yml"
  - ".env.example"
  - ".github/**"
  - "pyproject.toml"
  - "README.md"
  - "src/ticorom/startup/**"
---

# How TicoRom is configured and deployed

Read this before changing settings, the Docker image, the compose file, or proxy advice.

How to apply:

- Docker comes first. The entrypoint starts as root, creates `CONFIG_PATH` and `CACHE_PATH`, re-owns anything in them not owned by `PUID:PGID`, then drops to that user with `setpriv`. The library is only ever read.
- Inside the container the paths are fixed: `/data/library` (read-only), `/data/config`, `/data/cache`. Treat them as plain directories: assume nothing about the storage behind a mount.
- The image installs exactly the versions in `uv.lock` (`uv sync --locked`).
- `APP_URL` must be `https://`: tico only connected over HTTPS.
- On shutdown, uvicorn gives open downloads 5 seconds (`timeout_graceful_shutdown`), because Docker kills the container 10 seconds after `docker stop`.
- Publishing a GitHub release runs `.github/workflows/docker-publish.yml`, which builds the image and pushes it to `ghcr.io`.

> Section numbers (§N) and "Appendix A" are those of the original design document. `.claude/CLAUDE.md` lists which file holds each section. *Observed* means tested on a real Switch; *unverified* means not tested.

## 13. Configuration sketch

```env
APP_URL=https://bridge.example      # public address; must be https; every generated URL starts with it
ROMM_URL=https://romm.example
ROMM_API_TOKEN=rmm_...              # client API token; scopes roms.read, platforms.read
INSTANCE_SLUG=home                  # part of every package id (§7.3); do not change it later
SYNC_INTERVAL_MINUTES=60
LIBRARY_PATH=/data/library          # read-only mount; the folder that contains roms/
CONFIG_PATH=/data/config            # platforms.yaml and the SQLite database
CACHE_PATH=/data/cache              # built indexes and bundle zips; safe to delete
CACHE_MAX_GB=50                     # not used yet: for bundle zips (§7.2)
SKIP_FILES_OVER_4GIB=true           # FAT32 cards; set false only for exFAT
PUID=1000                           # user and group the bridge runs as in the container;
PGID=1000                           # must read the library and write config and cache
APP_PORT=8080                       # port on the host that the reverse proxy forwards to
```

*(As built with Docker Compose: `LIBRARY_PATH`, `CONFIG_PATH` and `CACHE_PATH` stay at the container paths, and the host folders are set in the `volumes` of `docker-compose.yml`; the three variables matter only when running outside Docker. `APP_PORT` is not read: the compose file maps `8080:8080`. `CACHE_MAX_GB` does nothing yet. `.env` holds only `APP_URL`, `ROMM_URL` and `ROMM_API_TOKEN`; the rest is set in the compose file.)*

**File permissions (`PUID` / `PGID`).** The container starts as root, creates the config and cache folders if they are missing, gives them to `PUID:PGID`, then drops to that user before the bridge starts. The library is only ever read.

**Paths.** `LIBRARY_PATH`, `CONFIG_PATH`, and `CACHE_PATH` are folders on the host. `docker-compose.yml` mounts them into the container at fixed places (`/data/library` read-only, `/data/config`, `/data/cache`), and the bridge inside always uses those. Run outside Docker, the bridge reads the three variables directly. The bridge treats all three as plain directories and assumes nothing about the storage behind them; the only requirement is that `PUID:PGID` can read the library and write the other two.

## Appendix A. Reverse proxy configuration for Switch downloads

Settings for the TLS proxy in front of the bridge (§6.4), chosen for ROM download speed. Variants: Nginx Proxy Manager (NPM), plain nginx, and Apache 2.4.

**What to expect.** The proxy was not the bottleneck in the spikes: with NPM's stock settings it delivered 20 MB/s to a PC while the Switch received 1.2 MB/s (§11). These settings remove the proxy-side costs that were observed, and add a log that shows where the real limit is. Only the congestion-control change in A.6 has a realistic chance of raising the Switch's speed, and only if the log points at packet loss.

**Test status.** The nginx directives were checked with a config parser for names, arguments, and context. Nothing here has been run against the Switch yet, and the Apache configuration has not been syntax-checked.

### A.1 What is known about the client

Measured in the spikes:

- One connection per download; no parallel range requests.
- `Range` on resume, no `If-Range`, no `Accept-Encoding`.
- 1.1–1.3 MB/s for multi-GB files.

Inferred, because tico is closed source. These hold if tico uses devkitPro's stock curl, which its request headers are consistent with:

- **TLS is done by the console's own SSL service**, not by a library inside tico. It supports TLS 1.3 on system version 14.0.0 and later, and falls back to TLS 1.2.
- **HTTP/1.1 only.** That curl build has no HTTP/2 support.
- **A 64 KiB TCP receive buffer** by default, growing to at most 256 KiB.

The last point suggests a hypothesis: a download limited by a 64 KiB window runs at about window ÷ round-trip time, and 64 KiB at 50 ms is 1.3 MB/s, the speed that was measured. A 50 ms round trip on a LAN would mean a poor Wi-Fi link or Wi-Fi power saving. The log in A.3 records the round-trip time, so it can confirm or rule this out.

### A.2 The settings and why

| Setting | Reason | Expected effect on speed |
|---|---|---|
| No spooling to disk (`proxy_max_temp_file_size 0`) | By default nginx reads ahead up to 1 GB per download into a temp file. On a router that is wasted flash writes and I/O. | None directly; removes disk load. |
| 2 MiB of in-memory read-ahead (`proxy_buffers 32 64k`) | Keeps the proxy fed without the disk, and reads the bridge in large chunks. | Small. |
| Full-size TLS records (`ssl_buffer_size 16k`, the default) | Fewer records for the console to decrypt per megabyte. Guides that lower it are tuning page-load time, not throughput. | Small; do not lower it. |
| HTTP/1.1 | The client most likely speaks nothing else. | None. |
| No compression, no rate limit | ROMs don't compress, and tico doesn't ask for it. | None; avoids wasted CPU. |
| Client's cipher order (`ssl_prefer_server_ciphers off`) | Whether AES-GCM or ChaCha20 is faster on the console is unknown; its own SSL service is the better judge. | Unknown; the log records what was chosen. |
| Long send timeout (`send_timeout 300s`) | A brief Wi-Fi stall should not end a transfer. tico can resume, but the user has to restart the install. | Fewer interrupted downloads. |
| Log without the token, with TLS and TCP details | The token is a password (§6.3). The TCP fields show whether the link, packet loss, or the console is the limit. | Diagnostic. |
| BBR congestion control on the proxy host (A.6) | Keeps its sending rate up on a lossy Wi-Fi link, where the default backs off. | Possibly large, if loss is the cause. |

### A.3 Nginx Proxy Manager

In the proxy host's **Advanced** tab, paste:

```nginx
# TicoRom: ROM downloads to a Switch
proxy_buffering          on;
proxy_buffer_size        16k;
proxy_buffers            32 64k;     # 2 MiB read-ahead per download, in RAM
proxy_busy_buffers_size  128k;
proxy_max_temp_file_size 0;          # never spool a download to disk
proxy_read_timeout       300s;
send_timeout             300s;
access_log               off;        # the URL contains the access token
```

On the other tabs:

- **Cache Assets: off.** It would cache cover images at the proxy for 30 minutes under their tokenized URLs, so they would outlive a revoked token.
- **HTTP/2 Support:** either setting; the Switch is not expected to use it.
- **Do not add** `ssl_protocols`, `ssl_ciphers`, `ssl_prefer_server_ciphers`, or `ssl_session_cache` to the Advanced tab. NPM already sets them in the same server block for Let's Encrypt certificates, nginx rejects the duplicate, and the host goes offline. NPM's values already match this appendix.

**Optional: the diagnostic log.** This needs file access to NPM's data folder. Create `/data/nginx/custom/http_top.conf` inside the container (the `data/nginx/custom/` folder of the NPM volume):

```nginx
map $request_uri $ticorom_uri {
    "~^/r/[^/]+(?<ticorom_rest>/.*)$"  "/r/-$ticorom_rest";
    default                            $request_uri;
}
log_format ticorom '$time_iso8601 $remote_addr $status "$request_method $ticorom_uri" '
                   'range="$http_range" sent=$body_bytes_sent time=$request_time '
                   'tls=$ssl_protocol/$ssl_cipher rtt=$tcpinfo_rtt rttvar=$tcpinfo_rttvar '
                   'cwnd=$tcpinfo_snd_cwnd rcv_space=$tcpinfo_rcv_space';
```

Restart NPM, then replace the `access_log off;` line in the Advanced tab with this block. Defining `location /` here makes NPM leave out its own, which is intended:

```nginx
location / {
    access_log /data/logs/ticorom_access.log ticorom;
    include conf.d/include/proxy.conf;
}
```

Do it in that order. If the Advanced tab names a log format that does not exist yet, the host goes offline. If the host uses an NPM Access List, note that it is not applied to a hand-written `location /`.

### A.4 Plain nginx

*(As built, TicoRom listens on port 8080, not the 8000 used in the examples below and in A.5. Replace `127.0.0.1:8000` with the address and port of the TicoRom container, e.g. `127.0.0.1:8080`.)*

```nginx
# http context
map $request_uri $ticorom_uri {
    "~^/r/[^/]+(?<ticorom_rest>/.*)$"  "/r/-$ticorom_rest";
    default                            $request_uri;
}
log_format ticorom '$time_iso8601 $remote_addr $status "$request_method $ticorom_uri" '
                   'range="$http_range" sent=$body_bytes_sent time=$request_time '
                   'tls=$ssl_protocol/$ssl_cipher rtt=$tcpinfo_rtt rttvar=$tcpinfo_rttvar '
                   'cwnd=$tcpinfo_snd_cwnd rcv_space=$tcpinfo_rcv_space';

upstream ticorom_bridge {
    server 127.0.0.1:8000;
    keepalive 8;
}

server {
    listen 443 ssl;
    server_name bridge.example;

    ssl_certificate     /etc/letsencrypt/live/bridge.example/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/bridge.example/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    ssl_session_cache   shared:ticorom:10m;
    ssl_session_timeout 1d;
    ssl_buffer_size     16k;             # the default; do not lower it

    access_log /var/log/nginx/ticorom_access.log ticorom;

    location / {
        proxy_pass         http://ticorom_bridge;   # no URI part: the path is passed through untouched
        proxy_http_version 1.1;
        proxy_set_header   Connection        "";
        proxy_set_header   Host              $host;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Real-IP         $remote_addr;

        proxy_buffering          on;
        proxy_buffer_size        16k;
        proxy_buffers            32 64k;
        proxy_busy_buffers_size  128k;
        proxy_max_temp_file_size 0;
        proxy_read_timeout       300s;
        send_timeout             300s;
    }
}
```

### A.5 Apache 2.4

Modules: `ssl`, `proxy`, `proxy_http`, `headers`, `setenvif`. Apache streams proxied responses and never spools them to disk, so there is no equivalent of the temp-file setting. It cannot log the TCP fields.

```apache
<VirtualHost *:443>
    ServerName bridge.example
    Protocols http/1.1

    SSLEngine on
    SSLCertificateFile    /etc/letsencrypt/live/bridge.example/fullchain.pem
    SSLCertificateKeyFile /etc/letsencrypt/live/bridge.example/privkey.pem
    SSLProtocol           -all +TLSv1.2 +TLSv1.3
    SSLHonorCipherOrder   off
    SSLCompression        off

    ProxyRequests     Off
    ProxyPreserveHost On
    ProxyPass         / http://127.0.0.1:8000/ timeout=300
    ProxyPassReverse  / http://127.0.0.1:8000/
    RequestHeader     set X-Forwarded-Proto "https"
    ProxyIOBufferSize 65536
    TimeOut           300

    # ROMs do not compress
    SetEnv no-gzip 1

    # Log without the access token
    SetEnvIf Request_URI "^(.*)$"            ticorom_path=$1
    SetEnvIf Request_URI "^/r/[^/]+(/.*)$"   ticorom_path=/r/-$1
    LogFormat "%{%Y-%m-%dT%H:%M:%S%z}t %a %>s \"%m %{ticorom_path}e\" range=\"%{Range}i\" sent=%B time_us=%D tls=%{SSL_PROTOCOL}x/%{SSL_CIPHER}x" ticorom
    CustomLog ${APACHE_LOG_DIR}/ticorom_access.log ticorom
</VirtualHost>
```

### A.6 Proxy host kernel settings (Linux)

Use BBR for the connections the proxy sends on. Needs Linux 4.13 or later and the `tcp_bbr` module (`modprobe tcp_bbr`).

On the host, in `/etc/sysctl.d/90-ticorom.conf`:

```
net.ipv4.tcp_congestion_control = bbr
net.ipv4.tcp_slow_start_after_idle = 0
```

If the proxy runs in Docker (NPM does), set them on the container instead, since each container has its own copy of these settings. With host networking, use the host file above.

```yaml
services:
  npm:
    sysctls:
      net.ipv4.tcp_congestion_control: bbr
      net.ipv4.tcp_slow_start_after_idle: 0
```

Treat this as an experiment: measure one large download before and after. BBR helps when the limit is packet loss on the Wi-Fi link, and does nothing when the console itself is the limit.

### A.7 Reading the log

After one large download, look at its line in `ticorom_access.log`. `sent ÷ time` is the speed in bytes per second, `rtt` is the round-trip time in microseconds, and `cwnd` is how many packets the proxy was allowed to have in flight. The thresholds below are rules of thumb.

| What the line shows | Likely limit | What to try |
|---|---|---|
| `rtt` above about 20000 (20 ms) on a LAN | The Wi-Fi link: band, signal, or power saving. | A.8. |
| Low `rtt`, small `cwnd` (under about 20) | Packet loss. | BBR (A.6), then the Wi-Fi link. |
| Low `rtt`, large `cwnd`, still slow | The console is reading slowly: tico's client or the card. | Nothing on the proxy. Report it to tico. |

`tls=` shows the protocol and cipher the console chose, which settles the TLS inferences in A.1.

### A.8 On the Switch

Not proxy settings, but they bear on the same number:

- **Use the 5 GHz band**, close to the access point.
- **Set the MTU to 1500** (System Settings > Internet > Internet Settings > the network > Change Settings > MTU). The Switch defaults to 1400. Users report faster downloads after the change; it was not measured here.
- **Dock with a USB LAN adapter** to take Wi-Fi out of the picture entirely.

