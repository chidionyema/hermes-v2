"""cron/evolution.jobs was crontab format while the loader is JSON Lines, so the
evolution lane installed nothing from the day it was added.

bin/install-cron.py reads a .jobs file as JSON Lines. cron/evolution.jobs was
written as crontab rows instead -- `0 3 * * *   work   <prose>` -- so json.loads
read the leading 0 as a number and stopped: "is not valid JSON - Extra data:
line 1 column 3 (char 2)". The `--feature` gate returns BEFORE load(), so an
off lane would have hidden it; evolution is on, so every boot printed the error
and created no job. Seen on the cluster in oke-check run 33355125388, pod
hermes-agent-gateway-7cbdfdc7b5-c5kxl, 2026-08-31.

The control is placed where every .jobs file merges -- the loader itself, not a
copy of its rules -- so a future lane file is covered without anyone adding a
row here. Founder, 2026-08-31 (docs/founder/2026-08-31T0404Z-yes-and-this-is-
the-answer-to-your-05f4c6af.md): "put a JSON schema check in the pre-commit or
CI gate -- that's a rung-2 control for a file class that will otherwise break
again silently."
"""

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
JOBS_FILES = sorted(ROOT.glob("cron/*.jobs"))


def _loader():
    """install-cron.py's own load(), never a reimplementation of its rules.

    A second copy of the parsing rules is the silent-green class: the guard
    passes while the loader still refuses the file.
    """
    spec = importlib.util.spec_from_file_location(
        "install_cron", ROOT / "bin" / "install-cron.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.load


def test_there_is_at_least_one_jobs_file_to_grade():
    """A glob that matched nothing would make every test below vacuously green."""
    assert JOBS_FILES, f"no cron/*.jobs under {ROOT}"


@pytest.mark.parametrize("path", JOBS_FILES, ids=lambda p: p.name)
def test_every_jobs_file_loads_with_install_crons_own_loader(path):
    """load() calls sys.exit on a bad line, so SystemExit IS the boot failure."""
    try:
        jobs = _loader()(str(path))
    except SystemExit as e:
        pytest.fail(f"{path.name} would abort the gateway boot: {e}")
    assert jobs, f"{path.name} declares no jobs at all"


@pytest.mark.parametrize("path", JOBS_FILES, ids=lambda p: p.name)
def test_no_job_names_a_skill_that_does_not_exist(path):
    """`hermes cron create --skill <missing>` fails per job, leaving a lane
    half-installed -- the same shape of silent breakage one level down."""
    have = {p.name for p in (ROOT / "skills").iterdir() if p.is_dir()}
    for job in _loader()(str(path)):
        missing = [s for s in (job.get("skills") or []) if s not in have]
        assert not missing, (
            f"{path.name}: {job['name']} names absent skill(s) {missing}"
        )


def test_the_evolution_lane_actually_declares_jobs():
    """The incident itself: the lane is on and installed nothing."""
    jobs = _loader()(str(ROOT / "cron" / "evolution.jobs"))
    assert len(jobs) >= 2, f"evolution lane declares {len(jobs)} job(s)"
    assert {j["name"] for j in jobs} == {"evolution-skills", "evolution-curator"}
