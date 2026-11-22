"""``sector_flow.py --flows-only`` — 수급만 페이로드(BYO 지도 전용 수집의 짝). 합성 데모 DB 로(실데이터 없음).

지키는 것: 일봉·주식수·업종지수가 빈 sqlite(지도 전용 수집과 같은 모양)로도 페이로드가 서고 ``flows``·``detail``·
``n_by_sector`` 가 전체 페이로드와 같다 · 그 페이로드로 지은 수급 지도가 전체 페이로드의 지도와 같다 · 수익률·시총 칸은
없거나 ``None`` 이다(0 으로 채우지 않는다) · 수치표·TUI 는 "수익률 없음(지도 전용 수집)" 으로 끝낸다(빈 표를 조용히 내지 않는다).
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from sectorflow import demo
from sectorflow import flowmap as FM
from sectorflow.basis import FLOWS_ONLY_NOTICE
from sectorflow.tui import flow_app, ledger_view

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
#: 지도 전용 수집이 채우는 표 — 나머지는 비운다.
MAP_TABLES = {"stocks", "supply_demand"}


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], cwd=str(REPO), capture_output=True, text=True, timeout=300)


def map_only_db(full: Path, out: Path) -> Path:
    """데모 DB 에서 지도 전용 수집에 없는 표(일봉·주식수·업종지수·기업행동…)를 비운 사본."""
    shutil.copy(full, out)
    con = sqlite3.connect(out)
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        if t not in MAP_TABLES and not t.startswith("sqlite_"):
            con.execute(f"DELETE FROM {t}")
    con.commit()
    con.close()
    return out


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("flows-only")
    demo.build_db(root / "full.db")
    map_only_db(root / "full.db", root / "map.db")
    days = str(demo.REPORT_DAYS)
    p = _run(str(SCRIPTS / "sector_flow.py"), "--db", str(root / "full.db"), "--days", days,
             "--json", str(root / "full.json"))
    assert p.returncode == 0, p.stderr
    p = _run(str(SCRIPTS / "sector_flow.py"), "--db", str(root / "map.db"), "--days", days, "--flows-only",
             "--json", str(root / "flows.json"))
    assert p.returncode == 0, p.stderr
    return {"root": root, "full": json.loads((root / "full.json").read_text("utf-8")),
            "flows": json.loads((root / "flows.json").read_text("utf-8"))}


def test_flows_only_payload_builds_map(built):
    F, P = built["flows"], built["full"]
    assert F["flows_only"] is True and "flows_only" not in P
    assert set(F) == {"dates", "sectors", "markets", "n_by_sector", "flows", "detail", "detail_labels", "names",
                      "n_names", "flows_only"}
    for k in ("dates", "sectors", "markets", "n_by_sector", "flows", "detail", "detail_labels", "n_names"):
        assert F[k] == P[k], k
    assert FM.build_map_data(F)["views"] == FM.build_map_data(P)["views"], "수급만으로 지은 지도가 전체 지도와 같다"


def test_flows_only_names_keep_flows_and_drop_returns(built):
    F, P = built["flows"], built["full"]
    assert set(F["names"]) == set(P["names"])
    for code, n in F["names"].items():
        assert n["cap"] is None and {k: n[k] for k in ("name", "sector", "market")} == {
            k: P["names"][code][k] for k in ("name", "sector", "market")}
        assert set(n["win"]) == set(P["names"][code]["win"])
        for w, cell in n["win"].items():
            assert cell["ret"] is None, "수익률을 모르는 칸을 0 으로 채우지 않는다"
            assert {k: v for k, v in cell.items() if k != "ret"} == {
                k: v for k, v in P["names"][code]["win"][w].items() if k != "ret"}


def test_flows_only_refuses_the_viewer(built):
    p = _run(str(SCRIPTS / "sector_flow.py"), "--db", str(built["root"] / "map.db"), "--flows-only",
             "--html", str(built["root"] / "v.html"))
    assert p.returncode == 2 and "--flows-only" in p.stderr
    assert not (built["root"] / "v.html").exists()


def test_numbers_and_tui_end_with_the_flows_only_notice(built, tmp_path):
    folder = tmp_path / "byo"
    folder.mkdir()
    shutil.copy(built["root"] / "flows.json", folder / "payload.json")
    p = _run(str(SCRIPTS / "sector_numbers.py"), "--payload", str(folder / "payload.json"),
             "--html", str(folder / "numbers.html"))
    assert p.returncode == 1 and "수익률 없음(지도 전용 수집)" in p.stderr
    assert not (folder / "numbers.html").exists()
    for load in (flow_app.load, ledger_view.load):
        with pytest.raises(SystemExit) as e:
            load(str(folder))
        assert FLOWS_ONLY_NOTICE in str(e.value), load.__module__
