"""hatch_build.py 를 hatchling 없이 들이는 흉내 — 진짜 hatchling 을 들이면 훅의 ``root`` 가 읽기 전용 속성이라
``test_packaging`` 의 훅 흉내가 깨진다. 두 검사가 같은 흉내를 쓴다."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def load_hatch_build():
    for m in ("hatchling", "hatchling.builders", "hatchling.builders.hooks", "hatchling.builders.hooks.plugin",
              "hatchling.metadata", "hatchling.metadata.plugin"):
        sys.modules.setdefault(m, types.ModuleType(m))
    build = types.ModuleType("hatchling.builders.hooks.plugin.interface")
    build.BuildHookInterface = type("BuildHookInterface", (), {})
    sys.modules.setdefault("hatchling.builders.hooks.plugin.interface", build)
    meta = types.ModuleType("hatchling.metadata.plugin.interface")
    meta.MetadataHookInterface = type("MetadataHookInterface", (), {})
    sys.modules.setdefault("hatchling.metadata.plugin.interface", meta)
    spec = importlib.util.spec_from_file_location("_hb", REPO / "hatch_build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
