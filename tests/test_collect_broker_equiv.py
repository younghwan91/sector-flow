"""``sector-flow-collect equiv`` — 증권사 어댑터 sqlite 를 키움 sqlite 와 맞대는 대조 명령(Phase 6b Task 62). 네트워크 없음.

지키는 것: 같은 수급이면 ``일치``(종료 0) · 섹터 합이 0.5% 를 넘게 갈리면 ``불일치``(1) · 겹침이 없거나 거래소 기준이 다르면
``판정 불가``(2) · 칸 대조는 그 어댑터가 주는 주체만(국가 없는 한국투자는 국가를 안 센다) · ``--sectors`` 로 좁힌다 ·
출처가 섞인 sqlite 는 거절 · **출력에 실데이터 숫자가 없다**(일치 여부·칸 수·최대 상대오차뿐 — 이슈에 붙여도 안전).
"""

from __future__ import annotations

import json

import pytest

from sectorflow.collect import run as R
from sectorflow.collect import write
from sectorflow.collect.equiv import BROKER_EQUIV_KEYS
from sectorflow.storage import INVESTOR_COLUMNS

DAYS = [f"2026-11-{d:02d}" for d in (2, 3, 4, 5, 6, 9, 10, 11, 12, 13)]
STOCKS = [("000120", "거래소", "운송/창고"), ("044490", "코스닥", "운송/창고"), ("035900", "코스닥", "오락/문화")]
#: 순매매 수량(주) — 종가 10만원이면 1만 주 = 10억. 섹터 칸이 분모 하한(1억)을 넘게 크게 잡는다.
QTY = {"individual": -30000, "foreign_": 10000, "institution": 15000, "fnnc_invt": 5000, "insrnc": 0, "invtrt": 4000,
       "bank": 0, "penfnd_etc": 6000, "samo_fund": 0, "natn": 0, "etc_corp": 5000}


def _db(path, source, *, tweak=None, natn=True):
    con = write.open_db(path)
    con.executemany("INSERT INTO stocks VALUES (?,?,?,?,?,?)",
                    [(c, c, m, s, "A", "kiwoom" if source == "kiwoom" else None) for c, m, s in STOCKS])
    rows = []
    for c, _m, _s in STOCKS:
        for d in DAYS:
            q = dict(QTY)
            if not natn:
                q["natn"] = None
            if tweak:
                q = tweak(c, d, q)
            rows.append((c, d, 100_000, None, 1, *[q[k] for k in INVESTOR_COLUMNS], source))
    write.upsert_supply_demand(con, rows)
    con.commit()
    con.close()
    return str(path)


def _run(capsys, *argv):
    rc = R.main(["equiv", *argv])
    return rc, json.loads(capsys.readouterr().out)


def test_identical_flows_match(tmp_path, capsys):
    k = _db(tmp_path / "k.db", "kiwoom")
    b = _db(tmp_path / "kis.db", "kis", natn=False)
    rc, out = _run(capsys, "--broker", "kis", "--db", b, "--against", k)
    assert rc == 0 and out["verdict"] == "일치" and out["max_rel_err"] == 0.0
    assert "natn" not in out["actors"] and out["missing_actors"] == ["natn"]
    assert out["cells"] == {"compared": 3 * 10 * 10, "equal": 300, "differ": 0}, "주는 주체 10칸만 센다"
    assert (out["dates"], out["codes"]) == (10, 3) and out["sector_cells"] > 0


def test_output_carries_no_real_numbers(tmp_path, capsys):
    k = _db(tmp_path / "k.db", "kiwoom")
    b = _db(tmp_path / "kis.db", "kis", natn=False,
            tweak=lambda c, d, q: {**q, "institution": q["institution"] * 2} if (c, d) == ("000120", DAYS[-1]) else q)
    rc, out = _run(capsys, "--broker", "kis", "--db", b, "--against", k)
    assert tuple(out) == BROKER_EQUIV_KEYS
    text = json.dumps(out, ensure_ascii=False)
    for secret in ("000120", "044490", "운송/창고", "2026-11", "15000", "30000", "100000", "1.5"):
        assert secret not in text, f"출력에 실데이터 모양({secret})이 실렸다"
    assert rc == 1 and out["verdict"] == "불일치" and out["cells"]["differ"] == 1
    assert out["max_rel_err"] > 0.005


def test_small_differences_stay_under_the_bar(tmp_path, capsys):
    k = _db(tmp_path / "k.db", "kiwoom")
    b = _db(tmp_path / "kis.db", "kis", natn=False,
            tweak=lambda c, d, q: {**q, "individual": q["individual"] + 1} if c == "000120" else q)
    rc, out = _run(capsys, "--broker", "kis", "--db", b, "--against", k)
    assert rc == 0 and out["verdict"] == "일치" and out["cells"]["differ"] == 10 and 0 < out["max_rel_err"] <= 0.005


def test_sectors_narrow_the_comparison(tmp_path, capsys):
    k = _db(tmp_path / "k.db", "kiwoom")
    b = _db(tmp_path / "kis.db", "kis", natn=False,
            tweak=lambda c, d, q: {**q, "institution": 0} if c == "035900" else q)
    assert _run(capsys, "--broker", "kis", "--db", b, "--against", k)[0] == 1
    rc, out = _run(capsys, "--broker", "kis", "--db", b, "--against", k, "--sectors", "운송/창고")
    assert rc == 0 and out["codes"] == 2


def test_no_overlap_or_another_venue_cannot_be_judged(tmp_path, capsys):
    k = _db(tmp_path / "k.db", "kiwoom")
    t = _db(tmp_path / "t.db", "toss", natn=False)
    rc, out = _run(capsys, "--broker", "toss", "--db", t, "--against", k)
    assert rc == 2 and out["verdict"] == "판정 불가" and "거래소 기준" in out["note"]
    b = _db(tmp_path / "kis.db", "kis", natn=False)
    rc, out = _run(capsys, "--broker", "kis", "--db", b, "--against", k, "--sectors", "없는섹터")
    assert rc == 2 and out["cells"]["compared"] == 0 and "겹치는" in out["note"]


def test_wrong_sources_are_refused(tmp_path, capsys):
    k = _db(tmp_path / "k.db", "kiwoom")
    b = _db(tmp_path / "kis.db", "kis", natn=False)
    with pytest.raises(SystemExit, match="--broker ls"):
        R.main(["equiv", "--broker", "ls", "--db", b, "--against", k])
    with pytest.raises(SystemExit, match="키움으로 받은"):
        R.main(["equiv", "--broker", "kis", "--db", b, "--against", b])
    with pytest.raises(SystemExit):
        R.main(["equiv", "--broker", "kiwoom", "--db", k, "--against", k])
