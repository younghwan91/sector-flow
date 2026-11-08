"""앱키 로드와 로그 마스킹 — 키·토큰이 화면·로그에 남지 않게.

변수 이름은 quant-airflow ``collectors/config.py`` 의 ``load_keys`` 규약(``KIWOOM_APP_KEY``·
``KIWOOM_APP_SECRET``)을 그대로 쓴다. 환경변수가 먼저, 없으면 ``.env`` 파일(값은 읽기만 하고 찍지 않는다).
OS 키체인은 Phase 2(Windows) 몫이다.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

KEY_ENV = "KIWOOM_APP_KEY"
SECRET_ENV = "KIWOOM_APP_SECRET"


class MissingKeys(RuntimeError):
    """앱키나 시크릿이 없다 — 메시지에 값은 싣지 않는다."""


def load_keys(env_path: "str | Path | None" = None) -> tuple[str, str]:
    """``(app_key, app_secret)``. 환경변수 → ``env_path``(.env 형식) 순서로 찾는다."""
    key = os.environ.get(KEY_ENV, "")
    secret = os.environ.get(SECRET_ENV, "")
    if (not key or not secret) and env_path and Path(env_path).exists():
        for line in Path(env_path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            value = value.strip().strip('"').strip("'")
            if name.strip() == KEY_ENV and not key:
                key = value
            elif name.strip() == SECRET_ENV and not secret:
                secret = value
    if not key or not secret:
        raise MissingKeys(f"{KEY_ENV} / {SECRET_ENV} 가 환경변수나 .env 에 없다.")
    return key, secret


_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-+/=]+")
_JSON_SECRET = re.compile(r'(?i)("?(?:token|appkey|secretkey|app_key|app_secret|authorization)"?\s*[:=]\s*"?)'
                          r'[^",\s}]+')


def mask(text: object, *secrets: str) -> str:
    """로그로 보내기 전 토큰·키 모양과 넘겨받은 비밀값을 가린다."""
    out = str(text)
    for s in secrets:
        if s:
            out = out.replace(s, "***")
    out = _BEARER.sub(r"\1***", out)
    return _JSON_SECRET.sub(r"\1***", out)
