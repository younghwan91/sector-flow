"""``sector-flow-collect`` 이 처음 쓰는 사람에게 트레이스백 대신 갈 길을 말하는가(``docs/byo/quickstart.md`` 의 '막혔을 때').

지키는 것: 키가 없으면 ``--env-file`` 을 말하고 끝난다(키움을 부르지 않는다) · 호출 상한에 닿으면 1 로 끝나며
``--max-calls`` 와 ``--resume`` 을 말하고, 그때까지 끝낸 종목은 남아 다시 치면 이어 받는다.
"""

from __future__ import annotations

import sqlite3

import pytest

from sectorflow.collect import run as R
from tests.test_collect_daily import FakeServer, _patch


def test_missing_keys_say_env_file_instead_of_a_traceback(tmp_path, monkeypatch):
    for v in ("KIWOOM_APP_KEY", "KIWOOM_APP_SECRET"):
        monkeypatch.delenv(v, raising=False)
    with pytest.raises(SystemExit) as e:
        R.main(["--env-file", str(tmp_path / "없다.env"), "universe", "--db", str(tmp_path / "x.db")])
    assert "KIWOOM_APP_KEY" in str(e.value) and "--env-file" in str(e.value)


def test_call_budget_stops_with_a_hint_and_resume_finishes(tmp_path, monkeypatch, capsys):
    db = tmp_path / "m.db"
    fake = FakeServer()
    _patch(monkeypatch, fake, "2026-11-20 21:00")
    # 토큰 · ka10099 ×2 · 첫 종목 ka10059 1쪽 → 둘째 종목에서 상한
    assert R.main(["--max-calls", "4", "backfill", "--db", str(db), "--map-only", "--days", "30"]) == 1
    err = capsys.readouterr().err
    assert "--max-calls" in err and "--resume" in err
    con = sqlite3.connect(db)
    assert [r[0] for r in con.execute("SELECT code FROM collect_done")] == ["000120"]
    before = len(fake.seen)
    assert R.main(["backfill", "--db", str(db), "--map-only", "--days", "30", "--resume"]) == 0
    assert [b["stk_cd"] for t, b in fake.seen[before:] if t == "ka10059"] == ["044490"]
