"""Atomic writes must not share a scratch file between concurrent callers."""

from __future__ import annotations

import threading

import pytest

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


def test_an_external_edit_blocks_the_save_and_warns_it_costs_the_unsaved_text(tmp_path):
    """A stale `expected_mtime` must refuse, not quietly overwrite.

    Checked by hand on a real job editor: editing the YAML in the browser,
    changing the same line from a terminal, then saving kept the terminal's
    version. The refusal is the load-bearing part -- the wording is what keeps
    it useful. The old text was "请刷新后重试", which reads as advice; acting
    on it reloads the page and silently discards whatever the user had typed
    but not yet saved. A warning that does not mention its own cost is not a
    warning.
    """
    import os

    target = tmp_path / "job.yaml"
    target.write_text("name: original\n", encoding="utf-8")
    stale_mtime = os.path.getmtime(target)

    # The file moves on underneath us, as a terminal edit would. The mtime is
    # set explicitly because two writes in one test run land tens of
    # microseconds apart, inside the guard's 10ms jitter tolerance -- so a
    # bare second write would pass the check and prove nothing.
    later = stale_mtime + 60
    target.write_text("name: terminal\n", encoding="utf-8")
    os.utime(target, (later, later))

    with pytest.raises(IOError) as excinfo:
        yaml_io.save_job_yaml(str(target), "name: browser\n", expected_mtime=stale_mtime)

    assert target.read_text(encoding="utf-8") == "name: terminal\n", \
        "the external edit was overwritten"

    message = str(excinfo.value)
    assert "外部修改" in message, message
    assert "未保存" in message and "复制" in message, \
        f"the warning does not say the refresh costs the unsaved edit: {message}"


def test_a_matching_mtime_saves_normally(tmp_path):
    """The conflict guard must not block the ordinary save it guards."""
    import os

    target = tmp_path / "job.yaml"
    target.write_text("name: original\n", encoding="utf-8")

    result = yaml_io.save_job_yaml(
        str(target), "name: browser\n", expected_mtime=os.path.getmtime(target)
    )

    assert target.read_text(encoding="utf-8") == "name: browser\n"
    assert result is not None
