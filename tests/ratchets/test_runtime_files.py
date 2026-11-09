"""The guarded writers refuse code folders and write everywhere else."""

from __future__ import annotations

import sqlite3
import tomllib
from pathlib import Path

import pytest

from src.fsio import runtime_files as rf


def test_code_folders_match_the_ratchet_config() -> None:
    config = tomllib.loads((rf.REPO_ROOT / ".fleet.toml").read_text(encoding="utf-8"))
    assert tuple(config["runtime_write_ratchet"]["code_folders"]) == rf.CODE_FOLDERS


@pytest.mark.parametrize("folder", rf.CODE_FOLDERS)
def test_every_writer_refuses_a_path_under_a_code_folder(folder: str) -> None:
    target = rf.REPO_ROOT / folder / "_never_written" / "x.txt"
    for call in (
        lambda: rf.make_dirs(target.parent),
        lambda: rf.open_for_write(target),
        lambda: rf.save_text(target, "x"),
        lambda: rf.save_bytes(target, b"x"),
        lambda: rf.copy_file(__file__, target),
        lambda: rf.move(__file__, target),
        lambda: rf.file_handler(target),
        lambda: rf.connect_sqlite(target),
    ):
        with pytest.raises(ValueError, match="code folder"):
            call()
    assert not (rf.REPO_ROOT / folder / "_never_written").exists()


def test_a_dotdot_path_into_a_code_folder_is_refused() -> None:
    sneaky = rf.REPO_ROOT / "data" / ".." / "src" / "_never_written.txt"
    with pytest.raises(ValueError, match="code folder"):
        rf.save_text(sneaky, "x")


def test_writers_work_outside_the_code_folders(tmp_path: Path) -> None:
    folder = rf.make_dirs(tmp_path / "a" / "b")
    assert folder.is_dir()
    rf.save_text(folder / "t.txt", "héllo\n")
    assert (folder / "t.txt").read_text(encoding="utf-8") == "héllo\n"
    rf.save_bytes(folder / "b.bin", b"\x00\x01")
    rf.copy_file(folder / "b.bin", folder / "c.bin")
    assert (folder / "c.bin").read_bytes() == b"\x00\x01"
    rf.move(folder / "c.bin", folder / "d.bin")
    assert not (folder / "c.bin").exists() and (folder / "d.bin").exists()
    with rf.open_for_write(folder / "log.txt", "a", encoding="utf-8") as fh:
        fh.write("line\n")
    handler = rf.file_handler(folder / "h.log")
    handler.close()
    assert (folder / "h.log").exists()
    conn = rf.connect_sqlite(folder / "db.sqlite3")
    conn.execute("CREATE TABLE t (x)")
    conn.close()
    assert isinstance(sqlite3.connect(folder / "db.sqlite3"), sqlite3.Connection)
