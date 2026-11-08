"""충실성 검사용: 원래 sector_flow.main() 을 기준일 컷오프만 넣어 통째로 돌린다. usage: fresh_build.py <기준일> <out.json>"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vcommon import FIRST_RUN_REV, out_dir, use_report_code  # noqa: E402

HERE = str(out_dir(__file__))
use_report_code(FIRST_RUN_REV, Path(HERE) / "report_code")  # 첫 검증의 리포트 코드(bd27017)
import sector_flow as sf  # noqa: E402
from long_load import con_ro  # noqa: E402
from sectorflow import storage  # noqa: E402

asof, out = sys.argv[1], sys.argv[2]
out = out if os.path.isabs(out) else os.path.join(HERE, out)
sf.recent_dates = lambda con, n: storage.recent_dates(con, n, asof)
sf.connect = lambda db: con_ro()
sys.argv = ["sector_flow.py", "--db", "x", "--json", out]
sf.main()
