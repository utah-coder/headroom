"""Example-based unit tests for sim/fetch_eia.py.

Every test uses a fake transport, ``tmp_path`` for the cache and ``.env``, and
dummy keys only. The real ``.env`` and ``data/raw`` are never touched: the
autouse fixture below repoints the module defaults at ``tmp_path``.
"""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

import pytest

from sim import fetch_eia as fe
from sim.config import DATA_END, DATA_START, EIA_PAGE_LENGTH

DUMMY_KEY = "dummy-KEY-0123456789abcdef"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    dotenv = tmp_path / ".env"
    monkeypatch.setattr(fe, "RAW_DIR", raw)
    monkeypatch.setattr(fe, "DOTENV_PATH", dotenv)
    monkeypatch.delenv(fe.KEY_ENV_VAR, raising=False)

    def no_default_transport(url, timeout):
        raise AssertionError("default transport must not be used in tests")

    monkeypatch.setattr(fe, "urllib_transport", no_default_transport)
    return raw, dotenv


class FakeTransport:
    """Serves ``total`` synthetic rows in pages, echoing the key like EIA does."""

    def __init__(self, total=0, status=200, body=None, exc=None):
        self.total = total
        self.status = status
        self.body = body
        self.exc = exc
        self.calls: list[dict] = []

    def __call__(self, url, timeout):
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.calls.append({"url": url, "timeout": timeout, "query": query})
        if self.exc is not None:
            raise self.exc
        if self.body is not None:
            return self.status, self.body
        offset, length = int(query["offset"]), int(query["length"])
        rows = [{"period": f"r{i}", "value": str(i)} for i in range(offset, min(offset + length, self.total))]
        payload = {
            "response": {"total": str(self.total), "data": rows},
            "request": {"command": "/v2/electricity/rto/x/data/",
                        "params": {"api_key": query.get("api_key"), "offset": offset}},
            "apiVersion": "2.1.0",
        }
        return self.status, json.dumps(payload).encode()


def raising_transport(url, timeout):
    raise AssertionError("transport must not be called")


REGION = fe.RequestSpec("region-data", "PACE", ("D", "DF", "NG", "TI"), 0)
FUEL_BPAT = fe.RequestSpec("fuel-type-data", "BPAT", (), 10000)


# --- params and naming ------------------------------------------------------


def test_base_params_region_data():
    assert fe.base_params(REGION) == [
        ("frequency", "hourly"),
        ("data[0]", "value"),
        ("facets[respondent][]", "PACE"),
        ("facets[type][]", "D"),
        ("facets[type][]", "DF"),
        ("facets[type][]", "NG"),
        ("facets[type][]", "TI"),
        ("start", "2026-06-01T00"),
        ("end", "2026-09-30T23"),
        ("sort[0][column]", "period"),
        ("sort[0][direction]", "asc"),
        ("offset", "0"),
        ("length", "5000"),
    ]
    assert fe.endpoint_url(REGION) == "https://api.eia.gov/v2/electricity/rto/region-data/data/"


def test_base_params_fuel_type_data_has_no_type_facet_or_key():
    params = fe.base_params(FUEL_BPAT)
    names = [k for k, _ in params]
    assert "facets[type][]" not in names and "api_key" not in names
    assert ("facets[respondent][]", "BPAT") in params
    assert ("start", DATA_START) in params and ("end", DATA_END) in params
    assert ("offset", "10000") in params
    assert fe.endpoint_url(FUEL_BPAT).endswith("/fuel-type-data/data/")


def test_cache_path_naming_matches_design(tmp_path):
    assert fe.cache_path(REGION, tmp_path).name == "region-data__PACE__D-DF-NG-TI__o000000.json"
    assert fe.cache_path(REGION.at_offset(5000), tmp_path).name == "region-data__PACE__D-DF-NG-TI__o005000.json"
    assert fe.cache_path(FUEL_BPAT, tmp_path).name == "fuel-type-data__BPAT__all__o010000.json"


def test_series_specs_cover_pace_region_and_three_fuel_mixes():
    assert [(s.endpoint, s.respondent, s.types) for s in fe.series_specs()] == [
        ("region-data", "PACE", ("D", "DF", "NG", "TI")),
        ("fuel-type-data", "PACE", ()),
        ("fuel-type-data", "BPAT", ()),
        ("fuel-type-data", "CISO", ()),
    ]


# --- key resolution ---------------------------------------------------------


def test_key_env_var_takes_precedence(monkeypatch, isolated):
    _, dotenv = isolated
    dotenv.write_text("EIA_API_KEY=from-dotenv\n")
    monkeypatch.setenv("EIA_API_KEY", "from-env")
    assert fe.read_api_key() == "from-env"


def test_key_falls_back_to_dotenv_when_env_blank(monkeypatch, isolated):
    _, dotenv = isolated
    dotenv.write_text("# comment\nOTHER=x\nEIA_API_KEY = \"from-dotenv\"\n")
    monkeypatch.setenv("EIA_API_KEY", "   ")
    assert fe.read_api_key() == "from-dotenv"


@pytest.mark.parametrize("dotenv_text", [None, "", "EIA_API_KEY=\n", "EIA_API_KEY=   \n"])
def test_key_blank_everywhere_raises_missing_key(isolated, dotenv_text):
    _, dotenv = isolated
    if dotenv_text is not None:
        dotenv.write_text(dotenv_text)
    with pytest.raises(fe.MissingKeyError) as ei:
        fe.read_api_key()
    assert "EIA_API_KEY" in str(ei.value)
    assert ei.value.exit_code == 2


def test_missing_key_on_cache_miss_sends_nothing_writes_nothing_exits_2(monkeypatch, isolated):
    raw, _ = isolated
    fake = FakeTransport(total=3)
    monkeypatch.setattr(fe, "urllib_transport", fake)
    assert fe.main([]) == 2
    assert fake.calls == []
    assert not raw.exists() or list(raw.iterdir()) == []


# --- pagination, caching, key stripping ------------------------------------


def test_pagination_steps_by_page_length_until_short_page(tmp_path):
    fake = FakeTransport(total=2 * EIA_PAGE_LENGTH + 7)
    rows = fe.fetch_series(REGION, cache_dir=tmp_path, transport=fake, key=DUMMY_KEY)
    assert [c["query"]["offset"] for c in fake.calls] == ["0", "5000", "10000"]
    assert [r["period"] for r in rows] == [f"r{i}" for i in range(2 * EIA_PAGE_LENGTH + 7)]
    assert all(c["timeout"] == 30 for c in fake.calls)


def test_pagination_requests_trailing_empty_page_on_exact_multiple(tmp_path):
    fake = FakeTransport(total=EIA_PAGE_LENGTH)
    rows = fe.fetch_series(REGION, cache_dir=tmp_path, transport=fake, key=DUMMY_KEY)
    assert [c["query"]["offset"] for c in fake.calls] == ["0", "5000"]
    assert len(rows) == EIA_PAGE_LENGTH
    assert fe.cache_path(REGION.at_offset(5000), tmp_path).exists()


def test_key_only_in_sent_url_and_stripped_from_cache(tmp_path):
    fake = FakeTransport(total=3)
    page = fe.fetch_page(REGION, cache_dir=tmp_path, transport=fake, key=DUMMY_KEY)
    assert fake.calls[0]["query"]["api_key"] == DUMMY_KEY
    path = fe.cache_path(REGION, tmp_path)
    text = path.read_text(encoding="utf-8")
    assert DUMMY_KEY not in text
    cached = json.loads(text)
    assert "api_key" not in cached["request"]["params"]
    assert cached["response"]["data"] == [{"period": f"r{i}", "value": str(i)} for i in range(3)]
    assert page["response"]["data"] == cached["response"]["data"]
    assert not path.with_name(path.name + ".tmp").exists()


def test_cache_hit_makes_zero_requests_and_never_reads_key(tmp_path, monkeypatch):
    fe.load_all(cache_dir=tmp_path, transport=FakeTransport(total=4), key=DUMMY_KEY)

    def boom(*a, **k):
        raise AssertionError("key must not be read on a fully cached run")

    monkeypatch.setattr(fe, "read_api_key", boom)
    raw = fe.load_all(cache_dir=tmp_path, transport=raising_transport)
    assert sorted(raw) == ["fuel-type-data:BPAT", "fuel-type-data:CISO",
                           "fuel-type-data:PACE", "region-data:PACE"]
    assert all(len(rows) == 4 for rows in raw.values())


def test_key_read_once_lazily_across_misses(tmp_path):
    calls = []

    def key_source():
        calls.append(1)
        return DUMMY_KEY

    fe.load_all(cache_dir=tmp_path, transport=FakeTransport(total=1), key=key_source)
    assert calls == [1]


# --- errors -----------------------------------------------------------------


@pytest.mark.parametrize("fake", [
    FakeTransport(status=500, body=f"Internal error for api_key={DUMMY_KEY}".encode()),
    FakeTransport(status=200, body=json.dumps({"error": f"bad key {DUMMY_KEY}", "code": 403}).encode()),
    FakeTransport(exc=ConnectionError(f"could not reach https://x/?api_key={DUMMY_KEY}")),
    FakeTransport(status=200, body=b"not json"),
], ids=["http-500", "error-body", "connect-error", "bad-json"])
def test_errors_raise_key_free_and_write_no_cache(tmp_path, fake):
    with pytest.raises(fe.EiaFetchError) as ei:
        fe.fetch_page(REGION, cache_dir=tmp_path, transport=fake, key=DUMMY_KEY)
    err = ei.value
    assert err.endpoint == fe.endpoint_url(REGION)
    assert err.params == fe.base_params(REGION)
    assert err.error
    assert DUMMY_KEY not in str(err) and DUMMY_KEY not in err.error
    # No chained exception that could carry the keyed URL into a traceback.
    assert err.__cause__ is None
    assert err.__context__ is None or err.__suppress_context__
    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []


def test_main_exits_nonzero_on_fetch_error_without_leaking(monkeypatch, isolated, capsys):
    raw, _ = isolated
    monkeypatch.setenv("EIA_API_KEY", DUMMY_KEY)
    monkeypatch.setattr(fe, "urllib_transport", FakeTransport(status=500, body=DUMMY_KEY.encode()))
    assert fe.main([]) == 1
    out = capsys.readouterr()
    assert DUMMY_KEY not in out.out and DUMMY_KEY not in out.err
    assert "region-data" in out.err
    assert not raw.exists() or list(raw.iterdir()) == []


def test_main_success_prints_no_key(monkeypatch, isolated, capsys):
    raw, _ = isolated
    monkeypatch.setenv("EIA_API_KEY", DUMMY_KEY)
    monkeypatch.setattr(fe, "urllib_transport", FakeTransport(total=2))
    assert fe.main([]) == 0
    out = capsys.readouterr()
    assert DUMMY_KEY not in out.out + out.err
    assert len(list(raw.glob("*.json"))) == 4
    assert all(DUMMY_KEY not in p.read_text(encoding="utf-8") for p in raw.glob("*.json"))


# --- offline mode -----------------------------------------------------------


def test_offline_missing_cache_names_path_and_never_fetches(tmp_path):
    with pytest.raises(fe.CacheMissingError) as ei:
        fe.fetch_page(REGION, cache_dir=tmp_path, transport=raising_transport, offline=True)
    expected = fe.cache_path(REGION, tmp_path)
    assert ei.value.path == expected
    assert str(expected) in str(ei.value)


def test_unreadable_cache_file_names_path(tmp_path):
    path = fe.cache_path(REGION, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(fe.CacheMissingError) as ei:
        fe.fetch_page(REGION, cache_dir=tmp_path, transport=raising_transport)
    assert str(path) in str(ei.value)
