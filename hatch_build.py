"""빌드 훅 둘 — wheel 에 리포트 템플릿을 원격 글꼴 없이 싣고(``src/sectorflow/offline_templates.py``),
PyPI 설명(README)의 상대 링크를 GitHub 절대 주소로 바꾼다(PyPI 는 상대 경로 그림·링크를 풀지 못한다).

레포 파일은 바꾸지 않는다. 빌드 임시 폴더에 떼어 낸 사본을 쓰고 ``force_include`` 로 파일 하나씩 넣는다.
편집 설치(editable)에서는 아무것도 하지 않는다 — 그때는 ``report_cli`` 가 레포 ``scripts/`` 를 돌린다.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import tempfile
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface
from hatchling.metadata.plugin.interface import MetadataHookInterface

REPO_URL = "https://github.com/younghwan91/sector-flow"
RAW_URL = "https://raw.githubusercontent.com/younghwan91/sector-flow/main"
_IMAGE = re.compile(r"\.(png|gif|svg|jpe?g|webp)$", re.I)
#: ``](경로``·``src="경로"``·``srcset="경로"`` — 주소(``scheme:``)·앵커(``#``)·절대 경로(``/``)는 그대로 둔다.
_REL = re.compile(r'(\]\(|\bsrc="|\bsrcset=")(?![a-z][a-z0-9+.-]*:|#|/)([^)"\s]+)')


def absolutize(md: str) -> str:
    """README 의 상대 링크를 절대 주소로 — 그림은 raw, 나머지는 blob/main."""
    def sub(m: re.Match) -> str:
        path = m.group(2)
        bare = path.split("#", 1)[0]
        base = RAW_URL if _IMAGE.search(bare) else f"{REPO_URL}/blob/main"
        return f"{m.group(1)}{base}/{path}"
    return _REL.sub(sub, md)


class PypiReadme(MetadataHookInterface):
    PLUGIN_NAME = "custom"

    def update(self, metadata: dict) -> None:
        text = (Path(self.root) / "README.md").read_text(encoding="utf-8")
        metadata["readme"] = {"content-type": "text/markdown", "text": absolutize(text)}


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
