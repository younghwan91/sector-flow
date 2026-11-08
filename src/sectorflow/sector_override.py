"""벤더 마스터(``stocks.sector``)가 비워 둔 종목의 섹터 — 이 레포에서만 덮는다.

마스터는 quant-airflow 소관이라 여기서 고칠 수 없다. 빈 섹터는 ``(미분류)`` 로 묶이는데, 2026-11-06 엔
그 바구니가 은행 넷(기업은행·카카오뱅크·케이뱅크·제주은행)이라 극단 목록에 "(미분류) 96" 이 떴다 —
섹터 이름이 아니라 결측의 이름이다. 근거가 분명한 것만 **명시적으로** 적는다(추정으로 메우지 않는다).

**데이터 보정이다** — 마스터가 섹터를 **비워 둔** 종목에만 쓴다. 마스터가 나중에 값을 채우면 그 값을 따르고
(덮어쓰지 않는다), 검증기 F 층이 그 사실을 경고로 적어 이 표를 지울 때를 알린다.

근거(출처·조회일): KRX 정보데이터시스템 업종 분류(2026-11-08 조회)에서 넷 다 은행업이고 KOSPI 업종지수 '금융' 에 든다. 이 마스터의 '금융' 섹터가 은행·
지주·리츠를 담는다(신한지주·KB금융 등). 나머지 빈 섹터 종목(해외 상장사 9xxxxx 등)은 근거가 없어 그대로 둔다 —
화면이 ``(미분류)`` 종목 수를 따로 적는다.

``sector_flow.load`` 와 검증기 F 층(SQL)이 **같은 표**를 쓴다 — 한쪽만 덮으면 F 가 섹터 칸마다 어긋난다.
"""

from __future__ import annotations

#: 종목코드 → 섹터. 값은 마스터에 이미 있는 섹터 이름이어야 한다(새 섹터를 만들지 않는다).
SECTOR_OVERRIDE: dict[str, str] = {
    "024110": "금융",   # 기업은행 — KRX 업종 은행(KOSPI 금융)
    "323410": "금융",   # 카카오뱅크 — KRX 업종 은행(KOSPI 금융)
    "279570": "금융",   # 케이뱅크 — KRX 업종 은행(KOSPI 금융), 2026 상장
    "006220": "금융",   # 제주은행 — KRX 업종 은행(KOSPI 금융)
}

UNCLASSIFIED = "(미분류)"
#: 표의 근거 — 화면·페이로드가 적는다.
SOURCE = "KRX 정보데이터시스템 업종 분류, 2026-11-08 조회"


def fingerprint(applied: dict) -> str:
    """실제로 덮은 (코드, 섹터) 의 지문 — 두 리포트가 같은 라벨로 셌는지 견줄 때 쓴다(직전 대비·잠정→확정)."""
    import hashlib
    body = ";".join(f"{c}={s}" for c, s in sorted(applied.items()))
    return hashlib.sha1(body.encode("utf-8")).hexdigest()[:12] if applied else "none"


def sector_sql(code_col: str = "st.code", sector_col: str = "st.sector") -> str:
    """SQL 의 섹터 식 — 마스터가 비었을 때만 덮어쓰기 → 그래도 비면 ``(미분류)``. 문자열은 위 상수에서만 온다."""
    base = f"coalesce(nullif({sector_col},''),'{UNCLASSIFIED}')"
    if not SECTOR_OVERRIDE:
        return base
    whens = " ".join(f"WHEN '{c}' THEN '{s}'" for c, s in sorted(SECTOR_OVERRIDE.items()))
    return (f"(CASE WHEN coalesce({sector_col},'') = '' THEN (CASE {code_col} {whens} ELSE '{UNCLASSIFIED}' END) "
            f"ELSE {sector_col} END)")
