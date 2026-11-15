# tico spikes (DESIGN.md §11)

Throwaway test repositories for tico plus a small file server. Standard library only, no install step.

Both rounds are finished (round 1 on 2026-11-11, round 2 on 2026-11-13/14); the results are in DESIGN.md §11. The steps below are kept so the tests can be repeated. Not yet run: the 25,000-package index.

## Run

```powershell
cd C:\Repos\TicoRom\spikes
python make_spike.py     # builds site\ ; rerun after changing files or options
python serve.py          # serves it and prints the addresses to enter in tico
```

- **Base URL.** `make_spike.py` keeps the base URL from the last run. Change it with `--base-url https://<host>`, never by editing the index files. tico only connected over HTTPS in round 1, so the server sits behind the reverse proxy.
- **Real games.** Round 2 uses the files in `site\files`: the first `.gba`, the first `.7z`, the largest `.chd` under 4 GiB, and the smallest `.chd` of 4 GiB or more. The console for each extension is set in `REAL_CONSOLES` at the top of `make_spike.py` (`.gba` → `gba`, `.7z` → `snes`, `.chd` → `ps2`).
- **Firewall.** The PC must accept inbound TCP on the port (8765) from the LAN: an allow rule for the port, and no Block rule for `python.exe` on the active network profile.

## Round 2: what to test

Add `https://<host>/r2/index.json` in tico (Store > Sources > Add repository) and install the packages in order. The quick ones come first.

| Package | Spike | What to do | What to report |
|---|---|---|---|
| Spike 11 – real GBA ROM, file mode | E | Install, then launch it. | Installs? Launches? |
| Spike 12 – real `.7z`, file mode | E | Install, then launch it. | Installs? Launches? |
| Spike 13 – real GBA ROM as `type: rom` (optional) | – | Install. | The round 1 SD-card error again, or does a real ROM work? |
| Spike 14 – extract mode | F | Install, then launch it. | Is `… (extract test).gba` in `tico/roms/gba`? Is a zip left behind? |
| Spike 15 – bundle | G | Install, then look in `tico/roms/psx`. | Are the `.cue` and both `.bin` files there, names intact? |
| Spike 16 – folder bundle | G | Install, then look in `tico/roms/psx`. | The subfolder's name. Does the game show on the shelf with Scan Subfolders off? |
| Spike 17 – resume after a dropped connection | D | Install. The server drops the first download at 16 of 64 MiB. If tico reports an error, start the install again. | What tico showed. Did the progress continue from where it stopped, or start over? |
| Spike 18 – real CHD, 2.0 GiB | D, E, H | Partway through, interrupt once: sleep the console or switch Wi-Fi off for about 20 seconds. Launch it afterwards. | Did it resume or restart? Installs? Launches? |
| Spike 19 – real CHD over 4 GiB | H | Install. Needs that much free space on the card. Launch it if it installs. | Is the card FAT32 or exFAT? If it fails: at what point, and with what message? |

Then add `https://<host>/huge/index.json` (25,000 packages, 14.7 MiB; 422 KiB as `huge/index.json.gz`):

- How long does adding it take, and does tico stay responsive?
- Is search still quick (try `crimson`, about 1,250 hits)?

Notes:

- **The log settles the resume question.** A resumed download appears in `serve.log` as a request with `Range=bytes=N-` and status 206. A restart appears as a second plain 200.
- Spike 17 drops the connection only once per run of `serve.py`. Restart the server to repeat it.
- Spikes 15–17 are dummy data and will not boot. Spikes 18 and 19 need the console's BIOS set up in tico to launch.
- Transfer speeds in the log are PC to proxy, not proxy to Switch; the proxy buffers.
- Afterwards, delete the test files from `tico/roms/` (`Spike …` and `… (rom type)` / `… (extract test)` copies).

## What to send back

The filled-in table, anything tico showed as an error, and `serve.log`. The log records every request with all headers, deliberate cuts, aborted transfers, and the client's address as reported by the proxy.

## Round 1 (done)

- `https://<host>/index.json` – spikes A and B (packages Spike 1–8, dummy data).
- `https://<host>/big/index.json` – spike C (5,000 packages).
- Variants of the round 1 address: `/redirect/index.json`, `/index.json.gz`, and the tokenized form printed by `serve.py`.

Results and conclusions are in DESIGN.md §11.
