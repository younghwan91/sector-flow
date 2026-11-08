"""KRX 공식 투자자별 거래대금 대조(scripts/krx_truth.py) — 네트워크 없이, 기록해 둔 응답 모양으로만.

응답 픽스처는 data.krx.co.kr ``MDCSTAT02201`` 의 공표 형식(``output[].INVST_TP_NM``·
``NETBID_TRDVAL``, 원·쉼표 구분)을 그대로 따른 **합성값**이다 — 2026-11-09 탐사에서 익명
조회가 LOGOUT 으로 거부돼 실응답을 기록하지 못했고, 형식은 공개 문서·구현(pykrx)과 같다.
값이 공표치가 아니므로 여기 숫자는 산수 검증용이지 시장 사실이 아니다.

검증기(``_vt_krx_truth``)는 producer 를 부르지 않고 파일의 원시 공표 행에서 오차를 다시
세는지, 그리고 블록을 틀리게 바꾸면 빨개지는지도 본다. 모든 실패 경로는 SKIP 이다 —
리포트를 깨지 않는다(D1-m2).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent


def _mod(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


kt = _mod("krx_truth")
vr = _mod("verify_report")

DATE = "2026-11-06"

# ── 기록 픽스처 — MDCSTAT02201 output 의 모양(원, 쉼표 구분). 값은 합성이다(머리말). ──
STK_ROWS = [
    ("금융투자", "900,000,000,000"), ("보험", "100,000,000,000"), ("투신", "200,000,000,000"),
    ("사모", "150,000,000,000"), ("은행", "50,000,000,000"), ("기타금융", "20,000,000,000"),
    ("연기금 등", "280,000,000,000"),
    ("기관합계", "1,700,000,000,000"),
    ("기타법인", "450,000,000,000"), ("개인", "520,000,000,000"),
    ("외국인", "-2,650,000,000,000"), ("기타외국인", "-50,000,000,000"),
    ("전체", "0"),
]
KSQ_ROWS = [
    ("기관합계", "200,000,000,000"), ("기타법인", "4,000,000,000"), ("개인", "-130,000,000,000"),
    ("외국인", "-80,000,000,000"), ("기타외국인", "-1,000,000,000"), ("전체", "0"),
]


def _body(rows) -> bytes:
    out = [{"INVST_TP_NM": n, "ASK_TRDVAL": "1,000", "BID_TRDVAL": "1,000", "NETBID_TRDVAL": v}
           for n, v in rows]
    return json.dumps({"output": out, "CURRENT_DATETIME": "2026.11.09 PM 07:00:00"},
                      ensure_ascii=False).encode()


def _fake_fetch(mkt_id: str, date: str, **_kw) -> dict:
    assert date == DATE
    return kt._parse_body(_body(STK_ROWS if mkt_id == "STK" else KSQ_ROWS))


def _payload() -> dict:
    def net(d):
        return {k: {"v": v, "pct": 50.0} for k, v in d.items()}
    return {
        "markets": ["거래소", "코스닥"], "dates": ["2026-11-05", DATE],
        "today": {"date": DATE, "market": {
            "전체": {"net": net({"inst": 19714.16, "forgn": -28336.39, "indiv": 4050.74, "etc": 4552.71}),
                   "tv": 335877.83},
            "거래소": {"net": net({"inst": 17633.83, "forgn": -27460.19, "indiv": 5319.09, "etc": 4503.28}),
                    "tv": 284671.23},
            "코스닥": {"net": net({"inst": 2080.33, "forgn": -876.2, "indiv": -1268.35, "etc": 49.43}),
                    "tv": 51206.6},
        }},
    }


def _official() -> dict:
    return kt.fetch_official(DATE, ["거래소", "코스닥"], fetch=_fake_fetch, pause=0)


# ── 응답 파싱 ──────────────────────────────────────────────────────────────

def test_parse_body_converts_comma_won_strings():
    """공표 금액은 쉼표 구분 문자열(원)이다 — 쉼표를 벗겨 float 로 읽는다. 빈 값·`-` 는 0."""
    rows = kt._parse_body(_body(STK_ROWS + [("빈값", ""), ("대시", "-")]))
    assert rows["기관합계"] == 1_700_000_000_000.0
    assert rows["외국인"] == -2_650_000_000_000.0
    assert rows["빈값"] == 0.0 and rows["대시"] == 0.0


def test_parse_body_logout_is_a_fetch_error_naming_the_login_wall():
    """2026-11-09 실측: 익명 조회는 400 `LOGOUT` — 이유에 로그인 장벽이라고 적어야 런북이 받는다."""
    with pytest.raises(kt.KrxFetchError, match="로그인 장벽"):
        kt._parse_body(b"LOGOUT")
    with pytest.raises(kt.KrxFetchError, match="형식"):
        kt._parse_body(b"<html>maintenance</html>")
    with pytest.raises(kt.KrxFetchError, match="비었다"):
        kt._parse_body(json.dumps({"output": []}).encode())


def test_fetch_official_converts_won_to_eok_and_keeps_raw_rows():
    """시장 블록은 억(소수 2자리), 원시 공표 행(원)은 rows 에 그대로 재운다 — 매핑을 나중에 다시 따질 수 있게."""
    off = _official()
    assert off["markets"]["거래소"] == {"inst": 17000.0, "forgn": -27000.0, "indiv": 5200.0, "etc": 4500.0}
    assert off["markets"]["코스닥"]["forgn"] == -810.0            # 외국인 + 기타외국인
    assert off["rows"]["거래소"]["기관합계"] == 1_700_000_000_000.0
    assert off["date"] == DATE and "재배포 금지" in off["use"]


def test_actor_mapping_sums_foreign_rows_and_inst_falls_back_to_subtotals():
    """외국인 = 외국인+기타외국인. 기관합계 행이 사라지면 공표 소분류 합으로 대신한다(행 이름이 바뀌는 날의 보험)."""
    rows = kt._parse_body(_body([r for r in STK_ROWS if r[0] != "기관합계"]))
    assert kt._actor_won(rows, "inst") == 1_700_000_000_000.0     # 소분류 7행의 합
    assert kt._actor_won(rows, "forgn") == -2_700_000_000_000.0
    with pytest.raises(kt.KrxFetchError):
        kt._actor_won({"개인": 1.0}, "forgn")


# ── 대조(compare) ──────────────────────────────────────────────────────────

def test_compare_gap_is_relative_to_the_official_value():
    """gap = |근사 − 공식| / |공식| × 100 — 분모는 공식 공표치다(근사를 분모로 쓰면 오차가 자기를 가린다)."""
    got = kt.compare(_payload()["today"], _official())
    g = got["markets"]["거래소"]
    assert g["inst"]["gap_pct"] == pytest.approx(633.83 / 17000 * 100, abs=0.011)
    assert g["forgn"]["gap_pct"] == pytest.approx(460.19 / 27000 * 100, abs=0.011)
    assert g["inst"]["official"] == 17000.0 and g["inst"]["approx"] == 17633.83
    assert g["inst"]["diff"] == pytest.approx(633.83, abs=0.011)
    assert got["status"] == "ok" and got["date"] == DATE


def test_compare_gates_small_official_amounts_to_none():
    """분모 게이트 — |공식| 이 그 시장 거래대금의 0.2% 미만이면 % 대신 None(절대차만). 0 근처의 상대 오차는 잡음이다."""
    got = kt.compare(_payload()["today"], _official())
    etc = got["markets"]["코스닥"]["etc"]                          # |40억| < 51206.6 × 0.002
    assert etc["gap_pct"] is None
    assert etc["diff"] == pytest.approx(9.43, abs=0.011)
    assert got["markets"]["코스닥"]["indiv"]["gap_pct"] is not None  # 게이트 위는 % 가 있다


# ── attach — 켜짐·캐시·실패 격리 ────────────────────────────────────────────

def _no_fetch(*_a, **_k):
    raise AssertionError("네트워크를 타면 안 되는 경로다")


def test_attach_is_silent_when_not_opted_in(tmp_path):
    """캐시도 KR_QUANT_KRX_TRUTH 도 없으면 흔적도 없다 — 선택 기능이 모든 리포트에 블록을 남기면 안 된다."""
    P = _payload()
    got = kt.attach(P, str(tmp_path), str(tmp_path), env={}, fetch=_no_fetch, pause=0)
    assert got is None
    assert "krx_truth" not in P["today"]
    assert not (tmp_path / "krx_truth.json").exists()


def test_attach_uses_same_day_cache_without_fetching(tmp_path):
    """같은 기준일 폴더의 krx_truth.json 이 있으면 다시 받지 않는다 — 기준일당 한 번(이용 범위 가드)."""
    root, build = tmp_path, tmp_path / f"{DATE}.building.1"
    (root / DATE).mkdir()
    build.mkdir()
    (root / DATE / "krx_truth.json").write_text(json.dumps(_official(), ensure_ascii=False), "utf-8")
    P = _payload()
    got = kt.attach(P, str(build), str(root), env={}, fetch=_no_fetch, pause=0)
    assert got["status"] == "ok"
    assert P["today"]["krx_truth"]["markets"]["거래소"]["inst"]["official"] == 17000.0
    # 재운 사본이 새 폴더와 같이 움직인다
    assert json.loads((build / "krx_truth.json").read_text("utf-8"))["date"] == DATE


def test_attach_network_failure_is_a_skip_block_not_an_exception(tmp_path):
    """망·로그인 장벽은 skip 이유로만 남는다 — 리포트 빌드는 계속된다(never FAIL)."""
    def dead(*_a, **_k):
        raise kt.KrxFetchError("LOGOUT — 로그인 장벽(익명 조회 거부). 접근이 풀릴 때까지 SKIP 이다")
    P = _payload()
    got = kt.attach(P, str(tmp_path), None, env={"KR_QUANT_KRX_TRUTH": "1"}, fetch=dead, pause=0)
    assert got["status"] == "skip" and "로그인 장벽" in got["reason"]
    assert "내부 오류" not in got["reason"]                        # 예상된 실패가 버그로 보고되면 런북이 어긋난다
    assert P["today"]["krx_truth"] is got
    assert not (tmp_path / "krx_truth.json").exists()


def test_attach_contains_its_own_bugs_as_internal_error_skip(tmp_path):
    """모듈 버그(예상 밖 예외)도 리포트를 죽이지 못한다 — '내부 오류' skip 으로 격리된다."""
    def bug(*_a, **_k):
        raise RuntimeError("boom")
    P = _payload()
    got = kt.attach(P, str(tmp_path), None, env={"KR_QUANT_KRX_TRUTH": "1"}, fetch=bug, pause=0)
    assert got["status"] == "skip" and "내부 오류" in got["reason"] and "boom" in got["reason"]
    assert P["today"]["krx_truth"] is got


def test_attach_writes_the_sidecar_with_the_usage_guard(tmp_path):
    """받은 공표치는 build 폴더의 krx_truth.json 에 — 이용 범위 문구(내부 검증용·재배포 금지)를 담아 재운다."""
    P = _payload()
    got = kt.attach(P, str(tmp_path), None, env={"KR_QUANT_KRX_TRUTH": "1"}, fetch=_fake_fetch, pause=0)
    assert got["status"] == "ok"
    side = json.loads((tmp_path / "krx_truth.json").read_text("utf-8"))
    assert side["markets"]["코스닥"]["inst"] == 2000.0
    assert "내부 검증용" in side["use"] and "재배포 금지" in side["use"]


def test_cli_rewrites_payload_in_place_and_exits_zero(tmp_path, monkeypatch):
    """CLI(--payload --fetch)는 today.krx_truth 를 채워 제자리에 다시 쓴다 — 종료코드는 skip 이어도 0 이다."""
    monkeypatch.setattr(kt, "fetch_market", _fake_fetch)
    monkeypatch.setattr(kt.time, "sleep", lambda _s: None)
    folder = tmp_path / DATE
    folder.mkdir()
    p = folder / "payload.json"
    p.write_text(json.dumps(_payload(), ensure_ascii=False), "utf-8")
    assert kt.main(["--payload", str(p), "--fetch"]) == 0
    saved = json.loads(p.read_text("utf-8"))
    assert saved["today"]["krx_truth"]["status"] == "ok"
    assert (folder / "krx_truth.json").exists()


# ── 검증기 — 공표 행에서 다시 세기 ───────────────────────────────────────────

def _verify(P: dict, folder: str) -> list[str]:
    vr.FAILS.clear()
    vr._vt_krx_truth(P, folder)
    return list(vr.FAILS)


def _built(tmp_path) -> tuple[dict, str]:
    off = _official()
    P = _payload()
    P["today"]["krx_truth"] = kt.compare(P["today"], off)
    (tmp_path / "krx_truth.json").write_text(json.dumps(off, ensure_ascii=False), "utf-8")
    return P, str(tmp_path)


def test_verifier_recomputes_the_gap_from_the_raw_published_rows(tmp_path):
    """검증기는 producer 를 믿지 않는다 — 파일의 원시 공표 행(원)에서 억·오차·게이트를 따로 다시 센다."""
    P, folder = _built(tmp_path)
    assert _verify(P, folder) == []
    bent = json.loads(json.dumps(P))
    bent["today"]["krx_truth"]["markets"]["거래소"]["inst"]["gap_pct"] = 0.01
    assert _verify(bent, folder), "오차를 틀리게 적었는데 검증기가 초록이다"
    gone = json.loads(json.dumps(P))
    gone["today"]["krx_truth"]["markets"]["코스닥"]["etc"]["gap_pct"] = 0.5   # 게이트 아래 칸에 % 를 적음
    assert _verify(gone, folder), "분모 게이트를 어겼는데 검증기가 초록이다"


def test_verifier_skip_block_is_a_note_not_a_failure(tmp_path, capsys):
    """skip 은 사유 한 줄이다 — 망·로그인 장벽을 리포트 품질 실패(VERIFY_FAILED)로 만들면 안 된다."""
    P = _payload()
    P["today"]["krx_truth"] = {"status": "skip", "reason": "LOGOUT — 로그인 장벽"}
    assert _verify(P, str(tmp_path)) == []
    assert "SKIP KRX 공식 거래대금 대조" in capsys.readouterr().out


def test_verifier_is_silent_when_the_feature_is_off(tmp_path, capsys):
    """파일도 블록도 없는 리포트(기능 꺼짐)에는 검사도 SKIP 줄도 없다 — 선택 기능이 기본 출력을 늘리면 안 된다."""
    assert _verify(_payload(), str(tmp_path)) == []
    assert "KRX 공식" not in capsys.readouterr().out


def test_verifier_flags_an_ok_block_whose_truth_file_vanished(tmp_path):
    """블록은 ok 인데 폴더에 krx_truth.json 이 없으면 실패다 — 재계산 근거가 사라진 리포트는 믿을 수 없다."""
    P = _payload()
    P["today"]["krx_truth"] = kt.compare(P["today"], _official())
    assert _verify(P, str(tmp_path)), "근거 파일이 없는데 검증기가 초록이다"


# ── TUI 도움말 ─────────────────────────────────────────────────────────────

def _fv():
    import sectorflow.tui.flow_view as fv  # noqa: PLC0415
    return fv


def test_help_tail_lists_the_measured_gaps_per_market():
    """도움말 끝 절 — '공식 거래대금 대비 근사 오차: 기관 x% · 외국인 y% …(KRX 공표치, 내부 검증용)'. 게이트 칸은 —."""
    fv = _fv()
    P = _payload()
    P["today"]["krx_truth"] = kt.compare(P["today"], _official())
    text = "\n".join(t for _h, t in fv.krx_truth_help(P))
    assert "공식 거래대금 대비 근사 오차" in text and "내부 검증용" in text
    assert "기관 3.7%" in text and "외국인 1.7%" in text          # 거래소
    assert "기타법인 —" in text                                   # 코스닥 — 분모 게이트
    skipped = _payload()
    skipped["today"]["krx_truth"] = {"status": "skip", "reason": "LOGOUT — 로그인 장벽"}
    assert "건너뜀: LOGOUT" in "\n".join(t for _h, t in fv.krx_truth_help(skipped))
    assert fv.krx_truth_help(_payload()) == []                    # 기능 꺼짐 — 절도 없다


def test_help_entries_append_the_krx_truth_tail():
    """도움말 조립(help_entries)이 공식 대조 절을 빼먹으면 측정값을 볼 길이 TUI 에 없다."""
    fv = _fv()
    P = _payload()
    P["today"]["krx_truth"] = {"status": "skip", "reason": "이유"}
    text = "\n".join(t for _h, t in fv.help_entries(SimpleNamespace(d=P)))
    assert "KRX 공식 거래대금 대조" in text


def test_sector_flow_build_calls_attach_before_writing_outputs():
    """sector_flow.main 이 revision_stats 뒤·산출물 쓰기 전에 attach 를 부른다 — 둘 다 payload 에 실려야 HTML 이 본다."""
    src = (REPO / "scripts" / "sector_flow.py").read_text("utf-8")
    i_rev = src.index('payload["revision_stats"] = revision_stats(payload, root)')
    i_kt = src.index("_krx_truth().attach(payload,")
    i_out = src.index('json.dump(payload, f, ensure_ascii=False)')
    assert i_rev < i_kt < i_out
