"""wheel 빌드 훅 — 리포트 템플릿을 원격 글꼴 없이 싣는다(``src/sectorflow/offline_templates.py``).

레포 파일은 바꾸지 않는다. 빌드 임시 폴더에 떼어 낸 사본을 쓰고 ``force_include`` 로 파일 하나씩 넣는다.
편집 설치(editable)에서는 아무것도 하지 않는다 — 그때는 ``report_cli`` 가 레포 ``scripts/`` 를 돌린다.
"""

from __future__ import annotations

import importlib.util
import shutil
import tempfile
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


def _offline_module(root: Path):
    spec = importlib.util.spec_from_file_location(
        "_sf_offline", root / "src" / "sectorflow" / "offline_templates.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class OfflineTemplates(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict) -> None:
        if self.target_name != "wheel" or version == "editable":
            return
        root = Path(self.root)
        off = _offline_module(root)
        tmp = Path(tempfile.mkdtemp(prefix="sf-templates-"))
        self._tmp = tmp                 # finalize 가 지운다 — 빌드마다 임시 폴더가 쌓였다
        for name in off.TEMPLATES:
            src = (root / "scripts" / "templates" / name).read_text(encoding="utf-8")
            out = off.strip_remote(src)
            if off.AUTO_FETCH.search(out):
                raise RuntimeError(f"{name}: 원격 자원이 남았다 — offline_templates.strip_remote 를 고쳐라")
            (tmp / name).write_text(out, encoding="utf-8")
            build_data["force_include"][str(tmp / name)] = f"sectorflow/_scripts/templates/{name}"

    def finalize(self, version: str, build_data: dict, artifact_path: str) -> None:
        if getattr(self, "_tmp", None):
            shutil.rmtree(self._tmp, ignore_errors=True)
