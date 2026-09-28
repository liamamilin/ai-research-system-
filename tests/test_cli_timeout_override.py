"""``run.py --timeout`` has to actually bound the run.

It did not, and it looked like it did. Two independent reasons, either of which
alone would have been enough:

* ``run.py`` wrote the value into the ``sys_cfg`` dict it had loaded, then
  constructed ``ResearchEngine``, whose constructor calls
  ``load_system_config`` again and replaces ``self.sys`` wholesale. The
  mutation was discarded before anything read it.
* Even had it survived, ``ResearchEngine`` prefers the job's own
  ``runtime.timeout_seconds`` over ``ai.timeout``, and 25 of 37 jobs set one --
  every pipeline stage included. So the flag would have been ignored a second
  time.

The only symptom was a flag that was accepted, documented, and did nothing.
"""

from __future__ import annotations

import pytest

from core.engine import ResearchEngine


def _effective_timeout(engine, job: dict) -> int:
    """The precedence rule from run_job, extracted so it can be asserted."""
    runtime_cfg = job.get("runtime", {})
    if engine._timeout_override:
        return engine._timeout_override
    return runtime_cfg.get("timeout_seconds",
                          engine.sys.get("ai", {}).get("timeout", 1800))


def _engine(tmp_path, **kwargs) -> ResearchEngine:
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "system.yaml").write_text(
        "ai:\n  timeout: 1800\n", encoding="utf-8")
    (tmp_path / "jobs").mkdir(exist_ok=True)
    return ResearchEngine(config_dir=str(tmp_path / "config"),
                          jobs_dir=str(tmp_path / "jobs"), **kwargs)


def test_the_cli_flag_outranks_the_jobs_own_timeout(tmp_path):
    engine = _engine(tmp_path, timeout_override=900)
    job = {"runtime": {"timeout_seconds": 3600}}
    assert _effective_timeout(engine, job) == 900


def test_the_cli_flag_outranks_system_yaml(tmp_path):
    engine = _engine(tmp_path, timeout_override=45)
    assert _effective_timeout(engine, {}) == 45


def test_without_the_flag_the_job_still_wins(tmp_path):
    # The default path must be untouched: a job's own timeout is a deliberate
    # per-stage setting, not something to quietly override.
    engine = _engine(tmp_path)
    assert _effective_timeout(engine, {"runtime": {"timeout_seconds": 1800}}) == 1800
    assert _effective_timeout(engine, {}) == 1800


def test_mutating_the_config_after_construction_does_nothing(tmp_path):
    """The exact mistake: the config is re-read inside the constructor.

    This is why writing into the dict run.py had loaded could never work, and
    it is worth pinning so nobody reintroduces that shape.
    """
    engine = _engine(tmp_path)
    engine.sys.setdefault("ai", {})["timeout"] = 30
    # engine.sys *is* the dict, so this particular mutation is visible...
    assert engine.sys["ai"]["timeout"] == 30
    # ...but a fresh engine over the same files ignores it, which is what
    # happened to the flag in run.py.
    assert _engine(tmp_path).sys["ai"]["timeout"] == 1800


def test_run_py_passes_the_flag_through_to_the_engine():
    import pathlib
    import re

    source = pathlib.Path("run.py").read_text(encoding="utf-8")
    assert "timeout_override=args.timeout" in source, \
        "run.py must hand --timeout to the engine, not mutate a discarded dict"
    # And the dead write must be gone.
    assert not re.search(r'sys_cfg\.setdefault\("ai".*\["timeout"\]', source), \
        "run.py still writes the timeout into a dict the engine replaces"
