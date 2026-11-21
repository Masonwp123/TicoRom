import asyncio

import httpx
import pytest

from ticorom.romm import RomMClient, RomMError


def client(handler):
    return RomMClient("https://romm.example", "rmm_x", httpx.MockTransport(handler))


def games(offset, limit, count):
    return [{"id": i, "platform_id": 1, "fs_name": f"{i}.7z", "full_path": f"roms/gba/{i}.7z"}
            for i in range(offset, min(offset + limit, count))]


@pytest.mark.parametrize("total", [250, None])
def test_all_roms_reads_every_page_with_or_without_a_total(total):
    offsets = []

    def handler(request):
        offset, limit = int(request.url.params["offset"]), int(request.url.params["limit"])
        offsets.append(offset)
        return httpx.Response(200, json={"items": games(offset, limit, 250), "total": total})

    async def run():
        async with client(handler) as romm:
            return await romm.all_roms(1)

    assert len(asyncio.run(run())) == 250
    assert offsets == [0, 100, 200]


def test_a_platform_that_fills_its_last_page_exactly():
    def handler(request):
        offset = int(request.url.params["offset"])
        return httpx.Response(200, json={"items": games(offset, 100, 200), "total": None})

    async def run():
        async with client(handler) as romm:
            return await romm.all_roms(1)

    assert len(asyncio.run(run())) == 200   # one extra, empty page ends it


def test_a_network_error_without_a_message_still_says_something():
    def handler(request):
        raise httpx.ConnectTimeout("")

    async def run():
        async with client(handler) as romm:
            await romm.platforms()

    with pytest.raises(RomMError) as caught:
        asyncio.run(run())
    assert str(caught.value).endswith(": ConnectTimeout") and caught.value.unreachable


@pytest.mark.parametrize("status, unreachable", [(502, True), (503, True), (401, False), (403, False), (404, False)])
def test_which_answers_count_as_unreachable(status, unreachable):
    async def run():
        async with client(lambda request: httpx.Response(status)) as romm:
            await romm.platforms()

    with pytest.raises(RomMError) as caught:
        asyncio.run(run())
    assert caught.value.unreachable is unreachable
