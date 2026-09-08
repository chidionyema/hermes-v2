"""Test infrastructure for CP4: a real, disposable Postgres+pgvector
instance, not an in-memory fake (the drop-mid-write scenario needs a real
server to terminate a real backend against).

Docker is preferred (``OTTO_CP4_TEST_USE_DOCKER=1`` with a
``pgvector/pgvector`` container) when available; this Mac has no docker
daemon running, so the default path is a scratch Postgres built with
``initdb``/``pg_ctl`` from the brew-installed ``postgresql@17`` +
``pgvector`` formula (the same 0.8.6 pin the estate's own pgvector
pattern uses, idp platform/hindsight/postgres.yaml).

No path, host or port here is a hardcoded literal (LAW 46): the binary
directory is resolved from ``PATH`` (overridable via
``OTTO_CP4_TEST_PG_BINDIR``), the data/socket directories are freshly
created temp dirs, and the port is chosen by asking the OS for a free
one.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import psycopg
import pytest

from otto.memory import db as memdb
from otto.memory.config import MemoryConfig, load_config


def _pg_bindir() -> Path:
    override = os.environ.get("OTTO_CP4_TEST_PG_BINDIR")
    if override:
        return Path(override)
    initdb = shutil.which("initdb")
    if not initdb:
        pytest.skip(
            "no initdb on PATH; install postgresql (brew) to run cp4 SQL scenarios"
        )
    return Path(initdb).parent


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _ScratchPostgres:
    """Owns one initdb-created cluster for the whole test session."""

    def __init__(self) -> None:
        self.bindir = _pg_bindir()
        self.data_root = Path(tempfile.mkdtemp(prefix="otto-cp4-pgdata-"))
        # Unix-domain sockets have a ~103 byte path limit; /tmp keeps this
        # short regardless of how deep the scratchpad/worktree path is.
        self.sock_dir = Path(tempfile.mkdtemp(prefix="otto-cp4-sock-", dir="/tmp"))
        self.port = _free_port()
        self.superuser = f"otto_cp4_test_{uuid.uuid4().hex[:8]}"
        self._started = False

    def start(self) -> None:
        subprocess.run(
            [
                str(self.bindir / "initdb"),
                "-D",
                str(self.data_root / "data"),
                "-U",
                self.superuser,
                "-A",
                "trust",
                "--no-locale",
                "--encoding=UTF8",
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                str(self.bindir / "pg_ctl"),
                "-D",
                str(self.data_root / "data"),
                "-o",
                f"-p {self.port} -k {self.sock_dir} -h ''",
                "-l",
                str(self.data_root / "postgres.log"),
                "start",
                "-w",
            ],
            check=True,
            capture_output=True,
        )
        self._started = True
        self._wait_ready()

    def _wait_ready(self, timeout_s: float = 15.0) -> None:
        deadline = time.time() + timeout_s
        last_err = None
        while time.time() < deadline:
            try:
                conn = psycopg.connect(
                    f"postgresql://{self.superuser}@/postgres"
                    f"?host={self.sock_dir}&port={self.port}"
                )
                conn.close()
                return
            except Exception as exc:  # noqa: BLE001 - retry loop
                last_err = exc
                time.sleep(0.2)
        raise RuntimeError(f"scratch postgres never became ready: {last_err}")

    def dsn(self, dbname: str) -> str:
        return (
            f"postgresql://{self.superuser}@/{dbname}"
            f"?host={self.sock_dir}&port={self.port}"
        )

    def create_database(self, dbname: str) -> None:
        conn = psycopg.connect(self.dsn("postgres"), autocommit=True)
        try:
            conn.execute(f'CREATE DATABASE "{dbname}"')
        finally:
            conn.close()

    def drop_database(self, dbname: str) -> None:
        conn = psycopg.connect(self.dsn("postgres"), autocommit=True)
        try:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
        finally:
            conn.close()

    def stop(self) -> None:
        if not self._started:
            return
        subprocess.run(
            [
                str(self.bindir / "pg_ctl"),
                "-D",
                str(self.data_root / "data"),
                "-m",
                "fast",
                "stop",
            ],
            check=False,  # best-effort teardown; nothing left to do if this fails
            capture_output=True,
        )
        shutil.rmtree(self.sock_dir, ignore_errors=True)
        shutil.rmtree(self.data_root, ignore_errors=True)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "cp4: CP4 memory-engine scenario (crew#768)")


@pytest.fixture(scope="session")
def pg_cluster():
    cluster = _ScratchPostgres()
    cluster.start()
    yield cluster
    cluster.stop()


@pytest.fixture()
def memory_config() -> MemoryConfig:
    # 8-dim embeddings in tests: fast, and proves the schema's dimension
    # is genuinely config-driven, not hardcoded to a real provider's size.
    os.environ["OTTO_MEMORY_EMBEDDING_DIM"] = "8"
    os.environ["OTTO_MEMORY_EMBEDDING_DEADLINE_S"] = "0.3"
    return load_config()


@pytest.fixture()
def db_conn(pg_cluster, memory_config):
    """A fresh database per test: real Postgres, real pgvector, real
    migrations, isolated so tests never see each other's rows."""
    dbname = f"otto_cp4_{uuid.uuid4().hex[:12]}"
    pg_cluster.create_database(dbname)
    os.environ["OTTO_MEMORY_DATABASE_URL"] = pg_cluster.dsn(dbname)
    conn = memdb.connect(memory_config)
    memdb.apply_migrations(conn, memory_config)
    yield conn
    try:
        conn.close()
    except psycopg.Error:
        pass  # already closed/terminated by a scenario (e.g. the network-drop test)
    pg_cluster.drop_database(dbname)


@pytest.fixture()
def second_conn(pg_cluster, db_conn, memory_config):
    """A second connection to the *same* database as ``db_conn`` - used
    to terminate ``db_conn``'s backend mid-write from the outside, the
    only way to genuinely prove a dropped connection leaves no partial
    row (a mocked driver could not demonstrate this)."""
    dsn = os.environ["OTTO_MEMORY_DATABASE_URL"]
    conn = psycopg.connect(dsn, autocommit=True)
    yield conn
    conn.close()
