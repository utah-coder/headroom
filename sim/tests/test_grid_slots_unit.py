"""Unit tests for GridSlot / build_slots, solar_share, write_processed and the
``python -m sim.grid`` sanity main (example-based)."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from sim.config import ALT_REGIONS, HOME_REGION, SLOTS_PER_HOUR, WINDOW_HOURS
from sim.grid import (
    HourlySeries,
    NoDataError,
    build_slots,
    main,
    solar_share,
    stress_flags,
    write_processed,
)
from sim.site import is_on_peak, tariff_per_kwh

UTC = timezone.utc
START = datetime(2026, 6, 1, 0, tzinfo=UTC)  # a Monday
H = timedelta(hours=1)
N = 72


def series(name, values, start=START):
    return HourlySeries(name, start, tuple(values))


def make_inputs(d=None, df=None):
    d = d if d is not None else [1000.0 + i for i in range(N)]
    df = df if df is not None else [2000.0 + i for i in range(N)]
    hourly = {
        "PACE.D": series("PACE.D", d),
        "PACE.DF": series("PACE.DF", df),
        "PACE.NG": series("PACE.NG", [900.0] * N),
        "PACE.TI": series("PACE.TI", [-50.0] * N),
        "PACE.SUN": series("PACE.SUN", [float(i % 24) for i in range(N)]),
        "PACE.TOTAL": series("PACE.TOTAL", [100.0] * N),
    }
    carbon = {
        "PACE": series("PACE.CI", [500.0 + i for i in range(N)]),
        "BPAT": series("BPAT.CI", [30.0 + i for i in range(N)]),
        "CISO": series("CISO.CI", [200.0 + i for i in range(N)]),
    }
    return hourly, carbon


# --- build_slots ---------------------------------------------------------------

def test_build_slots_expands_window_to_192_slots():
    hourly, carbon = make_inputs()
    start = START + 10 * H
    grid = build_slots(start, hourly, carbon)

    assert len(grid) == WINDOW_HOURS * SLOTS_PER_HOUR == 192
    assert [s.t for s in grid.slots] == [start + i * timedelta(minutes=15) for i in range(192)]
    for i, s in enumerate(grid.slots):
        h = 10 + i // SLOTS_PER_HOUR
        assert s.pace_demand_mw == 1000.0 + h
        assert s.pace_forecast_mw == 2000.0 + h
        assert s.carbon_g_per_kwh == 500.0 + h
        assert dict(s.alt_carbon) == {"BPAT": 30.0 + h, "CISO": 200.0 + h}
    assert grid.filled_hours == {"PACE.D": 0, "PACE.DF": 0,
                                 "PACE.CI": 0, "BPAT.CI": 0, "CISO.CI": 0}


def test_build_slots_window_and_end():
    hourly, carbon = make_inputs()
    grid = build_slots(START + 5 * H, hourly, carbon)
    assert grid.end == START + 53 * H
    assert grid.window == ("2026-06-01T05:00:00Z", "2026-06-03T05:00:00Z")


def test_build_slots_fills_only_window_gaps_and_counts_them():
    d = [1000.0] * N
    d[2] = None                 # outside the window starting at hour 10: not counted
    d[10] = None                # leading edge of the window -> nearest present value
    d[20] = 1000.0
    d[21] = None                # interior gap -> linear interpolation
    d[22] = None
    d[23] = 1300.0
    df = [2000.0] * N
    df[57] = None               # trailing edge (last window hour is 57)
    hourly, carbon = make_inputs(d=d, df=df)
    grid = build_slots(START + 10 * H, hourly, carbon)

    assert grid.filled_hours["PACE.D"] == 3
    assert grid.filled_hours["PACE.DF"] == 1

    def hour(k):  # slot values for window hour k (all four must agree)
        vals = {s.pace_demand_mw for s in grid.slots[k * 4:(k + 1) * 4]}
        assert len(vals) == 1
        return vals.pop()

    assert hour(0) == 1000.0
    assert hour(11) == pytest.approx(1100.0)
    assert hour(12) == pytest.approx(1200.0)
    assert hour(13) == 1300.0
    assert grid.slots[-1].pace_forecast_mw == 2000.0


def test_build_slots_attaches_tariff_and_on_peak():
    hourly, carbon = make_inputs()
    grid = build_slots(START, hourly, carbon)
    for s in grid.slots:
        assert s.price_per_kwh == tariff_per_kwh(s.t)
        assert s.on_peak == is_on_peak(s.t)
    # Monday 2026-06-01 15:00 MDT = 21:00Z is on-peak; 20:45Z (14:45 MDT) is not.
    by_t = {s.t: s for s in grid.slots}
    assert by_t[START + 21 * H].on_peak and by_t[START + 21 * H].price_per_kwh == 0.085
    assert not by_t[START + 20 * H + timedelta(minutes=45)].on_peak
    assert by_t[START + 20 * H + timedelta(minutes=45)].price_per_kwh == 0.045


def test_build_slots_stress_flags_from_demand():
    hourly, carbon = make_inputs()  # demand rises every hour
    grid = build_slots(START, hourly, carbon)
    demand = [s.pace_demand_mw for s in grid.slots]
    assert [s.stress for s in grid.slots] == stress_flags(demand)
    assert sum(s.stress for s in grid.slots) == 12  # the top three whole hours
    assert grid.stress_threshold_mw == 1045.0


def test_build_slots_all_missing_in_window_names_series_and_respondent():
    d = [1000.0] * N
    for i in range(10, 58):
        d[i] = None
    hourly, carbon = make_inputs(d=d)
    with pytest.raises(NoDataError, match=r"PACE\.D") as exc:
        build_slots(START + 10 * H, hourly, carbon)
    assert exc.value.series == "PACE.D"
    assert exc.value.respondent == "PACE"


def test_build_slots_missing_region_raises_no_data():
    hourly, carbon = make_inputs()
    del carbon[ALT_REGIONS[0]]
    with pytest.raises(NoDataError) as exc:
        build_slots(START, hourly, carbon)
    assert exc.value.series == f"{ALT_REGIONS[0]}.CI"
    assert exc.value.respondent == ALT_REGIONS[0]


def test_build_slots_rejects_bad_window_start():
    hourly, carbon = make_inputs()
    with pytest.raises(ValueError):
        build_slots(START + (N - WINDOW_HOURS + 1) * H, hourly, carbon)  # runs past the end
    with pytest.raises(ValueError):
        build_slots(START - H, hourly, carbon)
    with pytest.raises(ValueError):
        build_slots(START + timedelta(minutes=30), hourly, carbon)
    with pytest.raises(ValueError):
        build_slots(datetime(2026, 6, 1, 0), hourly, carbon)  # naive


# --- solar_share ---------------------------------------------------------------

def test_solar_share_skips_missing_and_clamps_negative():
    sun = [10.0] * 50
    total = [100.0] * 50
    sun[0] = -40.0     # counts as 0
    sun[1] = None      # hour skipped in both sums
    total[2] = None    # hour skipped in both sums
    share = solar_share(series("PACE.SUN", sun), series("PACE.TOTAL", total), START)
    # Window hours 0..47; hours 1 and 2 skipped; hour 0 contributes 0 sun.
    assert share == pytest.approx((45 * 10.0) / (46 * 100.0))


def test_solar_share_zero_total_is_none():
    assert solar_share(series("PACE.SUN", [1.0] * 48), series("PACE.TOTAL", [0.0] * 48),
                       START) is None


# --- write_processed -----------------------------------------------------------

def test_write_processed_writes_both_files(tmp_path):
    hourly, carbon = make_inputs()
    d = list(hourly["PACE.D"].values)
    d[3] = None
    d[4] = 1234.56
    hourly["PACE.D"] = series("PACE.D", d)
    del hourly["PACE.SUN"]
    carbon["PACE"] = series("PACE.CI", [-0.04] + [512.345] * (N - 1))

    out = tmp_path / "processed"
    paths = write_processed(hourly, carbon, out)
    assert paths == [out / "pace_hourly.json", out / "carbon_hourly.json"]
    assert sorted(p.name for p in out.iterdir()) == ["carbon_hourly.json", "pace_hourly.json"]

    pace = json.loads(paths[0].read_text(encoding="utf-8"))
    assert pace["start"] == "2026-06-01T00:00:00Z" and pace["hours"] == N
    assert len(pace["rows"]) == N
    assert list(pace["rows"][0]) == ["t", "demand_mw", "forecast_mw", "net_generation_mw",
                                     "interchange_mw", "solar_mw", "total_generation_mw"]
    assert pace["rows"][1]["t"] == "2026-06-01T01:00:00Z"
    assert pace["rows"][3]["demand_mw"] is None       # raw, not gap-filled
    assert pace["rows"][4]["demand_mw"] == 1234.6     # ROUND["mw"] = 1
    assert pace["rows"][0]["solar_mw"] is None        # series absent
    assert pace["rows"][0]["interchange_mw"] == -50.0

    ci = json.loads(paths[1].read_text(encoding="utf-8"))
    assert ci["note"] == "average intensity, not marginal"
    assert list(ci["rows"][0]) == ["t", HOME_REGION, *ALT_REGIONS]
    assert ci["rows"][0][HOME_REGION] == 0.0          # -0.04 rounds to 0.0, not -0.0
    assert "-0.0" not in paths[1].read_text(encoding="utf-8")
    assert ci["rows"][1][HOME_REGION] == 512.3


def test_write_processed_rejects_misaligned_series(tmp_path):
    hourly, carbon = make_inputs()
    carbon["BPAT"] = series("BPAT.CI", [1.0] * N, START + H)
    with pytest.raises(ValueError):
        write_processed(hourly, carbon, tmp_path)
    assert list(tmp_path.iterdir()) == []


# --- python -m sim.grid -----------------------------------------------------------

def test_main_without_raw_cache_fails_cleanly(tmp_path, capsys):
    out = tmp_path / "processed"
    assert main(cache_dir=tmp_path / "raw", out_dir=out) == 1
    err = capsys.readouterr().err
    assert "Raw_Cache file missing" in err
    assert not out.exists()
