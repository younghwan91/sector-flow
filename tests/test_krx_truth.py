"""공표 시장값 대조(scripts/krx_truth.py) — 네트워크 없이, 기록해 둔 응답 모양으로만.

KRX 픽스처는 data.krx.co.kr ``MDCSTAT02201`` 의 공표 형식(``output[].INVST_TP_NM``·
``NETBID_TRDVAL``, 원·쉼표 구분)을 그대로 따른 **합성값**이다 — 2026-11-09 탐사에서 익명
조회가 LOGOUT 으로 거부돼 실응답을 기록하지 못했고, 형식은 공개 문서·구현(pykrx)과 같다.
네이버 픽스처는 ``/api/index/KOSPI/trend`` 2026-11-09 실응답의 모양이고 값은 합성이다.
여기 숫자는 산수 검증용이지 시장 사실이 아니다.

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


KRX_ENV = {"KR_QUANT_KRX_TRUTH": "1", "KR_QUANT_KRX_TRUTH_SOURCE": "krx"}


def _official() -> dict:
    return kt.fetch_official(DATE, ["거래소", "코스닥"], source="krx", fetch=_fake_fetch, pause=0)


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
    assert got["status"] == "ok" and got["date"] == DATE


def test_compare_total_catches_a_big_error_the_actor_gate_hides():
    """시장 합계 = Σ|오차|÷Σ|공표|. 주체 칸이 게이트에 걸려도(공표 근 0) 합계·칩이 잡는다 — 10-29 개인이 그 꼴이었다.

    값은 합성: 공표 개인 −429·외국인 +1,221·기관 −4,132 에 근사 −11,028·+8,447·−754 는 2026-10-29 대조의 모양.
    """
    P = _payload()
    M = P["today"]["market"]["거래소"]
    M["tv"] = 284671.23
    M["net"] = {"indiv": {"v": -11028.0}, "forgn": {"v": 8447.0}, "inst": {"v": -754.0}}
    off = {"date": DATE, "source": "naver", "markets": {"거래소": {"indiv": -429.0, "forgn": 1221.0, "inst": -4132.0}}}
    g = kt.compare(P["today"], off)["markets"]["거래소"]
    assert g["indiv"]["gap_pct"] is None                          # |429| < 284671 × 0.002
    assert g["total_pct"] == pytest.approx((10599 + 7226 + 3378) / 5782 * 100, abs=0.011)
    assert g["hot"] is True


def test_compare_hot_only_above_the_threshold():
    """칩 판정은 합계가 문턱 **초과**일 때뿐이다 — 늘 뜨는 경고는 경고가 아니다."""
    got = kt.compare(_payload()["today"], _official())
    assert got["markets"]["거래소"]["total_pct"] == pytest.approx(2.2, abs=0.5)
    assert got["markets"]["거래소"]["hot"] is False


def test_compare_marks_a_sign_flip_with_a_denominator_gate():
    """부호 반대는 둘 중 큰 쪽이 게이트 이상일 때만 — 10-01 외국인(공표 −5,558 vs +390 꼴)은 뜨고, 0 근처 둘은 안 뜬다."""
    P = _payload()
    M = P["today"]["market"]["거래소"]
    M["tv"] = 100000.0                                            # 게이트 200억
    M["net"] = {"forgn": {"v": 390.0}, "indiv": {"v": 50.0}, "inst": {"v": 500.0}}
    off = {"date": DATE, "source": "naver", "markets": {"거래소": {"forgn": -5558.0, "indiv": -40.0, "inst": 300.0}}}
    g = kt.compare(P["today"], off)["markets"]["거래소"]
    assert g["forgn"]["flip"] is True
    assert g["indiv"]["flip"] is False
    assert g["inst"]["flip"] is False


def test_compare_never_puts_published_values_in_the_payload_block():
    """약관 미확인 — 페이로드엔 오차만 싣는다. 공표 수치·절대차가 블록에 있으면 화면·웹으로 재배포된다."""
    got = kt.compare(_payload()["today"], _official())
    text = json.dumps(got, ensure_ascii=False)
    for key in ('"official"', '"diff"', '"approx"', '"rows"'):
        assert key not in text
    assert "17000" not in text                                    # 공표 기관 합계 값


# ── 네이버 소스 ────────────────────────────────────────────────────────────

NAVER_BODY = json.dumps({"bizdate": DATE.replace("-", ""), "personalValue": "+5,200",
                         "foreignValue": "-27,000", "institutionalValue": "+17,000"}).encode()


class _Resp:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def read(self, _n=-1):
        return self.body


def test_parse_naver_reads_signed_comma_eok_as_won_rows():
    """네이버 값은 부호·쉼표가 붙은 억이다 — 공표 행(원)으로 바꿔 KRX 와 같은 이름으로 재운다."""
    assert kt._parse_naver(NAVER_BODY, DATE) == {"개인": 5200e8, "외국인": -27000e8, "기관합계": 17000e8}


def test_parse_naver_refuses_another_day_all_zero_and_format_drift():
    """휴장일·공표 전은 값이 전부 0 으로 온다(2026-11-08 실측) — 0 을 공표치로 믿으면 오차가 ~100% 로 적힌다."""
    with pytest.raises(kt.KrxFetchError, match="다른 날"):
        kt._parse_naver(NAVER_BODY, "2026-11-05")
    zero = json.dumps({"bizdate": DATE.replace("-", ""), "personalValue": "0", "foreignValue": "0",
                       "institutionalValue": "0"}).encode()
    with pytest.raises(kt.KrxFetchError, match="공표 전"):
        kt._parse_naver(zero, DATE)
    with pytest.raises(kt.KrxFetchError, match="형식"):
        kt._parse_naver(b"<html>", DATE)


def test_fetch_naver_retries_a_network_error_once_then_gives_up():
    """크론 부담 상한 — 망 오류만 **한 번** 더 시도한다. 타임아웃을 넘겨 요청하고 UA 에 이메일이 없다."""
    seen = []

    def flaky(req, timeout):
        seen.append((req.full_url, req.get_header("User-agent"), timeout))
        if len(seen) == 1:
            raise OSError("timed out")
        return _Resp(NAVER_BODY)
    assert kt.fetch_naver("KOSPI", DATE, opener=flaky, retry_pause=0)["개인"] == 5200e8
    assert len(seen) == 2
    assert seen[0][0].endswith("/index/KOSPI/trend?bizdate=20261106")
    assert seen[0][2] == kt.NAVER_TIMEOUT_S
    assert "@" not in seen[0][1]

    calls = []

    def dead(req, timeout):
        calls.append(req)
        raise OSError("down")
    with pytest.raises(kt.KrxFetchError, match="접속 실패"):
        kt.fetch_naver("KOSPI", DATE, opener=dead, retry_pause=0)
    assert len(calls) == 2


def test_fetch_naver_does_not_retry_a_client_error():
    """4xx 는 다시 물어도 같다 — 재시도하지 않는다."""
    import urllib.error
    calls = []

    def refuse(req, timeout):
        calls.append(req)
        raise urllib.error.HTTPError(req.full_url, 403, "no", {}, None)
    with pytest.raises(kt.KrxFetchError, match="403"):
        kt.fetch_naver("KOSPI", DATE, opener=refuse, retry_pause=0)
    assert len(calls) == 1


def test_default_source_is_naver_without_the_etc_actor(tmp_path):
    """기본 소스는 네이버 — 기타법인 행이 없어 그 주체는 비교 없음(None)이고 합계는 셋으로 낸다."""
    def fake(index, date, **_k):
        assert index in ("KOSPI", "KOSDAQ")
        return kt._parse_naver(NAVER_BODY, date)
    P = _payload()
    got = kt.attach(P, str(tmp_path), None, env={"KR_QUANT_KRX_TRUTH": "1"}, fetch=fake, pause=0)
    assert got["status"] == "ok" and got["source"] == "naver" and "미대조" in got["label"]
    assert got["markets"]["거래소"]["etc"] is None
    assert got["markets"]["거래소"]["total_pct"] is not None
    assert json.loads((tmp_path / "krx_truth.json").read_text("utf-8"))["source"] == "naver"


def test_sidecar_is_not_served_by_the_web_server():
    """공표 수치는 krx_truth.json 에만 있다 — 서버 목록에 들어가면 재배포다."""
    sr = _mod("serve_reports")
    assert "krx_truth.json" not in sr.FILES


def test_compare_gates_small_official_amounts_to_none():
    """분모 게이트 — |공식| 이 그 시장 거래대금의 0.2% 미만이면 % 대신 None(절대차만). 0 근처의 상대 오차는 잡음이다."""
    got = kt.compare(_payload()["today"], _official())
    etc = got["markets"]["코스닥"]["etc"]                          # |40억| < 51206.6 × 0.002
    assert etc["gap_pct"] is None
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
    assert P["today"]["krx_truth"]["markets"]["거래소"]["inst"]["gap_pct"] is not None
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
    """받은 공표치는 build 폴더의 krx_truth.json 에 — 이용 범위 문구(내부 대조 전용·재배포 금지)를 담아 재운다."""
    P = _payload()
    got = kt.attach(P, str(tmp_path), None, env=KRX_ENV, fetch=_fake_fetch, pause=0)
    assert got["status"] == "ok"
    side = json.loads((tmp_path / "krx_truth.json").read_text("utf-8"))
    assert side["markets"]["코스닥"]["inst"] == 2000.0
    assert "내부 대조 전용" in side["use"] and "재배포 금지" in side["use"]


def test_cli_rewrites_payload_in_place_and_exits_zero(tmp_path, monkeypatch):
    """CLI(--payload --fetch)는 today.krx_truth 를 채워 제자리에 다시 쓴다 — 종료코드는 skip 이어도 0 이다."""
    monkeypatch.setenv("KR_QUANT_KRX_TRUTH_SOURCE", "krx")
    monkeypatch.setitem(kt.SOURCES, "krx", (_fake_fetch, kt.MKT_IDS, 0, tuple(kt.ACTOR_ROWS)))
    folder = tmp_path / DATE
    folder.mkdir()
    p = folder / "payload.json"
    p.write_text(json.dumps(_payload(), ensure_ascii=False), "utf-8")
    assert kt.main(["--payload", str(p), "--fetch"]) == 0
    saved = json.loads(p.read_text("utf-8"))
    assert saved["today"]["krx_truth"]["status"] == "ok"
    assert (folder / "krx_truth.json").exists()


def _cached_tree(tmp_path, fetched_at: str):
    root, build = tmp_path, tmp_path / f"{DATE}.building.1"
    (root / DATE).mkdir()
    build.mkdir()
    off = _official()
    off["fetched_at"] = fetched_at
    (root / DATE / "krx_truth.json").write_text(json.dumps(off, ensure_ascii=False), "utf-8")
    return root, build


def test_attach_refreshes_an_evening_cache_once_for_the_next_morning_build(tmp_path):
    """저녁에 받은 값은 다음날 확정 빌드가 한 번 다시 받는다. 다음날 받은 캐시는 그대로 쓴다(기준일당 최대 2회)."""
    root, build = _cached_tree(tmp_path, f"{DATE}T18:10:00+09:00")
    calls = []

    def fetch(code, date, **_k):
        calls.append(code)
        return _fake_fetch(code, date)
    kt.attach(_payload(), str(build), str(root), env=KRX_ENV, fetch=fetch, pause=0, now="2026-11-07T08:20:00+09:00")
    assert calls == ["STK", "KSQ"]
    assert json.loads((build / "krx_truth.json").read_text("utf-8"))["fetched_at"][:10] != DATE


def test_attach_keeps_the_cache_when_it_was_fetched_after_the_base_date(tmp_path):
    """다음날 받은 캐시는 다시 받지 않는다 — _no_fetch 를 타면 attach 가 '내부 오류' skip 으로 삼켜 status 가 갈린다."""
    root, build = _cached_tree(tmp_path, "2026-11-07T08:20:00+09:00")
    got = kt.attach(_payload(), str(build), str(root), env=KRX_ENV, fetch=_no_fetch, pause=0, now="2026-11-07T10:20:00+09:00")
    assert got["status"] == "ok"


def test_attach_keeps_the_same_evening_cache_on_the_same_day(tmp_path):
    """같은 날 저녁 재빌드는 다시 받지 않는다."""
    root, build = _cached_tree(tmp_path, f"{DATE}T18:10:00+09:00")
    got = kt.attach(_payload(), str(build), str(root), env=KRX_ENV, fetch=_no_fetch, pause=0, now=f"{DATE}T19:10:00+09:00")
    assert got["status"] == "ok"


def test_attach_falls_back_to_the_cache_when_the_refresh_fails(tmp_path):
    """다시 받기 실패가 멀쩡한 저녁 값을 SKIP 으로 지우면 안 된다."""
    root, build = _cached_tree(tmp_path, f"{DATE}T18:10:00+09:00")

    def dead(*_a, **_k):
        raise kt.KrxFetchError("망")
    got = kt.attach(_payload(), str(build), str(root), env=KRX_ENV, fetch=dead, pause=0, now="2026-11-07T08:20:00+09:00")
    assert got["status"] == "ok"


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
    assert "SKIP 공표치 대조" in capsys.readouterr().out


def _skip_day(root, day: str, text: str) -> None:
    (root / day).mkdir()
    (root / day / "VERIFY.txt").write_text(text, "utf-8")


def test_verifier_counts_consecutive_skips_and_warns_on_the_third(tmp_path, capsys):
    """연속 SKIP — 정규장 대조와 같은 규칙. 3회째 WARN(실패 아님), 성공한 날이 끊고, 표지 없는 날은 세지도 끊지도 않는다."""
    vr.WARNS.clear()
    build = tmp_path / f"{DATE}.building.1"
    build.mkdir()
    P = _payload()
    P["today"]["krx_truth"] = {"status": "skip", "reason": "망"}
    _skip_day(tmp_path, "2026-11-05", "       SKIP 공표치 대조 — 망\n")
    _skip_day(tmp_path, "2026-11-04", "표지 없는 날\n")
    _skip_day(tmp_path, "2026-11-03", "       SKIP 공표치 대조 — 망\n")
    _skip_day(tmp_path, "2026-11-02", "  [OK] T · 공표치 대조 — 오차가 공표 행에서 다시 나오는가\n")
    _skip_day(tmp_path, "2026-10-30", "       SKIP 공표치 대조 — 망\n")
    assert _verify(P, str(build)) == []
    assert any("공표치 대조 연속 SKIP 3회" in w for w in vr.WARNS)
    assert "[WARN]" in capsys.readouterr().out
    (tmp_path / "2026-11-03" / "VERIFY.txt").write_text("  [OK] T · 공표치 대조 — 오차가 공표 행에서 다시 나오는가\n", "utf-8")
    vr.WARNS.clear()
    assert _verify(P, str(build)) == []
    assert not vr.WARNS
    assert "공표치 대조 연속 SKIP 2회" in capsys.readouterr().out


def test_verifier_fails_when_published_values_leak_into_the_payload(tmp_path):
    """블록에 공표 수치가 있으면 실패다 — 화면·웹·trust 로 그대로 재배포된다."""
    P, folder = _built(tmp_path)
    P["today"]["krx_truth"]["markets"]["거래소"]["inst"]["official"] = 17000.0
    assert _verify(P, folder), "공표 수치가 새었는데 검증기가 초록이다"


def test_verifier_recomputes_the_market_total_and_the_chip_decision(tmp_path):
    """칩을 정하는 값(합계·hot)도 공표 행에서 다시 센다 — producer 가 hot 을 꺼도 초록이면 칩이 조용히 죽는다."""
    P, folder = _built(tmp_path)
    bent = json.loads(json.dumps(P))
    bent["today"]["krx_truth"]["markets"]["거래소"]["total_pct"] = 0.5
    assert _verify(bent, folder), "합계 오차를 틀리게 적었는데 초록이다"
    cold = json.loads(json.dumps(P))
    cold["today"]["krx_truth"]["markets"]["거래소"]["hot"] = True
    assert _verify(cold, folder), "칩 판정을 틀리게 적었는데 초록이다"
    flip = json.loads(json.dumps(P))
    flip["today"]["krx_truth"]["markets"]["거래소"]["inst"]["flip"] = True
    assert _verify(flip, folder), "부호 반대를 틀리게 적었는데 초록이다"


def test_verifier_accepts_a_naver_sidecar_without_the_etc_row(tmp_path):
    """네이버 사이드카엔 기타법인 행이 없다 — 없는 주체를 0 으로 대조하면 매일 빨개진다."""
    off = kt.fetch_official(DATE, ["거래소", "코스닥"], fetch=lambda _i, d: kt._parse_naver(NAVER_BODY, d), pause=0)
    P = _payload()
    P["today"]["krx_truth"] = kt.compare(P["today"], off)
    (tmp_path / "krx_truth.json").write_text(json.dumps(off, ensure_ascii=False), "utf-8")
    assert _verify(P, str(tmp_path)) == []


def test_verifier_is_silent_when_the_feature_is_off(tmp_path, capsys):
    """파일도 블록도 없는 리포트(기능 꺼짐)에는 검사도 SKIP 줄도 없다 — 선택 기능이 기본 출력을 늘리면 안 된다."""
    assert _verify(_payload(), str(tmp_path)) == []
    assert "공표치" not in capsys.readouterr().out


def test_verifier_flags_an_ok_block_whose_truth_file_vanished(tmp_path):
    """블록은 ok 인데 폴더에 krx_truth.json 이 없으면 실패다 — 재계산 근거가 사라진 리포트는 믿을 수 없다."""
    P = _payload()
    P["today"]["krx_truth"] = kt.compare(P["today"], _official())
    assert _verify(P, str(tmp_path)), "근거 파일이 없는데 검증기가 초록이다"


# ── 대량 체결 의심 — producer·검증기 ─────────────────────────────────────────

def _block_con(minutes=True):
    """대량 체결 픽스처 — BLK 는 거래량 90% 가 분봉 밖이고 금액이 크다, NET 은 순매수만 크고 분봉에 다 있다,
    OFF 는 분봉 밖이 크지만 금액이 작다(분모 게이트), NOMIN 은 금액이 큰데 분봉이 없다(판정 못 함)."""
    import sqlite3

    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE daily_bars(code TEXT, date TEXT, volume REAL, trade_value REAL, source TEXT)")
    con.execute("CREATE TABLE supply_demand(code TEXT, date TEXT, close REAL, individual REAL, foreign_ REAL, "
                "institution REAL, etc_corp REAL, source TEXT)")
    if minutes:
        con.execute("CREATE TABLE minute_bars(code TEXT, ts TEXT, volume REAL)")
    rows = {"BLK": (1000, 10000, -900, 900, 0), "NET": (1000, 10000, 500, -500, 0),
            "SMALL": (10, 10000, 2, -2, 0), "OFF": (10, 10000, 1, -1, 0), "NOMIN": (1000, 10000, 300, -300, 0),
            "F1": (10, 10000, 1, -1, 0), "F2": (10, 10000, 1, -1, 0), "F3": (10, 10000, 1, -1, 0)}
    for i, (c, (px, vol, ind, frg, inst)) in enumerate(rows.items()):
        con.execute("INSERT INTO daily_bars VALUES(?,?,?,?,?)", (c, DATE, vol, 1, "kiwoom"))
        con.execute("INSERT INTO supply_demand VALUES(?,?,?,?,?,?,?,?)", (c, DATE, px, ind * 1e4, frg * 1e4, inst, 0, "kiwoom"))
        if minutes and c != "NOMIN":
            vm = 1000 if c in ("BLK", "OFF") else vol
            con.execute("INSERT INTO minute_bars VALUES(?,?,?)", (c, f"{DATE} 09:0{i}:00", vm))
            con.execute("INSERT INTO minute_bars VALUES(?,?,?)", (c, "2026-11-07 09:00:00", 99999))   # 다른 날
    return con


def _block_df(con):
    import pandas as pd
    d = pd.read_sql_query("SELECT code, date, close, individual, foreign_, institution FROM supply_demand", con)
    d["market"] = "거래소"
    d["name"] = d["code"]
    for raw, short in (("individual", "indiv"), ("foreign_", "forgn"), ("institution", "inst")):
        d[short] = d[raw] * d["close"] / 1e8
    return d


sf = None


def _sf():
    global sf
    if sf is None:
        sf = _mod("sector_flow")
    return sf


def test_block_suspects_needs_both_off_book_share_and_the_amount_gate():
    """분봉 밖 몫과 금액 몫을 **둘 다** 넘어야 뜬다 — 순매수가 커도 분봉에 다 있으면 대량 체결이 아니고(NET), 분봉 밖이 커도
    금액이 작으면 시장 합계에 안 닿는다(OFF). 분봉 없는 종목은 빠뜨리지 않고 판정 못 함으로 센다(NOMIN)."""
    con = _block_con()
    got = _sf().block_suspects(con, _block_df(con), DATE, ["거래소"])
    assert got["status"] == "ok"
    assert [x["code"] for x in got["markets"]["거래소"]] == ["BLK"]
    x = got["markets"]["거래소"][0]
    assert x["off_pct"] == 90.0 and x["actor"] == "indiv" and x["amt"] == pytest.approx(-90.0, abs=0.1)
    assert got["unjudged"] == 1


def test_block_suspects_skips_when_it_cannot_judge():
    """표 없음·그날 분봉 없음·수집 미완은 0건이 아니라 skip 이다 — 모름과 없음을 가른다."""
    con = _block_con(minutes=False)
    assert "표" in _sf().block_suspects(con, _block_df(con), DATE, ["거래소"])["reason"]
    con = _block_con()
    con.execute("DELETE FROM minute_bars")
    assert "분봉이 없다" in _sf().block_suspects(con, _block_df(con), DATE, ["거래소"])["reason"]
    con = _block_con()
    con.execute("UPDATE minute_bars SET volume = 10")               # 분봉 합÷거래량 중앙 0.1% — 미완
    got = _sf().block_suspects(con, _block_df(con), DATE, ["거래소"])
    assert got["status"] == "skip" and "덜 끝났다" in got["reason"]


def _block_bars(con):
    return {c: (float(v), float(t)) for c, v, t in con.execute("SELECT code, volume, trade_value FROM daily_bars")}


def _f_block(P_today: dict, con) -> list[str]:
    vr.FAILS.clear()
    P = {"markets": ["거래소"], "dates": [DATE], "today": {"block_suspects": P_today}}
    meta = {c: ("거래소", "x") for c, in con.execute("SELECT code FROM daily_bars")}
    vr._f_block_suspects(P, con.cursor(), "?", meta, DATE, _block_bars(con))
    return list(vr.FAILS)


def test_verifier_rebuilds_the_block_list_from_the_db():
    """검증기가 분봉·수량·종가에서 목록을 다시 만든다 — 지어낸 종목·빠진 종목·틀린 판정 불가 수·skip 위장이 걸린다."""
    con = _block_con()
    good = _sf().block_suspects(con, _block_df(con), DATE, ["거래소"])
    assert _f_block(good, con) == []
    assert _f_block({**good, "markets": {"거래소": []}}, con), "빠진 종목을 못 잡았다"
    assert _f_block({**good, "markets": {"거래소": good["markets"]["거래소"] + [{"code": "NET"}]}}, con), "지어낸 종목을 못 잡았다"
    assert _f_block({**good, "unjudged": 0}, con), "판정 못 한 수를 못 잡았다"
    assert _f_block({"status": "skip", "reason": "x"}, con), "분봉이 멀쩡한데 skip 을 통과시켰다"


def test_verifier_block_list_is_quiet_on_old_reports_and_accepts_a_real_skip():
    """옛 리포트(블록 없음)는 침묵하고, 표가 없는 DB 의 skip 은 통과하며 뒤 검사가 돌게 트랜잭션을 되돌린다."""
    con = _block_con(minutes=False)
    vr.FAILS.clear()
    P = {"markets": ["거래소"], "dates": [DATE], "today": {}}
    vr._f_block_suspects(P, con.cursor(), "?", {}, DATE, _block_bars(con))
    assert vr.FAILS == []
    assert _f_block({"status": "skip", "reason": "분봉 표(minute_bars)가 없다"}, con) == []
    assert _f_block({"status": "ok", "markets": {"거래소": []}, "unjudged": 0}, con), "표가 없는데 ok 를 통과시켰다"


# ── TUI 도움말 ─────────────────────────────────────────────────────────────

def _fv():
    import sectorflow.tui.flow_view as fv  # noqa: PLC0415
    return fv


def test_help_tail_lists_the_measured_gaps_per_market():
    """도움말 끝 절 — '합계 x% · 기관 y% …(소스 꼬리표)'. 게이트 칸은 —, 부호 반대는 표지."""
    fv = _fv()
    P = _payload()
    P["today"]["krx_truth"] = kt.compare(P["today"], _official())
    text = "\n".join(t for _h, t in fv.krx_truth_help(P))
    assert "공표 시장값 대조" in text and "내부 대조용" in text and "합계 " in text
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
    assert "공표 시장값 대조" in text


def test_sector_flow_build_calls_attach_before_writing_outputs():
    """sector_flow.main 이 revision_stats 뒤·산출물 쓰기 전에 attach 를 부른다 — 둘 다 payload 에 실려야 HTML 이 본다."""
    src = (REPO / "scripts" / "sector_flow.py").read_text("utf-8")
    i_rev = src.index('payload["revision_stats"] = revision_stats(payload, root)')
    i_kt = src.index("_krx_truth().attach(payload,")
    i_out = src.index('json.dump(payload, f, ensure_ascii=False)')
    assert i_rev < i_kt < i_out
