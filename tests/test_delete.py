"""Deleting recordings: only ever what has a complete copy on disk."""

import pytest
from click.testing import CliRunner

from pocket_libre import cli, commands
from pocket_libre.commands import PocketCommander, Recording, has_complete_copy

DONE = Recording("2026-10-03", "20261003081846", 97)     # complete copy on disk
SHORT = Recording("2026-10-03", "20261003090502", 3684)  # copy cut short
NEW = Recording("2026-10-03", "20261003192133", 5196)    # never downloaded


def _write(root, rec, size):
    path = root / rec.date / rec.filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff" * size)
    return path


# ── PocketCommander.delete ──────────────────────


@pytest.mark.asyncio
async def test_delete_sends_command_and_checks_listing():
    cmd = PocketCommander("AA:BB:CC:DD:EE:FF")
    sent = []
    listing = [DONE, NEW]

    async def send(command, verbose=False):
        sent.append(command)
        listing.remove(DONE)
        return ["MCU&D"]

    async def list_files(date):
        return list(listing)

    cmd._send, cmd.list_files = send, list_files
    assert await cmd.delete(DONE)
    assert sent == ["D&2026-10-03&20261003081846"]


@pytest.mark.asyncio
async def test_delete_reports_a_recording_that_stayed():
    cmd = PocketCommander("AA:BB:CC:DD:EE:FF")

    async def send(command, verbose=False):
        return ["MCU&D"]

    async def list_files(date):
        return [DONE]

    cmd._send, cmd.list_files = send, list_files
    assert not await cmd.delete(DONE)


# ── has_complete_copy ───────────────────────────


def test_complete_copy(tmp_path):
    assert has_complete_copy(DONE, _write(tmp_path, DONE, 389_408))


def test_missing_copy(tmp_path):
    assert not has_complete_copy(DONE, tmp_path / DONE.date / DONE.filename)


def test_empty_copy(tmp_path):
    assert not has_complete_copy(Recording("d", "t", 0), _write(tmp_path, DONE, 0))


def test_short_copy(tmp_path):
    assert not has_complete_copy(SHORT, _write(tmp_path, SHORT, 1_000_000))


# ── CLI ─────────────────────────────────────────


class _Device:
    """A fake Pocket shared by every connection the CLI opens."""

    recordings: list = []
    deleted: list = []

    def __init__(self, address):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def authenticate(self, key):
        return True

    async def list_all_recordings(self):
        return list(_Device.recordings)

    async def list_files(self, date):
        return [r for r in _Device.recordings if r.date == date]

    async def delete(self, rec):
        # The device goes by date and timestamp; `delete --date` has no duration.
        match = next(r for r in _Device.recordings if r.timestamp == rec.timestamp)
        _Device.recordings.remove(match)
        _Device.deleted.append(match)
        return True


@pytest.fixture
def device(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "PocketCommander", _Device)
    monkeypatch.setattr(cli, "load_config", lambda: {})
    _Device.recordings = [DONE, SHORT, NEW]
    _Device.deleted = []
    _write(tmp_path, DONE, 389_408)
    _write(tmp_path, SHORT, 1_000_000)
    return _Device


def _run(*args, input=None):
    return CliRunner().invoke(cli.cli, [*args, "--address", "addr", "--key", "k" * 16],
                              input=input)


def test_delete_downloaded_keeps_incomplete_and_missing(device, tmp_path):
    result = _run("delete", "--downloaded", "--yes", "--output-dir", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert device.deleted == [DONE]
    assert device.recordings == [SHORT, NEW]


def test_delete_downloaded_asks_first(device, tmp_path):
    result = _run("delete", "--downloaded", "--output-dir", str(tmp_path), input="n\n")
    assert result.exit_code != 0
    assert device.deleted == []


def test_delete_one(device):
    result = _run("delete", "--date", NEW.date, "--timestamp", NEW.timestamp, "--yes")
    assert result.exit_code == 0, result.output
    assert device.deleted == [NEW]


def test_delete_one_that_is_not_there(device):
    result = _run("delete", "--date", NEW.date, "--timestamp", "20990101000000", "--yes")
    assert result.exit_code == 1
    assert device.deleted == []


@pytest.mark.parametrize("args", [
    [],
    ["--downloaded", "--date", "2026-10-03"],
    ["--date", "2026-10-03"],
    ["--date", "..", "--timestamp", "x"],
    ["--date", "2026-10-03", "--timestamp", "x", "--since", "2026-10-01"],
])
def test_delete_rejects_bad_arguments(device, args):
    assert _run("delete", *args).exit_code == 2
    assert device.deleted == []


def test_download_all_delete_after(device, tmp_path, monkeypatch):
    async def download(address, key, rec, progress_callback=None):
        return b"\xff" * rec.estimated_bytes if rec == NEW else b""

    monkeypatch.setattr(commands, "download_with_retry", download)
    result = _run("download-all", "--output-dir", str(tmp_path), "--delete-after")
    assert result.exit_code == 0, result.output
    assert device.deleted == [DONE, NEW]
    assert device.recordings == [SHORT]
