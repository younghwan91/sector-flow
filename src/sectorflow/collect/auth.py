"""앱키 로드와 로그 마스킹 — 키·토큰이 화면·로그에 남지 않게.

변수 이름은 quant-airflow ``collectors/config.py`` 의 ``load_keys`` 규약(``KIWOOM_APP_KEY``·
``KIWOOM_APP_SECRET``)을 그대로 쓴다. 환경변수가 먼저, 없으면 ``.env`` 파일(값은 읽기만 하고 찍지 않는다).
OS 키체인은 Phase 2(Windows) 몫이다. DART 키(선택)는 ``DART_API_KEY``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

KEY_ENV = "KIWOOM_APP_KEY"
SECRET_ENV = "KIWOOM_APP_SECRET"
#: OpenDART 인증키(선택 — 주식수 이력). quant-airflow 와 같은 이름.
DART_ENV = "DART_API_KEY"


class MissingKeys(RuntimeError):
    """앱키나 시크릿이 없다 — 메시지에 값은 싣지 않는다."""


def _from_env_file(env_path: "str | Path | None", names: tuple[str, ...]) -> dict[str, str]:
    """``.env`` 형식 파일에서 ``names`` 의 값만(찍지 않는다)."""
    out: dict[str, str] = {}
    if not env_path or not Path(env_path).exists():
        return out
    for line in Path(env_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        name, value = line.split("=", 1)
        if name.strip() in names and name.strip() not in out:
            out[name.strip()] = value.strip().strip('"').strip("'")
    return out


def load_keys(env_path: "str | Path | None" = None) -> tuple[str, str]:
    """``(app_key, app_secret)``. 환경변수 → ``env_path``(.env 형식) 순서로 찾는다."""
    key = os.environ.get(KEY_ENV, "")
    secret = os.environ.get(SECRET_ENV, "")
    if not key or not secret:
        f = _from_env_file(env_path, (KEY_ENV, SECRET_ENV))
        key, secret = key or f.get(KEY_ENV, ""), secret or f.get(SECRET_ENV, "")
    if not key or not secret:
        raise MissingKeys(f"{KEY_ENV} / {SECRET_ENV} 가 환경변수나 .env 에 없다.")
    return key, secret


def load_dart_key(env_path: "str | Path | None" = None) -> str:
    """OpenDART 인증키. 환경변수 ``DART_API_KEY`` → ``env_path``. 없으면 :class:`MissingKeys`."""
    key = os.environ.get(DART_ENV, "") or _from_env_file(env_path, (DART_ENV,)).get(DART_ENV, "")
    if not key:
        raise MissingKeys(f"{DART_ENV} 가 환경변수나 .env 에 없다(OpenDART 가입으로 무료 발급).")
    return key


_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-+/=]+")
_JSON_SECRET = re.compile(r"""(?i)(["']?(?:token|appkey|secretkey|app_key|app_secret|authorization|crtfc_key)["']?"""
                          r"""\s*[:=]\s*["']?)[^"',\s}&]+""")


def mask(text: object, *secrets: str) -> str:
    """로그로 보내기 전 토큰·키 모양과 넘겨받은 비밀값을 가린다."""
    out = str(text)
    for s in secrets:
        if s:
            out = out.replace(s, "***")
    out = _BEARER.sub(r"\1***", out)
    return _JSON_SECRET.sub(r"\1***", out)
