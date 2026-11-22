"""``sector-flow-byo map`` — sqlite 에서 수급 지도까지 한 명령. 합성 데모 DB 를 지도 전용 수집 모양으로 비워서(실데이터 없음).

지키는 것: 일봉·주식수·업종지수가 빈 sqlite 로 ``payload.json``(수급만) → ``map.html`` 이 끝까지 선다 · 지도 데이터는
같은 페이로드로 :func:`sectorflow.flowmap.build_map_data` 를 부른 것과 같다 · 카드는 생성기와 Playwright 가 다 있을 때만
부르고, 없거나 실패해도 지도는 그대로(종료 0) · 수급만이 아닌 리포트 폴더는 덮지 않는다.
"""

from __future__ import annotations

import json
import re

import pytest

from sectorflow import byo, demo
from sectorflow import flowmap as FM
from tests.test_flows_only import map_only_db


@pytest.fixture(scope="module")
def map_db(tmp_path_factory):
    root = tmp_path_factory.mktemp("byo-map")
    demo.build_db(root / "full.db")
    return map_only_db(root / "full.db", root / "map.db")


def _map_data(html: str) -> dict:
    m = re.search(r"const D = (\{.*?\});\n", html, re.S)
    assert m, "지도 데이터 줄이 없다"
    return json.loads(m.group(1))


def test_byo_map_end_to_end(map_db, tmp_path, capsys):
    out = tmp_path / "map"
    assert byo.main(["map", "--db", str(map_db), "--out", str(out), "--no-card"]) == 0
    P = json.loads((out / "payload.json").read_text("utf-8"))
    assert P["flows_only"] is True and len(P["dates"]) == demo.REPORT_DAYS
    D = _map_data((out / "map.html").read_text("utf-8"))
    assert D == json.loads(json.dumps(FM.build_map_data(P), ensure_ascii=False)), "지도 = 같은 페이로드의 build_map_data"
    assert D["asof"] == P["dates"][-1] and D["windows"] == list(FM.MAP_WINDOWS)
    said = capsys.readouterr().out
    assert (out / "map.html").as_uri() in said


def test_byo_map_respects_days_and_asof(map_db, tmp_path):
    out = tmp_path / "m"
    full = json.loads(json.dumps(FM.MAP_WINDOWS))
    assert byo.main(["map", "--db", str(map_db), "--out", str(out), "--days", "30", "--no-card"]) == 0
    P = json.loads((out / "payload.json").read_text("utf-8"))
    assert len(P["dates"]) == 30
    assert _map_data((out / "map.html").read_text("utf-8"))["windows"] == [w for w in full if w <= 30]
    asof = P["dates"][-3]
    assert byo.main(["map", "--db", str(map_db), "--out", str(out), "--asof", asof, "--no-card"]) == 0
    assert json.loads((out / "payload.json").read_text("utf-8"))["dates"][-1] == asof


def test_card_runs_only_with_generator_and_playwright(map_db, tmp_path, monkeypatch, capsys):
    gen = tmp_path / "map_card.py"
    gen.write_text("import sys, pathlib\n"
                   "d = pathlib.Path(sys.argv[sys.argv.index('--dir') + 1])\n"
                   "(d / 'map.png').write_bytes(b'png')\n", "utf-8")
    monkeypatch.setattr(byo, "_script", lambda name: gen if name == byo.CARD_SCRIPT else byo.script_path(name))
    out = tmp_path / "with"
    monkeypatch.setattr(byo, "_has_playwright", lambda: True)
    assert byo.main(["map", "--db", str(map_db), "--out", str(out)]) == 0
    assert (out / "map.png").read_bytes() == b"png" and "카드: " + str(out / "map.png") in capsys.readouterr().out
    out2 = tmp_path / "without"
    monkeypatch.setattr(byo, "_has_playwright", lambda: False)
    assert byo.main(["map", "--db", str(map_db), "--out", str(out2)]) == 0
    assert not (out2 / "map.png").exists() and "Playwright 가 없다" in capsys.readouterr().out
    gen.write_text("raise SystemExit(3)\n", "utf-8")
    monkeypatch.setattr(byo, "_has_playwright", lambda: True)
    assert byo.main(["map", "--db", str(map_db), "--out", str(out2)]) == 0, "카드 실패는 지도를 막지 않는다"
    assert "3 로 끝났다" in capsys.readouterr().out and (out2 / "map.html").exists()


def test_card_skipped_when_the_install_has_no_generator(monkeypatch):
    monkeypatch.setattr(byo, "_script", lambda name: None)
    monkeypatch.setattr(byo, "_has_playwright", lambda: True)
    assert "카드 생성기가 없다" in byo.card(byo.Path("."))


def test_byo_map_does_not_overwrite_a_report_folder(map_db, tmp_path):
    out = tmp_path / "report"
    out.mkdir()
    (out / "payload.json").write_text(json.dumps({"dates": ["2026-11-20"], "ret": {}}), "utf-8")
    with pytest.raises(SystemExit, match="덮지 않는다"):
        byo.main(["map", "--db", str(map_db), "--out", str(out), "--no-card"])
    assert json.loads((out / "payload.json").read_text("utf-8")) == {"dates": ["2026-11-20"], "ret": {}}
    assert not (out / "map.html").exists()


def test_byo_map_stops_when_the_payload_fails(tmp_path, capsys):
    empty = tmp_path / "empty.db"
    import sqlite3
    sqlite3.connect(empty).close()
    out = tmp_path / "o"
    assert byo.main(["map", "--db", str(empty), "--out", str(out), "--no-card"]) != 0
    assert not (out / "map.html").exists() and "페이로드를 못 지었다" in capsys.readouterr().err


def test_byo_is_a_console_script():
    import tomllib
    from pathlib import Path
    py = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text("utf-8"))
    assert py["project"]["scripts"]["sector-flow-byo"] == "sectorflow.byo:main"
