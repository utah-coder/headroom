"""EIA-930 fetcher with a cache-first Raw_Cache under ``data/raw/``.

This is the only module that reads ``EIA_API_KEY`` (Requirement 6.1). The key
is read lazily, on the first cache miss only, so a fully cached run never
touches the environment variable or ``.env`` (Requirement 7.7).

Key hygiene (Requirements 6.9, 7.8, 7.15):

- ``base_params`` never contains ``api_key``. The key is appended only inside
  ``_send()``, and the URL carrying it is never printed, stored or put in an
  exception message.
- Before a response is cached, ``request.params.api_key`` is removed, the
  serialized text is checked for the key, and the file is written to a temp
  path and then moved into place with ``os.replace``. A failed request writes
  nothing.
- Transport failures are re-raised as ``EiaFetchError`` with key-free text and
  without exception chaining, so a traceback cannot show the keyed URL.

Run once on the developer machine with ``python -m sim.fetch_eia``. Tests and
CI only read the committed cache; ``offline=True`` turns a cache miss into
``CacheMissingError(path)`` instead of a network request (Requirement 23.11).

``load_all()`` returns a dict keyed ``"<endpoint>:<respondent>"``, for example
``"region-data:PACE"`` and ``"fuel-type-data:BPAT"``, each holding the
concatenated rows of every page in order.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Union

from sim.config import (
    ALT_REGIONS,
    DATA_END,
    DATA_START,
    EIA_BASE,
    EIA_PAGE_LENGTH,
    HOME_REGION,
    REGION_TYPES,
)

__all__ = [
    "ENDPOINTS",
    "KEY_ENV_VAR",
    "RequestSpec",
    "MissingKeyError",
    "EiaFetchError",
    "CacheMissingError",
    "base_params",
    "endpoint_url",
    "cache_path",
    "read_api_key",
    "urllib_transport",
    "fetch_page",
    "fetch_series",
    "series_specs",
    "load_all",
    "main",
]

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = REPO_ROOT / "data" / "raw"
DOTENV_PATH = REPO_ROOT / ".env"

KEY_ENV_VAR = "EIA_API_KEY"
ENDPOINTS = ("region-data", "fuel-type-data")
HTTP_TIMEOUT_S = 30  # stdlib urllib timeout per request

# (url, timeout_seconds) -> (http_status, body_bytes). Connection failures raise.
Transport = Callable[[str, float], tuple[int, bytes]]
KeySource = Union[str, Callable[[], str], None]


class MissingKeyError(RuntimeError):
    """EIA_API_KEY is unset or blank in both the environment and ``.env``."""

    exit_code = 2

    def __init__(self) -> None:
        super().__init__(
            f"{KEY_ENV_VAR} is missing: set it in the environment or in .env at the repo root"
        )


class EiaFetchError(RuntimeError):
    """An EIA request failed. Carries only key-free information."""

    exit_code = 1

    def __init__(self, endpoint: str, params: list[tuple[str, str]], error: str) -> None:
        self.endpoint = endpoint
        self.params = list(params)
        self.error = error
        super().__init__(
            f"EIA request failed: endpoint={endpoint} params={self.params} error={error}"
        )


class CacheMissingError(RuntimeError):
    """A required Raw_Cache file is missing (offline mode) or unreadable."""

    exit_code = 1

    def __init__(self, path: Path, detail: str = "missing") -> None:
        self.path = Path(path)
        self.detail = detail
        super().__init__(f"Raw_Cache file {detail}: {self.path}")


@dataclass(frozen=True)
class RequestSpec:
    endpoint: str              # "region-data" or "fuel-type-data"
    respondent: str
    types: tuple = ()          # () for fuel-type-data (all fuels)
    offset: int = 0

    def __post_init__(self) -> None:
        if self.endpoint not in ENDPOINTS:
            raise ValueError(f"unknown EIA endpoint: {self.endpoint!r}")
        if not self.respondent:
            raise ValueError("respondent must be non-empty")
        if self.endpoint == "fuel-type-data" and self.types:
            raise ValueError("fuel-type-data takes no type facets")
        if self.offset < 0:
            raise ValueError(f"offset must be non-negative: {self.offset}")

    def at_offset(self, offset: int) -> "RequestSpec":
        return RequestSpec(self.endpoint, self.respondent, tuple(self.types), offset)


def endpoint_url(spec: RequestSpec) -> str:
    return f"{EIA_BASE}/{spec.endpoint}/data/"


def base_params(spec: RequestSpec) -> list[tuple[str, str]]:
    """Request parameters for ``spec``. Never contains ``api_key``."""
    params: list[tuple[str, str]] = [
        ("frequency", "hourly"),
        ("data[0]", "value"),
        ("facets[respondent][]", spec.respondent),
    ]
    params += [("facets[type][]", t) for t in spec.types]
    params += [
        ("start", DATA_START),
        ("end", DATA_END),
        ("sort[0][column]", "period"),
        ("sort[0][direction]", "asc"),
        ("offset", str(spec.offset)),
        ("length", str(EIA_PAGE_LENGTH)),
    ]
    return params


def cache_path(spec: RequestSpec, cache_dir: Optional[Path] = None) -> Path:
    """``<cache_dir>/<endpoint>__<respondent>__<types or all>__o<offset:06d>.json``."""
    facets = "-".join(spec.types) if spec.types else "all"
    root = Path(cache_dir) if cache_dir is not None else RAW_DIR
    return root / f"{spec.endpoint}__{spec.respondent}__{facets}__o{spec.offset:06d}.json"


# --- API key ---------------------------------------------------------------


def _read_dotenv_value(path: Path, name: str) -> Optional[str]:
    """Tiny KEY=VALUE reader. Returns the last value for ``name`` or None."""
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        return None
    value = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k.startswith("export "):
            k = k[len("export "):].strip()
        if k != name:
            continue
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        value = v
    return value


def read_api_key(dotenv_path: Optional[Path] = None) -> str:
    """Environment variable first, then the repo-root ``.env``.

    Unset, empty or whitespace-only in both raises ``MissingKeyError``.
    """
    env_value = os.environ.get(KEY_ENV_VAR)
    if env_value is not None and env_value.strip():
        return env_value.strip()
    file_value = _read_dotenv_value(dotenv_path if dotenv_path is not None else DOTENV_PATH,
                                    KEY_ENV_VAR)
    if file_value is not None and file_value.strip():
        return file_value.strip()
    raise MissingKeyError()


class _LazyKey:
    """Resolves the key at most once, and only when first asked."""

    def __init__(self, source: KeySource = None, dotenv_path: Optional[Path] = None) -> None:
        self._source = source
        self._dotenv_path = dotenv_path
        self._key: Optional[str] = None

    def get(self) -> str:
        if self._key is None:
            if isinstance(self._source, str):
                key = self._source
            elif callable(self._source):
                key = self._source()
            else:
                key = read_api_key(self._dotenv_path)
            if not key or not key.strip():
                raise MissingKeyError()
            self._key = key
        return self._key


# --- Transport -------------------------------------------------------------


def urllib_transport(url: str, timeout: float) -> tuple[int, bytes]:
    """Default transport: stdlib urllib. HTTP errors return their status and body."""
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "headroom-sim"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https base)
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as e:
        try:
            body = e.read()
        except Exception:  # pragma: no cover - defensive
            body = b""
        return int(e.code), body


def _scrub(text: str, key: str) -> str:
    return text.replace(key, "[redacted]") if key else text


def _send(spec: RequestSpec, key: str, transport: Transport) -> dict:
    """Make one request. The only place ``api_key`` is added to the parameters."""
    endpoint = endpoint_url(spec)
    params = base_params(spec)
    url = endpoint + "?" + urllib.parse.urlencode(params + [("api_key", key)])

    def fail(error: str) -> EiaFetchError:
        return EiaFetchError(endpoint, params, _scrub(error, key)[:500])

    try:
        status, body = transport(url, HTTP_TIMEOUT_S)
    except Exception as e:  # connect failure, timeout, anything else
        raise fail(f"{type(e).__name__}: {e}") from None

    text = body.decode("utf-8", errors="replace") if isinstance(body, (bytes, bytearray)) else str(body)
    if not 200 <= int(status) < 300:
        raise fail(f"HTTP {status}: {text}")
    try:
        payload = json.loads(text)
    except ValueError as e:
        raise fail(f"invalid JSON response: {e}") from None
    if not isinstance(payload, dict):
        raise fail("response body is not a JSON object")
    if payload.get("error"):
        raise fail(f"API error: {payload.get('error')}")
    response = payload.get("response")
    if not isinstance(response, dict) or not isinstance(response.get("data"), list):
        raise fail("response.data missing or not a list")
    return payload


# --- Cache -----------------------------------------------------------------


def _strip_key(payload: dict) -> None:
    request = payload.get("request")
    if isinstance(request, dict):
        params = request.get("params")
        if isinstance(params, dict):
            params.pop("api_key", None)


def _write_cache(path: Path, payload: dict, key: str, spec: RequestSpec) -> None:
    _strip_key(payload)
    text = json.dumps(payload, ensure_ascii=False)
    if key and key in text:
        raise EiaFetchError(endpoint_url(spec), base_params(spec),
                            "response still contains the API key after stripping; not cached")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


def _read_cache(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload["response"]["data"]
        if not isinstance(rows, list):
            raise TypeError("response.data is not a list")
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise CacheMissingError(path, f"unreadable ({type(e).__name__})") from None
    return payload


def fetch_page(
    spec: RequestSpec,
    *,
    cache_dir: Optional[Path] = None,
    transport: Optional[Transport] = None,
    key: Union[KeySource, _LazyKey] = None,
    offline: bool = False,
) -> dict:
    """Return the response for ``spec``: from the cache if present, else from the API."""
    path = cache_path(spec, cache_dir)
    if path.exists():
        return _read_cache(path)
    if offline:
        raise CacheMissingError(path)
    lazy = key if isinstance(key, _LazyKey) else _LazyKey(key)
    api_key = lazy.get()  # MissingKeyError before any request
    payload = _send(spec, api_key, transport or urllib_transport)
    _write_cache(path, payload, api_key, spec)
    return payload


def fetch_series(
    first: RequestSpec,
    *,
    cache_dir: Optional[Path] = None,
    transport: Optional[Transport] = None,
    key: Union[KeySource, _LazyKey] = None,
    offline: bool = False,
    on_page: Optional[Callable[[Path, int], None]] = None,
) -> list[dict]:
    """All rows for a series: offsets step by EIA_PAGE_LENGTH until a short page."""
    lazy = key if isinstance(key, _LazyKey) else _LazyKey(key)
    rows: list[dict] = []
    spec = first
    while True:
        page = fetch_page(spec, cache_dir=cache_dir, transport=transport, key=lazy,
                          offline=offline)
        data = page["response"]["data"]
        rows.extend(data)
        if on_page is not None:
            on_page(cache_path(spec, cache_dir), len(data))
        if len(data) < EIA_PAGE_LENGTH:
            return rows
        spec = spec.at_offset(spec.offset + EIA_PAGE_LENGTH)


def series_specs() -> list[RequestSpec]:
    """PACE region-data (D, DF, NG, TI) and fuel-type-data for PACE, BPAT, CISO."""
    specs = [RequestSpec("region-data", HOME_REGION, tuple(REGION_TYPES), 0)]
    specs += [RequestSpec("fuel-type-data", r, (), 0) for r in (HOME_REGION, *ALT_REGIONS)]
    return specs


def load_all(
    *,
    cache_dir: Optional[Path] = None,
    transport: Optional[Transport] = None,
    key: KeySource = None,
    offline: bool = False,
    dotenv_path: Optional[Path] = None,
    on_page: Optional[Callable[[Path, int], None]] = None,
) -> dict[str, list[dict]]:
    """Rows for every series over the Data_Range, keyed ``"<endpoint>:<respondent>"``."""
    lazy = _LazyKey(key, dotenv_path)
    out: dict[str, list[dict]] = {}
    for spec in series_specs():
        out[f"{spec.endpoint}:{spec.respondent}"] = fetch_series(
            spec, cache_dir=cache_dir, transport=transport, key=lazy, offline=offline,
            on_page=on_page,
        )
    return out


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sim.fetch_eia",
                                     description="Fill data/raw/ from the EIA API (cache-first).")
    parser.add_argument("--offline", action="store_true",
                        help="never fetch; fail if a cache file is missing")
    args = parser.parse_args(argv)

    def report(path: Path, n: int) -> None:
        print(f"{path.name}: {n} rows")

    try:
        raw = load_all(offline=args.offline, on_page=report)
    except MissingKeyError as e:
        print(f"error: {e}", file=sys.stderr)
        return MissingKeyError.exit_code
    except (EiaFetchError, CacheMissingError) as e:
        print(f"error: {e}", file=sys.stderr)
        return e.exit_code
    for name, rows in raw.items():
        print(f"{name}: {len(rows)} rows total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
