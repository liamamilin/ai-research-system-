"""Atomic writes must not share a scratch file between concurrent callers."""

from __future__ import annotations

import threading

from web.services import yaml_io


def test_concurrent_saves_both_land_and_neither_names_a_missing_file(tmp_path):
    """Two writers to one path must not share a temp file.

    `atomic_write` used `file_path + ".tmp"`, so both writers opened the same
    file: one truncated what the other was writing, and the first rename pulled
    it out from under the second, which then raised "No such file or directory"
    naming a path the caller had never mentioned. One save was silently lost.
    `atomic_create` already avoided this with a per-call temp name.
    """
    target = tmp_path / "job.yaml"
    target.write_text("name: original\n", encoding="utf-8")

    payloads = [f"name: a\nvalue: {'A' * 200000}\n",
                f"name: b\nvalue: {'B' * 200000}\n"]
    errors: list[Exception] = []
    barrier = threading.Barrier(2)

    def save(content: str) -> None:
        try:
            barrier.wait(timeout=5)
            yaml_io.atomic_write(str(target), content)
        except Exception as exc:  # noqa: BLE001 - collected for the assertion
            errors.append(exc)

    threads = [threading.Thread(target=save, args=(c,)) for c in payloads]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert not errors, f"a concurrent save failed: {[str(e) for e in errors]}"
    final = target.read_text(encoding="utf-8")
    assert final in payloads, "the file is neither writer's content -- interleaved"
    leftovers = [p.name for p in tmp_path.iterdir() if ".tmp" in p.name]
    assert not leftovers, f"temp files left behind: {leftovers}"


def test_a_failed_write_leaves_no_scratch_file(tmp_path, monkeypatch):
    """A partial file next to the target is the thing a reader might pick up."""
    target = tmp_path / "job.yaml"
    target.write_text("name: original\n", encoding="utf-8")

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(yaml_io.os, "replace", boom)
    try:
        yaml_io.atomic_write(str(target), "name: new\n")
    except IOError:
        pass
    else:
        raise AssertionError("the failure was not reported")

    assert target.read_text(encoding="utf-8") == "name: original\n", \
        "the original file was damaged by a failed write"
    assert [p.name for p in tmp_path.iterdir() if ".tmp" in p.name] == []


def test_a_plain_write_still_works(tmp_path):
    target = tmp_path / "job.yaml"
    yaml_io.atomic_write(str(target), "name: written\n")
    assert target.read_text(encoding="utf-8") == "name: written\n"
