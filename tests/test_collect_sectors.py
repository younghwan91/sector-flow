"""섹터를 증권사와 떼어 한 벌로 — ``stocks.sector_source`` 와 지도 표지(Phase 6b Task 56).

지키는 것: 섹터마다 출처가 남는다(키움 업종명·증권사 업종·``csv:`` 사용자 표) · 섹터를 안 주는 목록이 저장된 다른 출처의 섹터를
지우지 않는다 · 사용자 표는 증권사 목록이 덮지 않는다 · 옛 BYO sqlite(판 3)는 키움 출처로 채워진다 · 키움 업종명 체계의 덮어쓰기
표(``sector_override``)를 다른 분류가 섞인 마스터에 넣지 않는다 · 수급만 페이로드 → 지도 머리에 섹터 출처가 적힌다.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from sectorflow import flowmap as FM
from sectorflow.basis import sector_source_label
from sectorflow.collect import run as R
from sectorflow.collect import write
from sectorflow.storage import SCHEMA

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"


@pytest.mark.parametrize("old, new, src, want", [
    (None, "운송/창고", "kiwoom", ("운송/창고", "kiwoom")),                 # 처음 — 들어온 것
    (("운송/창고", "kiwoom"), "", "kis", ("운송/창고", "kiwoom")),          # 섹터 없는 목록은 다른 출처를 안 지운다
    (("운송/창고", "kiwoom"), "운수창고업", "ls", ("운수창고업", "ls")),     # 섹터를 주면 그것
    (("운송/창고", "kiwoom"), "", "kiwoom", ("", None)),                    # 같은 출처가 비웠으면 비운다(옛 동작)
    (("내 운송", "csv:내표"), "운송/창고", "kiwoom", ("내 운송", "csv:내표")),  # 사용자 표는 덮지 않는다
    (("", None), "", "kis", ("", None)),
])
def test_resolve_sector_keeps_one_set(old, new, src, want):
    assert write.resolve_sector(old, new, src) == want


def _stock(code, sector, src, name="x", market="거래소"):
    return {"code": code, "name": name, "market": market, "sector": sector, "kind": "A", "sector_source": src}


def test_listing_without_sectors_does_not_wipe_stored_ones(tmp_path):
    con = write.open_db(tmp_path / "s.db")
    write.upsert_stocks(con, [_stock("000120", "운송/창고", "kiwoom", name="옛이름")])
    write.upsert_stocks(con, [_stock("000120", "", None, name="새이름")])          # 섹터를 안 주는 증권사의 목록
    assert tuple(con.execute("SELECT name, sector, sector_source FROM stocks").fetchone()) == (
        "새이름", "운송/창고", "kiwoom")


def test_user_table_wins_over_later_listings(tmp_path):
    con = write.open_db(tmp_path / "s.db")
    write.upsert_stocks(con, [_stock("000120", "운송/창고", "kiwoom"), _stock("044490", "운송/창고", "kiwoom")])
    csv = tmp_path / "내 표.csv"
    csv.write_text("﻿code,sector\n120,물류\n999999,없는종목\n", "utf-8")    # BOM·앞 0 빠진 코드
    con.commit()
    con.close()
    assert R.main(["sectors", "--db", str(tmp_path / "s.db"), "--csv", str(csv)]) == 0
    con = write.open_db(tmp_path / "s.db")
    write.upsert_stocks(con, [_stock("000120", "운송/창고", "kiwoom")])          # 키움 목록을 다시 받아도
    got = dict(con.execute("SELECT code, sector || '|' || sector_source FROM stocks").fetchall())
    assert got == {"000120": "물류|csv:내 표", "044490": "운송/창고|kiwoom"}


def test_sectors_without_a_listing_fails_loudly(tmp_path, capsys):
    csv = tmp_path / "t.csv"
    csv.write_text("code,sector\n000120,물류\n", "utf-8")
    assert R.main(["sectors", "--db", str(tmp_path / "e.db"), "--csv", str(csv)]) == 1
    assert "universe" in capsys.readouterr().err
    bad = tmp_path / "b.csv"
    bad.write_text("종목,업종\n000120,물류\n", "utf-8")
    with pytest.raises(SystemExit, match="code,sector"):
        R.main(["sectors", "--db", str(tmp_path / "e.db"), "--csv", str(bad)])


def test_open_db_marks_old_rows_as_kiwoom(tmp_path):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO stocks VALUES ('000120','CJ대한통운','거래소','운송/창고','A')")
    con.execute("INSERT INTO stocks VALUES ('900000','해외','코스닥','','A')")
    con.execute("PRAGMA user_version = 3")
    con.commit()
    con.close()
    con = write.open_db(db)
    assert dict(con.execute("SELECT code, sector_source FROM stocks").fetchall()) == {"000120": "kiwoom",
                                                                                     "900000": None}
    assert con.execute("PRAGMA user_version").fetchone()[0] == write.SCHEMA_VERSION == 4


def test_sector_source_label_names_each_source_and_flags_a_mix():
    assert sector_source_label(None) is None and sector_source_label({}) is None
    assert sector_source_label({"kiwoom": 96}) == "섹터: 키움 업종명"
    assert sector_source_label({"csv:내 표": 3}) == "섹터: 사용자 표(내 표)"
    mixed = sector_source_label({"kiwoom": 90, "ls": 1200, "": 4})
    assert mixed == "섹터: LS증권 업종 1,200종목 · 키움 업종명 90종목 · 출처 미기록 4종목 (분류 체계 섞임)"


def _byo_db(path: Path, sectors: dict[str, tuple[str, "str | None"]]) -> Path:
    con = write.open_db(path)
    con.executemany("INSERT INTO stocks VALUES (?,?,?,?,?,?)",
                    [(c, c, "거래소", sec, "A", src) for c, (sec, src) in sectors.items()])
    days = [f"2026-11-{d:02d}" for d in (16, 17, 18, 19, 20)]
    con.executemany(f"INSERT INTO supply_demand ({','.join(write._SD_COLS)}) VALUES ({','.join('?' * 17)})",
                    [(c, d, 1000, 0.0, 100, -30, 10, 15, 5, 0, 4, 0, 6, 0, 0, 5, "kiwoom")
                     for c in sectors for d in days])
    con.commit()
    con.close()
    return path


def _flows(db: Path, out: Path) -> dict:
    p = subprocess.run([sys.executable, str(SCRIPTS / "sector_flow.py"), "--db", str(db), "--days", "5",
                        "--flows-only", "--json", str(out)], cwd=REPO, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr
    return json.loads(out.read_text("utf-8"))


def test_sector_source_labelled(tmp_path):
    """BYO sqlite → 수급만 페이로드 → 지도: 섹터 출처가 페이로드·지도 데이터·map.html 머리에 실린다."""
    db = _byo_db(tmp_path / "b.db", {"000120": ("운송/창고", "kiwoom"), "044490": ("물류", "csv:내 표")})
    P = _flows(db, tmp_path / "p.json")
    assert P["sector_source"] == {"csv:내 표": 1, "kiwoom": 1}
    data = FM.build_map_data(P)
    assert data["sector_source"] == "섹터: 사용자 표(내 표) 1종목 · 키움 업종명 1종목 (분류 체계 섞임)"
    html = tmp_path / "map.html"
    p = subprocess.run([sys.executable, str(SCRIPTS / "sector_map.py"), "--payload", str(tmp_path / "p.json"),
                        "--html", str(html)], cwd=REPO, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr
    text = html.read_text("utf-8")
    assert data["sector_source"] in text and 'id="secsrc"' in text and 'id="cardsecsrc"' in text


def test_kiwoom_override_table_stays_out_of_other_taxonomies(tmp_path):
    """덮어쓰기 표(기업은행 → 금융)는 키움 업종명 체계다 — 사용자 표가 섞인 마스터에서는 빈 섹터를 (미분류)로 둔다."""
    only_kiwoom = _flows(_byo_db(tmp_path / "k.db", {"000120": ("운송/창고", "kiwoom"), "024110": ("", None)}),
                         tmp_path / "k.json")
    assert only_kiwoom["names"]["024110"]["sector"] == "금융"
    mixed = _flows(_byo_db(tmp_path / "m.db", {"000120": ("물류", "csv:내 표"), "024110": ("", None)}),
                   tmp_path / "m.json")
    assert mixed["names"]["024110"]["sector"] == "(미분류)"


def test_kiwoom_listing_records_its_source(tmp_path, monkeypatch):
    from sectorflow.collect import kiwoom as K
    from tests.test_collect_universe import FakeList
    monkeypatch.setattr(R, "_client", lambda args: K.KiwoomRest("k", "s", transport=FakeList(), rate=None,
                                                                 sleep=lambda s: None))
    assert R.main(["universe", "--db", str(tmp_path / "u.db")]) == 0
    con = sqlite3.connect(tmp_path / "u.db")
    assert {r[0] for r in con.execute("SELECT DISTINCT sector_source FROM stocks")} == {"kiwoom"}


def test_sector_csv_reads_the_krx_download_headers_in_cp949(tmp_path):
    f = tmp_path / "krx.csv"
    f.write_bytes("종목코드,종목명,시장구분,업종명\n000120,CJ대한통운,KOSPI,운수창고업\n44490,태웅로직스,KOSDAQ,운송\n"
                  .encode("cp949"))
    assert R.read_sector_csv(str(f)) == {"000120": "운수창고업", "044490": "운송"}
