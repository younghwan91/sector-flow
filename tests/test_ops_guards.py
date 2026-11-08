"""운영 방어선 — DB 접속 상한과 크론 로그의 잡음.

진짜 Postgres 없이 본다: ``psycopg2`` 자리에 받은 인자와 실행한 SQL 을 적어 두는 가짜를 끼운다.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import types
import warnings
from pathlib import Path

import pandas as pd
import pytest

from sectorflow import storage

REPO = Path(__file__).resolve().parent.parent


class _FakePg:
    def __init__(self):
        self.kwargs: dict = {}
        self.sql: list[str] = []
        self.committed = False

    def connect(self, dsn, **kwargs):
        self.kwargs = kwargs
        fake = self

        class Cur:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, *a):
                fake.sql.append(sql)

        class Con:
            def cursor(self):
                return Cur()

            def commit(self):
                fake.committed = bool(fake.sql)

        return Con()


@pytest.fixture
def fake_pg(monkeypatch):
    fake = _FakePg()
    mod = types.ModuleType("psycopg2")
    mod.connect = fake.connect
    monkeypatch.setitem(sys.modules, "psycopg2", mod)
    return fake


def test_pg_connection_has_a_connect_timeout(fake_pg):
    """DB 서버가 패킷을 삼키면 libpq 기본은 무한 대기다 — 배치가 알림 없이 매달린다."""
    storage.connect("postgresql://u@127.0.0.1/x")
    assert 0 < fake_pg.kwargs.get("connect_timeout", 0) <= 60


def test_pg_session_has_a_statement_timeout(fake_pg):
    """락 대기·폭주 쿼리는 클라이언트를 죽여도 서버에서 계속 돈다 — 세션 상한이 DB 쪽에서 끊는다."""
    storage.connect("postgresql://u@127.0.0.1/x")
    sets = [q for q in fake_pg.sql if "statement_timeout" in q]
    assert sets, fake_pg.sql
    ms = int(sets[0].rsplit("=", 1)[1])
    assert 60_000 <= ms <= 30 * 60_000
    assert fake_pg.committed, "SET 뒤 commit 이 없으면 첫 rollback 에 상한이 풀린다"


def test_sqlite_path_is_untouched(tmp_path):
    con = storage.connect(str(tmp_path / "x.db"))
    assert isinstance(con, sqlite3.Connection)
    con.close()


class _DbapiNotSqlite:
    """sqlite3 연결이 아닌 DBAPI — pandas 가 psycopg2 에 내는 경고를 그대로 낸다."""

    def __init__(self):
        self._c = sqlite3.connect(":memory:")

    def cursor(self):
        return self._c.cursor()

    def commit(self):
        pass

    def close(self):
        self._c.close()


@pytest.mark.parametrize("script", ["sector_flow", "completeness_gate", "verify_report"])
def test_pandas_dbapi_warning_is_silenced_in_report_scripts(script):
    """read_sql 마다 같은 UserWarning 이 크론 로그를 덮어 진짜 경고를 묻는다(cron.log 1,491줄)."""
    con = _DbapiNotSqlite()
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            spec = importlib.util.spec_from_file_location(f"_s_{script}", REPO / "scripts" / f"{script}.py")
            spec.loader.exec_module(importlib.util.module_from_spec(spec))
            pd.read_sql_query("SELECT 1 AS x", con)
            warnings.warn("다른 UserWarning 은 그대로 보인다", UserWarning, stacklevel=1)
    finally:
        con.close()
    msgs = [str(w.message) for w in caught if issubclass(w.category, UserWarning)]
    assert not [m for m in msgs if "SQLAlchemy" in m], msgs
    assert "다른 UserWarning 은 그대로 보인다" in msgs, "경고를 통째로 껐다"
