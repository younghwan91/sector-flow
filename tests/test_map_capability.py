"""증권사 능력에 맞춘 지도(Phase 6b Task 61) — 없는 주체는 그리지 않고, 잔여는 4주체가 다 있을 때만, 미검증이면 배지.

지키는 것: 페이로드 ``capability``(주체·거래소·검증 여부·섹터 출처)가 BYO sqlite 의 어댑터에서 온다 · 주지 않는 주체는 수급만 페이로드에서
빠진다(0 으로 남기면 '순매매 없음') · 지도는 그 주체를 노드·간선·머리글·시간 흐름 어디에도 그리지 않는다 · 잔여 노드는 4주체가 다
있을 때만(하나라도 없으면 −Σ 가 그 주체를 삼킨다) · "이 증권사는 X 를 주지 않는다" 표지 · 미검증 어댑터 배지 · 배치(능력 없음)는 그대로.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from sectorflow import flowmap as FM
from sectorflow.collect import write

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
DATES = [f"2026-11-{d:02d}" for d in (16, 17, 18, 19, 20)]


def _payload(cap=None, etc=True):
    def arr(v):
        return [float(v)] * len(DATES)
    flows = {"거래소": {
        "가상전자": {"indiv": arr(-30), "forgn": arr(10), "inst": arr(15), **({"etc": arr(4)} if etc else {}), "tv": arr(1)},
        "가상화학": {"indiv": arr(8), "forgn": arr(-6), "inst": arr(-5), **({"etc": arr(1)} if etc else {}), "tv": arr(1)},
    }}
    P = {"dates": DATES, "sectors": ["가상전자", "가상화학"], "markets": ["거래소"], "flows": flows, "detail": {},
         "detail_labels": {}}
    if cap is not None:
        P["capability"] = cap
    return P


THREE = {"brokers": ["db"], "broker_labels": ["DB증권"], "actors": ["indiv", "forgn", "inst"], "detail": [],
         "venue": "KRX", "verified": False, "sector_source": None}


def _all_nodes(D):
    out = set()
    for by_win in D["views"].values():
        for v in by_win.values():
            ex = v["exchange"]
            out |= set(ex["left"]) | set(ex["right"])
            for r in ex["sectors"]:
                out |= set(r["give"]) | set(r["take"])
            out |= set(v["actors"])
    for fr in D["timelapse"].values():
        for f in fr["frames"]:
            out |= set(f["left"]) | set(f["right"])
    for by_actor in D["stream"]["views"].values():
        out |= set(by_actor)
    return out


def test_missing_actor_not_drawn():
    D = FM.build_map_data(_payload(THREE, etc=False))
    nodes = _all_nodes(D)
    assert "etc" not in nodes and FM.RESID not in nodes
    assert D["actors"] == ["indiv", "forgn", "inst"] and D["missing"] == ["기타법인"]
    assert all("기타법인" not in f and "잔여" not in f for v in D["views"]["전체"].values() for f in v["headline"]["facts"])
    assert D["cap_note"] == "DB증권 어댑터는 기타법인을 주지 않는다 — 그 주체와 잔여는 그리지 않는다(0 이 아니라 모름)"


def test_resid_only_with_all_four():
    full = FM.window_sums(_payload(), ["거래소"], 0, 4)
    assert full["가상전자"][FM.RESID] == -(-30 + 10 + 15 + 4) * 5
    three = FM.window_sums(_payload(THREE, etc=False), ["거래소"], 0, 4)
    assert FM.RESID not in three["가상전자"], "기타법인이 없으면 −(개인+외국인+기관)은 잔여가 아니다 — 기타법인을 삼킨다"
    four = dict(THREE, actors=["indiv", "forgn", "inst", "etc"])
    assert FM.RESID in FM.window_sums(_payload(four), ["거래소"], 0, 4)["가상전자"]
    assert FM.build_map_data(_payload(four))["actors"][-1] == FM.RESID


def test_batch_payload_draws_everything_without_a_badge():
    D = FM.build_map_data(_payload())
    assert D["actors"] == [*FM.ACTORS, FM.RESID] and D["missing"] == [] and D["cap_note"] is None
    assert D["verified"] is True and D["broker"] is None


def _db_sqlite(path: Path, source: str) -> Path:
    con = write.open_db(path)
    con.executemany("INSERT INTO stocks VALUES (?,?,?,?,?,?)",
                    [("000120", "CJ대한통운", "거래소", "운송/창고", "A", "kiwoom"),
                     ("044490", "태웅로직스", "코스닥", "운송/창고", "A", "kiwoom")])
    three = source in ("db", "nh")
    rows = [(c, d, 1000, None, 100, -30, 10, 15, *([None] * 7 if three else [5, 0, 4, 0, 6, 0, None]),
             None if three else 5, source) for c in ("000120", "044490") for d in DATES]
    con.executemany(f"INSERT INTO supply_demand ({','.join(write._SD_COLS)}) VALUES ({','.join('?' * 17)})", rows)
    con.commit()
    con.close()
    return path


def _build(db: Path, tmp: Path) -> "tuple[dict, str]":
    out, html = tmp / "p.json", tmp / "m.html"
    for args in ((SCRIPTS / "sector_flow.py", "--db", db, "--days", "5", "--flows-only", "--json", out),
                 (SCRIPTS / "sector_map.py", "--payload", out, "--html", html)):
        p = subprocess.run([sys.executable, *map(str, args)], cwd=REPO, capture_output=True, text=True, timeout=120)
        assert p.returncode == 0, p.stderr
    return json.loads(out.read_text("utf-8")), html.read_text("utf-8")


def test_db_sqlite_payload_carries_the_capability_and_drops_what_it_lacks(tmp_path):
    P, html = _build(_db_sqlite(tmp_path / "d.db", "db"), tmp_path)
    cap = P["capability"]
    assert (cap["brokers"], cap["actors"], cap["detail"], cap["venue"], cap["verified"]) == (
        ["db"], ["indiv", "forgn", "inst"], [], "KRX", False)
    assert cap["sector_source"] == {"kiwoom": 2}
    f = P["flows"]["거래소"]["운송/창고"]
    assert "etc" not in f and set(f) == {"indiv", "forgn", "inst", "tv"}, "주지 않는 주체를 0 배열로 남기지 않는다"
    assert P["detail_labels"] == {} and P["detail"]["거래소"]["운송/창고"] == {}
    cell = P["names"]["000120"]["win"]["5"]
    assert cell["etc"] is None and cell["invtrt"] is None and cell["inst"] is not None
    D = json.loads(html.split("const D = ", 1)[1].split(";\n", 1)[0])
    assert D["actors"] == ["indiv", "forgn", "inst"] and D["verified"] is False
    assert "DB증권 API로 각자 수집" in html and 'id="unverified"' in html and "미검증 어댑터" in html


def test_kiwoom_sqlite_is_verified_and_full(tmp_path):
    P, html = _build(_db_sqlite(tmp_path / "k.db", "kiwoom"), tmp_path)
    assert P["capability"]["verified"] is True and P["capability"]["actors"] == ["indiv", "forgn", "inst", "etc"]
    D = json.loads(html.split("const D = ", 1)[1].split(";\n", 1)[0])
    assert D["verified"] is True and D["actors"][-1] == FM.RESID and D["cap_note"] is None


def test_mixed_brokers_keep_only_what_both_give(tmp_path):
    db = _db_sqlite(tmp_path / "x.db", "kiwoom")
    con = sqlite3.connect(db)
    con.execute("UPDATE supply_demand SET source = 'db', etc_corp = NULL, invtrt = NULL WHERE code = '044490'")
    con.commit()
    con.close()
    P, _ = _build(db, tmp_path)
    assert P["capability"]["brokers"] == ["kiwoom", "db"] and P["capability"]["verified"] is False
    assert P["capability"]["actors"] == ["indiv", "forgn", "inst"], "한쪽(DB)이 안 주는 기타법인은 뺀다"
    assert P["capability"]["detail"] == []
