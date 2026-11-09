"""TR 응답 → ``storage.SCHEMA`` 행. 동등성 시험이 kr_quant 와 숫자를 대조하므로 quant-airflow 파싱과 한 글자씩 같아야 한다."""

from __future__ import annotations

import pytest

from sectorflow.collect import fetch as F
from sectorflow.storage import INVESTOR_COLUMNS


def _sd_row(dt="20261106", **kw):
    row = {"dt": dt, "cur_prc": "-61200", "flu_rt": "-152", "acc_trde_qty": "123456",
           "ind_invsr": "+1500", "frgnr_invsr": "-2000", "orgn": "+300", "fnnc_invt": "+100",
           "insrnc": "", "invtrt": "+50", "bank": "0", "penfnd_etc": "+150", "samo_fund": "-",
           "natn": "0", "etc_corp": "+200"}
    row.update(kw)
    return row


def test_supply_demand_row_matches_quant_airflow_parsing():
    (rec,) = F.sd_records("000120", [_sd_row()])
    code, date, close, flu, qty, *inv, src = rec
    assert (code, date, close, flu, qty, src) == ("000120", "2026-11-06", 61200, -152.0, 123456, "kiwoom")
    got = dict(zip(INVESTOR_COLUMNS, inv))
    # 빈 값·'-' 는 0 (quant-airflow to_int 와 같다 — 0 은 "0 또는 미보고")
    assert got == {"individual": 1500, "foreign_": -2000, "institution": 300, "fnnc_invt": 100,
                   "insrnc": 0, "invtrt": 50, "bank": 0, "penfnd_etc": 150, "samo_fund": 0,
                   "natn": 0, "etc_corp": 200}


def test_records_drop_rows_older_than_the_window_and_blank_dates():
    rows = [_sd_row("20261106"), _sd_row("20250101"), _sd_row("")]
    assert [r[1] for r in F.sd_records("000120", rows, oldest="20250102")] == ["2026-11-06"]


def test_bar_and_event_rows():
    rows = [{"dt": "20261106", "open_pric": "+100", "high_pric": "+110", "low_pric": "-90",
             "cur_prc": "-105", "trde_qty": "1000", "trde_prica": "12", "upd_stkpc_tp": "", "upd_rt": ""},
            {"dt": "20260520", "open_pric": "10", "high_pric": "10", "low_pric": "10", "cur_prc": "10",
             "trde_qty": "1", "trde_prica": "0", "upd_stkpc_tp": "16", "upd_rt": "+900.00"}]
    bars = F.bar_records("078130", rows)
    assert bars[0] == ("078130", "2026-11-06", 100, 110, 90, 105, 1000, 12, "kiwoom")
    assert F.event_records("078130", rows) == [("078130", "2026-05-20", "16", 900.0)]


def test_index_rows_keep_name_and_abs_prices():
    rows = [{"dt": "20261106", "open_pric": "250000", "high_pric": "+251000", "low_pric": "-249000",
             "cur_prc": "-250500", "trde_qty": "5", "trde_prica": "6"}]
    assert F.index_records("019", "운송/창고", rows) == [
        ("019", "운송/창고", "2026-11-06", 250000, 251000, 249000, 250500, 5, 6)]


class _One:
    def __init__(self, body):
        self.body = body

    def request(self, api_id, body):
        return self.body, {}


def test_listed_shares_is_thousands_and_zero_is_unknown():
    assert F.listed_shares(_One({"flo_stk": "5846279"}), "005930") == 5_846_279_000
    assert F.listed_shares(_One({"flo_stk": ""}), "005930") is None
    assert F.listed_shares(_One({}), "005930") is None


def test_common_stock_filter():
    assert F.is_common_stock({"market": "거래소", "code": "005930"})
    assert not F.is_common_stock({"market": "거래소", "code": "005935"})   # 우선주
    assert not F.is_common_stock({"market": "ETF", "code": "069500"})


def test_iso_rejects_garbage():
    assert F.iso("20261106") == "2026-11-06"
    with pytest.raises(ValueError):
        F.iso("2026-11-06")


def test_shape_carries_no_values():
    s = F.shape({"token": "secret-ish", "list": [{"code": "005930", "name": "삼성전자"}]})
    assert "secret-ish" not in str(s) and "005930" not in str(s)
    assert s["list"] == {"len": 1, "row_keys": ["code", "name"]}
