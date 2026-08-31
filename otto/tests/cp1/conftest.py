"""Fixtures for the CP1 spine-and-measurement BDD suite.

Runs against a REAL `nats-server -js` subprocess and a REAL ephemeral
Postgres cluster, not fakes — the task's own instruction is explicit that
a fake is not acceptable for the partition/slow-consumer scenarios, and a
fake could not fail the way a real broker or a real transactional outbox
actually fails. Both are started once per test session, on dynamically
chosen free ports (LAW 46: nothing here names a fixed host or port), and
torn down at the end of the session.

One asyncio event loop lives for the whole session (`run_async`) so that
connections opened by one fixture (the Bus, the asyncpg pool) stay valid
across every step in a scenario — pytest-bdd steps are plain sync
functions, so this is the run_until_complete bridge rather than pulling
in pytest-asyncio's fixture-generation machinery for a suite this shape.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import asyncpg
import pytest

from otto.spine.bus import Bus
from otto.spine.outbox import Outbox

_LOOP = asyncio.new_event_loop()
# asyncpg.create_pool() constructs its Pool object synchronously (the
# coroutine is only for the *connect* step) and that constructor calls
# asyncio.get_event_loop() — which raises if no loop has ever been set as
# current in this thread, even though the actual connect happens later
# inside run_async(). Setting it once here, not inside run_async, is what
# makes that synchronous construction step succeed.
asyncio.set_event_loop(_LOOP)


def run_async(coro):
    return _LOOP.run_until_complete(coro)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "cp1: CP1 spine-and-measurement BDD scenarios")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _find_nats_server_bin() -> str:
    env = os.environ.get("OTTO_NATS_SERVER_BIN")
    if env:
        return env
    found = shutil.which("nats-server")
    if found:
        return found
    # `go install` puts it under $GOBIN or $GOPATH/bin; neither is
    # guaranteed on PATH in every shell this suite might run from.
    candidates = [
        Path(os.environ.get("GOBIN", "")) / "nats-server",
        Path(os.environ.get("GOPATH", str(Path.home() / "go"))) / "bin" / "nats-server",
    ]
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    raise RuntimeError(
        "nats-server binary not found. Set OTTO_NATS_SERVER_BIN, or "
        "`go install github.com/nats-io/nats-server/v2@latest`."
    )


class NatsServerHandle:
    """Controls one real `nats-server -js` process. `stop()`/`start()`
    simulate the NATS-partition scenario literally — the process is
    unreachable, not merely slow — while keeping the same JetStream file
    store directory so a restart on the same port comes back with every
    stream and every message it held before, exactly like a network
    partition healing rather than a data loss event."""

    def __init__(self, store_dir: Path) -> None:
        self.bin = _find_nats_server_bin()
        self.store_dir = store_dir
        self.port = _free_port()
        self.host = "127.0.0.1"
        self._proc: subprocess.Popen | None = None

    @property
    def url(self) -> str:
        return f"nats://{self.host}:{self.port}"

    def start(self) -> None:
        if self._proc is not None:
            return
        log_path = self.store_dir / "nats-server.log"
        self._proc = subprocess.Popen(
            [
                self.bin,
                "-js",
                "-sd",
                str(self.store_dir),
                "-p",
                str(self.port),
                "-a",
                self.host,
            ],
            stdout=open(log_path, "a"),
            stderr=subprocess.STDOUT,
        )
        self._wait_ready()

    def _wait_ready(self, timeout_s: float = 10.0) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((self.host, self.port), timeout=0.5):
                    return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError(
            f"nats-server did not open {self.host}:{self.port} within {timeout_s}s"
        )

    def stop(self) -> None:
        if self._proc is None:
            return
        self._proc.terminate()
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=5)
        self._proc = None
        # Confirm the port is actually closed before the caller treats the
        # partition as "in effect" — TCP TIME_WAIT can otherwise let a new
        # connection attempt still succeed for a moment after terminate().
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((self.host, self.port), timeout=0.2):
                    time.sleep(0.05)
                    continue
            except OSError:
                return

    def restart(self) -> None:
        self.stop()
        self.start()


class PostgresHandle:
    """One ephemeral `initdb` + `pg_ctl` cluster, Unix-socket only (no TCP
    port to collide with anything else on the machine), torn down with
    `pg_ctl stop` at session end."""

    def __init__(self, data_dir: Path, socket_dir: Path) -> None:
        self.data_dir = data_dir
        self.socket_dir = socket_dir
        self.dbname = "otto_cp1"
        self.user = os.environ.get("USER", "otto")

    @property
    def dsn(self) -> str:
        return f"postgresql://{self.user}@/{self.dbname}?host={self.socket_dir}"

    def start(self) -> None:
        subprocess.run(
            [
                "initdb",
                "-D",
                str(self.data_dir),
                "-U",
                self.user,
                "-A",
                "trust",
                "--no-locale",
                "-E",
                "UTF8",
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "pg_ctl",
                "-D",
                str(self.data_dir),
                "-o",
                f"-k {self.socket_dir} -h ''",
                "-l",
                str(self.data_dir / "pg.log"),
                "-w",
                "start",
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["createdb", "-h", str(self.socket_dir), "-U", self.user, self.dbname],
            check=True,
            capture_output=True,
        )

    def stop(self) -> None:
        subprocess.run(
            ["pg_ctl", "-D", str(self.data_dir), "-m", "fast", "stop"],
            check=False,
            capture_output=True,
        )
        shutil.rmtree(self.socket_dir, ignore_errors=True)


@pytest.fixture(scope="session")
def nats_handle(tmp_path_factory: pytest.TempPathFactory):
    store_dir = tmp_path_factory.mktemp("otto-cp1-nats")
    handle = NatsServerHandle(store_dir)
    handle.start()
    yield handle
    handle.stop()


@pytest.fixture(scope="session")
def postgres_handle(tmp_path_factory: pytest.TempPathFactory):
    base = tmp_path_factory.mktemp("otto-cp1-pg")
    # The Unix-domain socket path has a kernel-enforced ceiling (103 bytes
    # on macOS/BSD) and pytest's own tmp_path_factory nests deep enough
    # under $TMPDIR (itself often a long per-process darwin path) to blow
    # past it — Postgres then refuses to start with "path too long", not a
    # config error. The socket dir alone is carved out under a short,
    # env-overridable base (LAW 46: no literal path, just a documented
    # default) while the data dir stays under pytest's own tmp tree, which
    # has no such length limit.
    socket_base = os.environ.get("OTTO_PG_SOCKET_BASE", tempfile.gettempdir())
    socket_dir = Path(tempfile.mkdtemp(prefix="otto-pg-sock-", dir=socket_base))
    handle = PostgresHandle(base / "data", socket_dir)
    handle.start()
    yield handle
    handle.stop()


@pytest.fixture(scope="session")
def pg_pool(postgres_handle: PostgresHandle):
    pool = run_async(asyncpg.create_pool(dsn=postgres_handle.dsn))
    yield pool
    run_async(pool.close())


@pytest.fixture
def bus(nats_handle: NatsServerHandle):
    """A fresh connected Bus per scenario, streams ensured. Fresh per
    scenario (not session) because the partition scenario deliberately
    kills the server mid-test and a stale client connection object from
    an earlier scenario should never leak into a later one."""
    b = run_async(Bus(servers=[nats_handle.url]).connect())
    run_async(b.ensure_streams())
    yield b
    run_async(b.close())


@pytest.fixture
def outbox(pg_pool: asyncpg.Pool):
    ob = Outbox(pg_pool)
    run_async(ob.ensure_schema())
    return ob


@pytest.fixture
def ctx() -> dict:
    """The one piece of mutable state Given/When/Then steps pass between
    each other within a scenario (pytest-bdd idiom, same shape as the CP2
    lane's `ctx` fixture)."""
    return {}
