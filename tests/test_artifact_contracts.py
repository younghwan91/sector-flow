"""생산자-파서 계약 — **실제 산출물**을 그것을 읽는 쪽에 넣는다.

``trust_page``·``daily_report.sh`` 의 원장 줄은 ``VERIFY.txt``·관문 출력·``FINAL.txt`` 의 **글자**를 정규식으로 읽는다.
파서 검사는 손으로 쓴 글자만 파싱해서, 생산자가 요약줄을 "검사 3건, 실패 0건" 으로 바꾸면 신뢰 페이지가
``checks 0, complete False`` 를 내도 pytest 는 초록이었다. 여기서는 합성 DB 로 배치와 같은 명령을 돌려 나온 파일을
읽는다. ``digest`` 도 합성 페이로드가 아니라 같은 파이프라인의 페이로드로 돈다 — 페이로드 키가 바뀌면 여기서 드러난다.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from tests import test_pipeline_e2e as E

REPO = E.REPO
SHELL = (REPO / "scripts" / "daily_report.sh").read_text("utf-8")
tp = E._mod("trust_page")
dg = E._mod("digest")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """잠정 리포트 + 같은 기준일 확정 리포트(수급 일부를 고친 DB 로 다시 지음) — 배치와 같은 명령으로."""
    base = tmp_path_factory.mktemp("contract")
    report = E.run_pipeline(base)
    asof = report["P"]["dates"][-1]
    root = base / "reports"
    prov = root / asof
    prov.mkdir(parents=True)
    for name in ("payload.json", "viewer.html", "numbers.html"):
        shutil.copy(report["dir"] / name, prov / name)

    db = base / "final.db"
    shutil.copy(report["db"], db)
    con = sqlite3.connect(db)
    con.execute("UPDATE supply_demand SET institution = institution + 5000000 "
                "WHERE date = ? AND code IN (SELECT code FROM stocks WHERE sector = '금융')", (asof,))
    con.commit()
    con.close()
    final = root / f"{asof}.building.77"
    final.mkdir()
    p = E._run(str(E.SCRIPTS / "sector_flow.py"), "--db", str(db), "--days", str(E.REPORT_DAYS), "--final",
               "--json", str(final / "payload.json"), "--reports-root", str(root))
    assert p.returncode == 0, p.stderr[-3000:]
    return {"report": report, "asof": asof, "root": root, "prov": prov, "final": final, "db": db,
            "P_final": json.loads((final / "payload.json").read_text("utf-8"))}


def _shell_summary(verify_txt: Path) -> str:
    """``daily_report.sh`` 가 원장에 싣는 요약 — **그 스크립트의 sed 식을 그대로** 돌린다."""
    m = re.search(r"""EV_SUMMARY="\$\(sed -n '([^']+)'""", SHELL)
    assert m, "daily_report.sh 의 EV_SUMMARY sed 줄을 못 찾았다"
    out = subprocess.run(["sed", "-n", m.group(1), str(verify_txt)], capture_output=True, text=True, check=True)
    return out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""


def test_real_verify_output_parses_on_the_trust_page_and_in_the_shell_summary(built, tmp_path):
    """검증기 실출력 → ``parse_verify`` 와 쉘 sed 가 같은 건수를 읽는다. 층 머리줄도 방법 절이 읽는다."""
    v = E.verified(built["report"])
    n_marks = sum(len(x) for x in v["marks"].values())
    parsed = tp.parse_verify(v["out"])
    assert parsed["complete"] is True, v["out"][-500:]
    assert parsed["checks"] > 150 and parsed["fails"] == 0 and parsed["fail_names"] == []
    assert parsed["checks"] == n_marks
    assert parsed["claims"] == sum(x.count("CLAIM") for x in v["marks"].values())

    txt = tmp_path / "VERIFY.txt"
    txt.write_text(v["out"], "utf-8")
    assert _shell_summary(txt) == f"{parsed['checks']} {parsed['fails']}"

    day = tmp_path / "2026-01-02"
    day.mkdir()
    shutil.copy(txt, day / "VERIFY.txt")
    layers = dict(tp.verify_layers(str(day)))
    assert {"A", "A3", "F", "T", "W"} <= set(layers), layers


def test_real_failing_verify_names_its_failures(built, tmp_path):
    """실패한 실출력에서 실패 수·이름을 읽는다 — 신뢰 페이지 사유 칸과 원장의 실패 수."""
    bad = tmp_path / "2026-09-07.building.1"
    shutil.copytree(built["report"]["dir"], bad)
    P = json.loads((bad / "numbers.html").read_text("utf-8").split("const D = ", 1)[1].split(";\n", 1)[0])
    html = (bad / "numbers.html").read_text("utf-8")
    row = next(r for r in P["blocks"]["20|전체"]["rows"] if r.get("exp") is not None)
    row["x"] += 7.0
    (bad / "numbers.html").write_text(re.sub(r"const D = \{.*?\};\n", lambda _m: "const D = " + json.dumps(
        P, ensure_ascii=False) + ";\n", html, count=1, flags=re.S), "utf-8")
    v = E._run(str(E.SCRIPTS / "verify_report.py"), "--dir", str(bad), "--layers", "A")
    assert v.returncode == 1, v.stdout[-1500:]
    parsed = tp.parse_verify(v.stdout)
    assert parsed["complete"] is True and parsed["fails"] >= 1
    assert len(parsed["fail_names"]) == parsed["fails"], v.stdout[-800:]
    assert all(n.startswith("A/") for n in parsed["fail_names"]), parsed["fail_names"]
    txt = tmp_path / "VERIFY.txt"
    txt.write_text(v.stdout, "utf-8")
    assert _shell_summary(txt) == f"{parsed['checks']} {parsed['fails']}"


def _gate(db: Path, asof: str) -> subprocess.CompletedProcess:
    return E._run(str(E.SCRIPTS / "completeness_gate.py"), "--asof", asof, "--db", str(db))


def test_real_gate_pass_output_parses_with_its_minimum(built):
    """관문 통과 실출력 → 기준일·시장·최저 비율을 읽는다(최저 ``None`` 이면 페이지가 공백이 된다)."""
    p = _gate(built["report"]["db"], built["asof"])
    assert p.returncode == 0, p.stdout + p.stderr[-800:]
    rows = tp.parse_gate(p.stdout).get(built["asof"])
    assert rows, p.stdout
    assert {r["market"] for r in rows} == {"거래소", "코스닥"} and all(r["ok"] for r in rows)
    assert all(r["min"] is not None and 0.97 <= r["min"] <= 1.1 for r in rows), rows


def test_real_gate_block_output_is_read_as_blocked_from_the_folder(built, tmp_path):
    """관문이 막은 실출력 → ``INCOMPLETE.txt`` 로 쓴 폴더를 ``scan_day`` 가 '수집 미완·막힘' 으로 읽는다."""
    db = tmp_path / "partial.db"
    shutil.copy(built["report"]["db"], db)
    con = sqlite3.connect(db)
    con.execute("DELETE FROM supply_demand WHERE date = ? AND code IN "
                "(SELECT code FROM stocks WHERE market = '코스닥')", (built["asof"],))
    con.commit()
    con.close()
    p = _gate(db, built["asof"])
    assert p.returncode == 75, p.stdout + p.stderr[-800:]
    day = tmp_path / "reports" / built["asof"]
    day.mkdir(parents=True)
    (day / "INCOMPLETE.txt").write_text(f"[..] {built['asof']} 수집 미완 — 리포트를 만들지 않았다.\n{p.stdout}", "utf-8")
    got = tp.scan_day(str(tmp_path / "reports"), built["asof"], {})
    assert got["state"] == "수집 미완" and got["verify_status"] == "incomplete" and got["gate_src"] == "INCOMPLETE.txt"
    assert {r["market"]: r["ok"] for r in got["gate"]} == {"거래소": True, "코스닥": False}


def test_real_final_text_parses_market_and_sector_counts(built):
    """``--final`` 실산출(``final_diff.txt``) → ``parse_final`` 의 시장·섹터 칸 수가 페이로드와 같다."""
    fd = built["P_final"]["final_diff"]
    assert fd and fd["n_changed"] > 0, built["P_final"].get("final_diff_reason")
    got = tp.parse_final((built["final"] / "final_diff.txt").read_text("utf-8"))
    moved = sum(1 for t in fd["market_totals"] if t["diff"] is None or abs(t["diff"]) > fd["tol"])
    assert got["kind"] == "diff"
    assert got["sector"] == (fd["n_changed"], fd["n_cells"])
    assert got["market"] == (moved, len(fd["market_totals"]))
    assert moved > 0


def test_real_final_without_a_provisional_says_why(built, tmp_path):
    """비교할 잠정이 없는 확정 → 파서가 이유를 읽는다('unknown' 으로 떨어지면 페이지가 말을 못 한다)."""
    out = tmp_path / "2026-09-07.building.2"
    out.mkdir()
    p = E._run(str(E.SCRIPTS / "sector_flow.py"), "--db", str(built["db"]), "--days", str(E.REPORT_DAYS), "--final",
               "--json", str(out / "payload.json"), "--reports-root", str(tmp_path / "empty"))
    assert p.returncode == 0, p.stderr[-2000:]
    got = tp.parse_final((out / "final_diff.txt").read_text("utf-8"))
    assert got["kind"] == "none" and got["reason"]


def test_scan_and_page_read_a_real_report_folder(built, tmp_path):
    """실산출 폴더 전체 → ``scan`` 의 상태·검증·관문·정정이 맞고 페이지가 그 숫자를 싣는다."""
    v = E.verified(built["report"])
    root = tmp_path / "reports"
    day = root / built["asof"]
    day.mkdir(parents=True)
    for name in ("payload.json", "numbers.html"):
        shutil.copy(built["final"] / name if name == "payload.json" else built["report"]["dir"] / name, day / name)
    (day / "VERIFY.txt").write_text(v["out"], "utf-8")
    (day / "FINAL.txt").write_text("[..] daily_finalize 확인 뒤 확정치로 만들었다.\n"
                                   + (built["final"] / "final_diff.txt").read_text("utf-8"), "utf-8")
    (root / "cron.log").write_text(_gate(built["report"]["db"], built["asof"]).stdout + "[..] 생성 시작\n", "utf-8")
    (root / "latest").symlink_to(day)

    d = tp.scan(str(root))[-1]
    assert d["state"] == "확정" and d["verify_status"] in ("pass", "claim") and d["gate_src"] == "cron.log"
    assert d["verify"]["complete"] and d["verify"]["checks"] > 150
    fd = built["P_final"]["final_diff"]
    assert d["final"]["sector"] == (fd["n_changed"], fd["n_cells"])
    assert [e["kind"] for e in tp.folder_errata([d])] == ["확정 정정"]

    out = tmp_path / "trust.html"
    p = E._run(str(E.SCRIPTS / "trust_page.py"), "--root", str(root), "--out", str(out))
    assert p.returncode == 0, p.stdout + p.stderr[-1500:]
    page = out.read_text("utf-8")
    v = d["verify"]
    assert f'{v["checks"] - v["fails"] - v["claims"]}/{v["checks"]}' in page
    assert f'섹터 {fd["n_changed"]}/{fd["n_cells"]}칸' in page


# ── digest: 합성 페이로드가 아니라 파이프라인 페이로드로 ───────────────────────────────

def _digest(src: Path, out: Path) -> dict:
    assert dg.main(["--dir", str(src), "--out", str(out), "--png", "skip"]) == 0
    return json.loads((out / "digest.json").read_text("utf-8"))


def test_digest_runs_on_the_pipeline_payload_and_says_its_numbers(built, tmp_path):
    """잠정 파이프라인 페이로드 → 시장 순매수·섹터 상하위·연속 줄이 페이로드 값과 같고, 잠정 표지가 붙는다."""
    F = _digest(built["prov"], tmp_path)
    T = built["report"]["P"]["today"]
    net = T["market"]["전체"]["net"]
    assert {n["actor"]: n["v"] for n in F["net"]} == {a: net[a]["v"] for a in ("inst", "forgn", "indiv", "etc")}
    assert F["final"] is False and F["asof"] == built["asof"]
    assert F["streak"] and F["streak"]["market"], "payload 의 streaks 를 digest 가 못 읽었다"
    longest = T["streaks"]["longest"]["전체"][F["dominant"]]
    assert F["streak"]["sector"] == longest and F["streak"]["sector_fact"]

    inst = next(s for s in F["sectors"] if s["actor"] == "inst")
    rows = sorted(((n, r["inst"]["v"]) for n, r in T["sectors"]["전체"].items()
                   if n not in dg.EXCLUDED_SECTORS and r["inst"]["v"] is not None), key=lambda t: -t[1])
    assert [n for n, _v, _p in inst["top"]] == [n for n, v in rows if v > 0][:dg.TOP_N]

    text = (tmp_path / "digest.md").read_text("utf-8")
    card = (tmp_path / "digest_card.html").read_text("utf-8")
    for body in (text, card):
        assert "잠정치다" in body and dg.DISCLAIMER in body and dg.SOURCE in body
    assert 'class="chip prov"' in card and "__" not in re.sub(r"<!--.*?-->", "", card, flags=re.S)


def test_digest_on_the_final_payload_drops_the_provisional_label(built, tmp_path):
    """확정 파이프라인 페이로드 → 잠정 표지가 없고, 바뀐 칸 수가 ``final_diff`` 와 같다."""
    src = tmp_path / "src"
    src.mkdir()
    shutil.copy(built["final"] / "payload.json", src / "payload.json")
    F = _digest(src, tmp_path)
    fd = built["P_final"]["final_diff"]
    assert F["final"] is True and F["final_diff"]["n_changed"] == fd["n_changed"]
    text = (tmp_path / "digest.md").read_text("utf-8")
    card = (tmp_path / "digest_card.html").read_text("utf-8")
    for body in (text, card):
        assert "잠정치다" not in body
        assert f"잠정→확정 변경: {fd['n_changed']:,}칸 / {fd['n_cells']:,}칸" in body
    assert 'class="chip final"' in card
