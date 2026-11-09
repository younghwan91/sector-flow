"""같은 앱키를 쓰는 다른 프로그램의 운영 창 — 그 창에서는 키움을 부르지 않는다.

키움은 앱키 하나에 토큰 하나다. 같은 키로 따로 로그인하면 앞 토큰이 무효가 된다(``8005``,
quant-airflow ``CLAUDE.md`` §4). 그래서 같은 키를 매매 봇·다른 수집기와 나눠 쓰면 **서로 죽인다.**

창은 **호스트별 설정 파일**이다(코드에 박지 않는다). BYO 사용자의 기본값은 빈 목록이다. 그 키를 다른 프로그램과
나눠 쓴다면 그 프로그램의 시간표를 여기에 적는다. 이 호스트(simnode)의 창은 ``deploy/kiwoom_busy.simnode.toml`` 이다.

검사는 시작할 때 한 번이 아니라 **요청(토큰 발급 포함) 직전마다** 한다(:class:`.kiwoom.KiwoomRest` 의 ``guard``).
긴 백필이 창 안으로 걸어 들어가면 그 요청을 보내지 않고 :class:`.kiwoom.BusyWindow` 로 멈춘다 — 끝난 종목은
커밋돼 있고 ``backfill --resume`` 이 이어 받는다.

파일 모양::

    [[window]]
    days = ["tue", "wed", "thu", "fri", "sat"]   # mon..sun
    start = "06:25"                               # KST, 포함
    end = "07:45"                                 # KST, 미포함
    why = "quant-airflow daily_finalize (30 6 * * 2-6), 실측 06:30~07:25"
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .kiwoom import BusyWindow

KST = timezone(timedelta(hours=9))
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
SHARED_KEY_WARNING = (
    "⚠️ 운영 창 설정이 없다 — 이 앱키를 매매 봇·다른 수집기와 같이 쓰면 서로의 토큰을 무효화한다(8005). "
    "같이 쓴다면 그 프로그램이 도는 시간을 --busy-file(또는 SECTORFLOW_BUSY_FILE)로 알려라.")


@dataclass(frozen=True)
class Window:
    days: frozenset[int]
    start: int  # 분(KST 0시 기준), 포함
    end: int    # 분, 미포함
    why: str = ""

    def contains(self, now: datetime) -> bool:
        now = now.astimezone(KST) if now.tzinfo else now
        m = now.hour * 60 + now.minute
        return now.weekday() in self.days and self.start <= m < self.end


def _minutes(hhmm: str) -> int:
    h, m = str(hhmm).split(":")
    v = int(h) * 60 + int(m)
    if not 0 <= v <= 24 * 60:
        raise ValueError(f"시각이 아니다: {hhmm!r}")
    return v


def load(path: "str | Path | None") -> list[Window]:
    """설정 파일 → 창 목록. ``None`` 이면 빈 목록(BYO 기본값)."""
    if not path:
        return []
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for w in data.get("window", []):
        days = frozenset(DAYS.index(str(d).lower()[:3]) for d in w["days"])
        start, end = _minutes(w["start"]), _minutes(w["end"])
        if not days or start >= end:
            raise ValueError(f"잘못된 창: {w}")
        out.append(Window(days, start, end, str(w.get("why", ""))))
    return out


def active(windows: list[Window], now: datetime) -> "Window | None":
    return next((w for w in windows if w.contains(now)), None)


def make_guard(windows: list[Window],
               now: Callable[[], datetime] = lambda: datetime.now(KST)) -> Callable[[], None]:
    """요청 직전마다 부를 검사 — 창 안이면 :class:`BusyWindow`."""
    def guard() -> None:
        t = now()
        w = active(windows, t)
        if w is not None:
            raise BusyWindow(f"{t:%a %H:%M} KST 는 같은 키를 쓰는 프로그램의 운영 창이다({w.why}) — "
                             f"요청을 보내지 않고 멈춘다. 창이 끝난 뒤 --resume 으로 이어 받아라.")
    return guard
