# What was tested on a real Switch

Results of the tests run on real hardware: a modded Switch running tico, behind an HTTPS reverse proxy. Treat the *observed* behaviour here as ground truth about tico. Anything not listed has not been tested; check it on hardware before relying on it.

> Section numbers (§N) and "Appendix A" are those of the original design document. `.claude/CLAUDE.md` lists which file holds each section. *Observed* means tested on a real Switch; *unverified* means not tested.

## 11. Spike results

### Round 1 (spikes A–C)

Run on 2026-11-11. All payloads were dummy data (random bytes).

| Spike | Question | Result | Consequence |
|---|---|---|---|
| **A** | Does `type: zip` + `extract: false` + `filename` save a non-zip file intact? | **Yes.** A 4 MiB and a 128 MiB non-zip file installed under their `.chd` filenames. | Single files need no repacking, whatever their size. Bundles are only for multi-file games (§7.2). |
| **B** | Does tico accept credentials or long tokens in the URL, and follow redirects? | **Yes.** A 32-character path token worked for the index and for downloads. 302 redirects were followed for both. `user:pass@` credentials arrived as HTTP Basic. | Path tokens work as designed (§6.3). |
| **C** | How does tico behave with ~5,000 packages in one index? | **Fine.** The repository added quickly, tico stayed responsive, and search was fast. | A combined repository is viable (§6.5). |

Also confirmed:

- **`sha256` is enforced with `extract: false`.** A package with a deliberately wrong hash was refused ("could not be verified"). The successful installs above were therefore hash-checked.
- **Console tags.** All 23 of tico's ROM folder names produced a shelf (§8).
- **`.json.gz` index** is accepted.
- **A genuine zip with `extract: false`** was saved whole, from a percent-encoded URL containing spaces and parentheses.

What did not work:

- **`type: rom`.** A `rom` package with a `.sfc` filename never appeared in the listing. Three `rom` packages with `.gba` filenames were listed and downloaded completely (the server sent every byte), but tico then reported that the file could not be written to the SD card. A zip package installed into the same folder, so the card and folder were fine. Round 2 repeated this with a real ROM and got the same error, so the dummy payload was not the cause. The cause remains unknown and is not pursued, because the file mode covers the same case.
- **Plain `http://`.** tico only connected over HTTPS. No plain-HTTP request from the Switch reached the test server, so whether tico refused the address or the network dropped it was not determined.

### Round 2 (spikes D–H)

Run on 2026-11-13 and 14 with real games: a GBA ROM, an SNES game as a `.7z`, and two PS2 `.chd` images (about 2 GiB and 6.8 GiB). The bundle and resume-cut payloads were dummy data.

| Spike | Question | Result | Consequence |
|---|---|---|---|
| **D** | Does tico continue an interrupted download with `Range`? | **Yes.** The server dropped a download at 16 of 64 MiB; tico reported a connection problem, and starting the install again sent `Range: bytes=16777216-` and completed. A 2 GiB download interrupted by Wi-Fi loss and by closing tico also continued from the byte it had reached. | `Range` support is required and is enough. tico sends no `If-Range`, hence the versioned URLs in §6.6. |
| **E** | Do real games delivered in file mode launch? | **Yes, with one catch.** The `.gba` and the `.7z` installed and played. The PS2 `.chd` installed, but launched only after it was rebuilt as a CD-type CHD. | File mode is confirmed for cartridge ROMs, archives the core opens, and disc images. Check the CHD variant per platform (§9). |
| **F** | Does a zip with no `extract` flag unpack its ROM into `install`? | **Yes.** The unpacked ROM installed and played. | Extract mode works as documented. |
| **G** | Does a bridge-built stored zip unpack flat? What does `extract_to_folder` do? | **Yes.** The `.cue` and both `.bin` files appeared in the console folder. With `extract_to_folder` they landed in a subfolder, and tico still listed the game. | Bundle flat by default; `extract_to_folder` is the fallback for name collisions (§9). |
| **H** | Size limits. | **A real limit at 4 GiB.** The 2 GiB images installed. The 6.8 GiB image downloaded for 55 minutes and failed when the file on the card reached 4 GiB, the FAT32 limit. The 25,000-package index was not run. | Skip files ≥ 4 GiB by default (§9). |

Also observed in round 2:

- **`type: rom` fails with a real ROM too** (Spike 13), with the same SD-card error as round 1.
- **`size` is not enforced for `type: zip`.** After a test file was replaced, the index still listed the old size (about 10% smaller) and the package installed. The hash is what tico checks.
- **`sha256` in uppercase hex** is accepted.
- **Retries.** After the 4 GiB failure tico retried five times within five seconds, each time with `Range` from the same offset.

**Download speed.** The Switch received multi-GB files at 1.1–1.3 MB/s (about 10 Mbit/s), measured from the server log over three long transfers. The same proxy delivered 20 MB/s to a PC on the same Wi-Fi, and the server fed the proxy at 25–60 MiB/s. The limit is therefore on the Switch's side: its Wi-Fi link, tico's HTTP/TLS client, or writing to the card. Which one is not known.

Experiments, cheapest first:

1. **Check the Switch's Wi-Fi band and signal** in the router's client list. A 2.4 GHz link would explain most of it.
2. **Apply the proxy settings and log from Appendix A**, run one large download, and read the round-trip time and window from the log (A.7). This separates the Wi-Fi link, packet loss, and the console itself.
3. **Send a large file to the Switch by another route**, such as an FTP homebrew. That measures Wi-Fi plus card writes without tico's HTTP/TLS client.
4. **Try wired networking** with the console docked and a USB LAN adapter.
5. If the other route is fast and tico is slow, the limit is in tico's client and can't be fixed from the bridge. Report it upstream. A small default socket buffer is a candidate (Appendix A.1, *unverified*).

### Cover test

Run on 2026-11-20 with `spikes/covers/` (since removed from the repository): one package per image variant, all made from one real RomM cover with the variant's name printed on it.

| Variant | Displayed |
|---|---|
| PNG (RomM's format), PNG 1024×1024 at 1.6 MiB, PNG 64 px wide | Yes |
| JPEG, progressive JPEG | Yes |
| WebP lossy, WebP lossless | Yes |
| GIF, BMP | Yes |
| AVIF | **No** |
| JPEG bytes behind a `.png` address; PNG with no extension | Yes: tico reads the image itself, not the name |
| `banner` and `screenshots` | Shown on the package page |

From the server log: tico fetched each image once, about five at a time, sending only `Accept: */*` and no conditional headers. It sent no request to revalidate an image during the test.

Consequences: covers are accepted in any format but AVIF, the smallest one wins, and a changed cover must change its URL (§7.3, "Covers"). Banners and screenshots are a stretch goal (§12).

Still open:

- **Download speed**, above.
- **Files ≥ 4 GiB on an exFAT card.**
- **A real multi-file game.** The bundle test used dummy `.cue` / `.bin` files, so it checked the layout on the card, not a launch.
- **Bundles over 4 GiB**, which need Zip64.
- **Whether `extract_to_folder` games depend on the Scan Subfolders setting**, and whether extract mode leaves the zip behind.
- **Libraries beyond 5,000 games.**
- **Plain `http://`** (round 1).

