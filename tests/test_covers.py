import asyncio

import httpx
import pytest

from ticorom import covers
from ticorom.romm import RomMClient, RomMRom

PNG = b"\x89PNG\r\n\x1a\n" + b"p" * 300
JPEG = b"\xff\xd8\xff\xe0" + b"j" * 100
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"w" * 50
AVIF = b"\x00\x00\x00\x1cftypavif" + b"a" * 20


@pytest.mark.parametrize("data, expected", [
    (PNG, "png"), (JPEG, "jpg"), (WEBP, "webp"), (b"GIF89a....", "gif"), (b"BM......", "bmp"),
    (AVIF, None), (b"<html>not found</html>", None), (b"", None),
])
def test_image_type(data, expected):
    assert covers.image_type(data) == expected


class Assets:
    """RomM's /assets folder. Records the requests it receives."""

    def __init__(self, files, fail=False):
        self.files, self.fail, self.requests = files, fail, []

    def handler(self, request):
        self.requests.append((request.method, request.url.path))
        if self.fail:
            raise httpx.ConnectError("connection refused")
        data = self.files.get(request.url.path)
        if data is None:
            return httpx.Response(404)
        if request.method == "HEAD":
            return httpx.Response(200, headers={"Content-Length": str(len(data))})
        return httpx.Response(200, content=data)


def rom(rom_id=7, small="/assets/7/small.png?ts=1", large="/assets/7/big.png?ts=1"):
    return RomMRom(id=rom_id, platform_id=1, fs_name="Game.7z", full_path="roms/gba/Game.7z",
                   path_cover_small=small, path_cover_large=large)


def fetch(assets, roms, known, cache_path):
    async def run():
        async with RomMClient("https://romm.example", "rmm_x", httpx.MockTransport(assets.handler)) as romm:
            return await covers.fetch_all(romm, roms, known, cache_path)
    return asyncio.run(run())


def test_the_smallest_cover_is_used(tmp_path):
    assets = Assets({"/assets/7/small.png": PNG, "/assets/7/big.png": JPEG})  # here the "large" one is smaller
    [cover] = fetch(assets, [rom()], {}, tmp_path)
    assert cover.file.startswith("7-") and cover.file.endswith(".jpg")
    assert (tmp_path / "covers" / cover.file).read_bytes() == JPEG
    assert covers.FILE_NAME_RE.fullmatch(cover.file)
    assert ("GET", "/assets/7/small.png") not in assets.requests  # the bigger one is never downloaded


def test_a_format_tico_cannot_show_is_skipped(tmp_path):
    assets = Assets({"/assets/7/small.png": AVIF, "/assets/7/big.png": PNG})
    [cover] = fetch(assets, [rom()], {}, tmp_path)
    assert cover.file.endswith(".png")


def test_no_usable_cover(tmp_path):
    [cover] = fetch(Assets({"/assets/7/small.png": AVIF}), [rom()], {}, tmp_path)
    assert cover.file is None
    [cover] = fetch(Assets({}), [rom(small=None, large=None)], {}, tmp_path)
    assert cover.file is None and cover.source == "|"


def test_an_unchanged_cover_is_not_downloaded_again(tmp_path):
    [first] = fetch(Assets({"/assets/7/small.png": PNG}), [rom()], {}, tmp_path)
    assets = Assets({"/assets/7/small.png": PNG})
    [again] = fetch(assets, [rom()], {7: first}, tmp_path)
    assert again == first
    # Only a check for a WebP copy, nothing downloaded
    assert assets.requests == [("HEAD", "/assets/7/small.webp"), ("HEAD", "/assets/7/big.webp")]


def test_a_webp_copy_is_used_when_it_is_the_smallest(tmp_path):
    assets = Assets({"/assets/7/small.png": PNG, "/assets/7/small.webp": WEBP, "/assets/7/big.png": PNG * 3})
    [cover] = fetch(assets, [rom()], {}, tmp_path)
    assert cover.file.endswith(".webp")
    assert ("GET", "/assets/7/small.png") not in assets.requests


def test_a_png_cover_switches_to_webp_once_romm_has_made_a_copy(tmp_path):
    [first] = fetch(Assets({"/assets/7/small.png": PNG}), [rom()], {}, tmp_path)
    assert first.file.endswith(".png")
    # RomM's conversion task has run since: same paths, a new WebP file beside the PNG
    [second] = fetch(Assets({"/assets/7/small.png": PNG, "/assets/7/small.webp": WEBP}), [rom()], {7: first}, tmp_path)
    assert second.file.endswith(".webp")


def test_a_webp_cover_is_left_alone_without_any_request(tmp_path):
    [first] = fetch(Assets({"/assets/7/small.webp": WEBP, "/assets/7/small.png": PNG}), [rom()], {}, tmp_path)
    assets = Assets({"/assets/7/small.webp": WEBP, "/assets/7/small.png": PNG})
    assert fetch(assets, [rom()], {7: first}, tmp_path) == [first] and assets.requests == []


@pytest.mark.parametrize("path, copy", [
    ("/assets/7/small.png?ts=2026-11-10 03:58:52", "/assets/7/small.webp"),
    ("/assets/7/big.JPG", "/assets/7/big.webp"),
    ("/assets/7/big.jpeg", "/assets/7/big.webp"),
    ("/assets/7/small.webp", None),
    ("/assets/7/small", None),
])
def test_webp_copy_path(path, copy):
    assert covers._webp_copy(path) == copy


def test_a_changed_cover_gets_a_new_file(tmp_path):
    [first] = fetch(Assets({"/assets/7/small.png": PNG}), [rom()], {}, tmp_path)
    [second] = fetch(Assets({"/assets/7/small.png": JPEG}), [rom(small="/assets/7/small.png?ts=2")],
                     {7: first}, tmp_path)
    assert second.file != first.file and second.file.endswith(".jpg")


def test_a_missing_file_is_downloaded_again(tmp_path):
    [first] = fetch(Assets({"/assets/7/small.png": PNG}), [rom()], {}, tmp_path)
    (tmp_path / "covers" / first.file).unlink()  # e.g. the cache folder was emptied
    [again] = fetch(Assets({"/assets/7/small.png": PNG}), [rom()], {7: first}, tmp_path)
    assert (tmp_path / "covers" / again.file).is_file()


def test_when_romm_cannot_be_reached_the_old_cover_stays(tmp_path):
    [first] = fetch(Assets({"/assets/7/small.png": PNG}), [rom()], {}, tmp_path)
    changed = rom(small="/assets/7/small.png?ts=2")
    assert fetch(Assets({}, fail=True), [changed], {7: first}, tmp_path) == [first]
    assert fetch(Assets({}, fail=True), [changed], {}, tmp_path) == []   # nothing to keep: no cover this time


def test_remove_unused(tmp_path):
    folder = tmp_path / "covers"
    folder.mkdir()
    for name in ("7-aaaaaaaa.png", "7-bbbbbbbb.png", "8-cccccccc.jpg"):
        (folder / name).write_bytes(b"x")
    covers.remove_unused(tmp_path, [covers.Cover(7, "s", "7-bbbbbbbb.png"), covers.Cover(9, "s", None)])
    assert [p.name for p in folder.iterdir()] == ["7-bbbbbbbb.png"]
