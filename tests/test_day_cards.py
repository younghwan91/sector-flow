"""날짜 카드 촬영기(``scripts/day_cards.py``) — 브라우저·Pillow 없이 흐름만: 커밋 안 한 화면 코드·실데이터는 거절,
날마다 그날까지 자른 지도를 찍어 ``<날짜>.png`` 로, 원장 표를 다시 쓴다, 브라우저 사정은 2·카드 결함은 1."""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _mod():
    spec = importlib.util.spec_from_file_location("_t_day_cards", REPO / "scripts" / "day_cards.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


D = _mod()


def _payload(n=7, demo=True):
    f = lambda: {a: [float(i - 3) * (1 if a == "inst" else -1) for i in range(n)] for a in ("indiv", "forgn", "inst", "etc")}  # noqa: E731
    dates = [f"2026-01-{i + 5:02d}" for i in range(n)]
    return {"dates": dates, "sectors": ["가상가", "가상나"], "markets": ["거래소"],
            "flows": {"거래소": {"가상가": f(), "가상나": f()}}, "demo": demo}


class _Img:
    MEDIANCUT = 0

    def __init__(self, src=None):
        self.src = src

    @classmethod
    def open(cls, p):
        return cls(Path(p))

    def convert(self, _mode):
        return self

    def quantize(self, colors, method):
        assert colors == D.COLORS
        return self

    def save(self, target, optimize):
        Path(target).write_bytes(b"q:" + self.src.read_bytes())


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """가짜 Pillow·가짜 카드 촬영기·임시 원장. ``rig.shot`` 은 찍은 map.html 들, ``rig.fail`` 로 예외를 넣는다."""
    pil = types.ModuleType("PIL")
    pil.Image = _Img
    monkeypatch.setitem(sys.modules, "PIL", pil)
    real_load = D._load

    class NoBrowser(RuntimeError):
        pass

    class CardError(RuntimeError):
        pass

    card = types.SimpleNamespace(NoBrowser=NoBrowser, CardError=CardError, shot=[], fail=None)

    def shoot(html, out):
        if card.fail:
            raise card.fail
        page = html.read_text("utf-8")
        card.shot.append(page)
        (out / "map_wide.png").write_bytes(b"card")

    card.shoot = shoot
    monkeypatch.setattr(D, "_load", lambda name: card if name == "map_card" else real_load(name))
    monkeypatch.setattr(D, "_git", lambda *a: "c" * 40 if a[0] == "rev-parse" else "")
    monkeypatch.setattr(D, "OUT", tmp_path / "days")
    ledger = tmp_path / "SCREENSHOTS.md"
    ledger.write_text("# 원장\n\n<!-- day-cards -->\n| 이미지 | 감시 경로 | 찍은 커밋 |\n|---|---|---|\n"
                      f"| docs/images/days/old.png | scripts/day_cards.py | {'a' * 40} |\n\n## 다음\n", "utf-8")
    monkeypatch.setattr(D, "MANIFEST", ledger)
    demo = tmp_path / "demo"
    demo.mkdir()
    (demo / "payload.json").write_text(json.dumps(_payload(), ensure_ascii=False), "utf-8")
    card.demo, card.ledger = demo, ledger
    return card


def test_shoots_the_last_days_each_cut_to_its_date_and_restamps_the_ledger(rig):
    (D.OUT).mkdir()
    (D.OUT / "2025-12-31.png").write_bytes(b"stale")
    assert D.main(["--demo-dir", str(rig.demo)]) == 0
    days = _payload()["dates"][-5:]
    assert sorted(p.name for p in D.OUT.iterdir()) == [f"{d}.png" for d in days], "옛 카드는 지운다"
    assert (D.OUT / f"{days[0]}.png").read_bytes() == b"q:card"
    assert [f'"asof":"{d}"' in page for d, page in zip(days, rig.shot)] == [True] * 5, "날마다 그날까지 자른 지도"
    text = rig.ledger.read_text("utf-8")
    assert "old.png" not in text and text.count("c" * 40) == 5 * len(D.WATCHED) and text.endswith("## 다음\n")


def test_refuses_uncommitted_screen_code(rig, monkeypatch, capsys):
    monkeypatch.setattr(D, "_git", lambda *a: " M scripts/map_card.py")
    assert D.main(["--demo-dir", str(rig.demo)]) == 2
    assert "커밋" in capsys.readouterr().err and not rig.shot


def test_refuses_a_real_payload(rig, capsys):
    (rig.demo / "payload.json").write_text(json.dumps(_payload(demo=False)), "utf-8")
    assert D.main(["--demo-dir", str(rig.demo)]) == 2
    assert "데모" in capsys.readouterr().err and not rig.shot


@pytest.mark.parametrize("kind, code", [("NoBrowser", 2), ("CardError", 1)])
def test_browser_trouble_is_2_and_a_bad_card_is_1_and_the_ledger_stays(rig, kind, code):
    before = rig.ledger.read_text("utf-8")
    rig.fail = getattr(rig, kind)("x")
    assert D.main(["--demo-dir", str(rig.demo)]) == code
    assert rig.ledger.read_text("utf-8") == before


def test_stamp_refuses_a_ledger_without_the_table():
    with pytest.raises(SystemExit, match="day-cards"):
        D.stamp("# 원장\n", ["a.png"], "c" * 40)
