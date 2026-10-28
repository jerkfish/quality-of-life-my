"""Fetch upstream data.

Two transports, both deliberately chosen in Phase 0:

*   **Bulk parquet** from ``storage.dosm.gov.my`` / ``storage.data.gov.my``. This is how we
    get the actual data. The REST API is limited to 4 requests/minute and is missing several
    datasets entirely, so it is unusable as a primary source.

*   **Catalogue page metadata** from ``open.dosm.gov.my`` / ``data.gov.my``. Each page inlines
    a ``__NEXT_DATA__`` JSON blob containing ``last_updated``, ``data_as_of``, ``next_update``,
    the methodology note, the caveat, and (for editioned datasets) the list of available
    editions. The REST API exposes some of this via ``?meta=true`` but costs quota, and is
    wrong for the datasets that 404 there.

Everything is cached on disk and content-hashed so a re-run can tell "nothing changed" from
"the numbers moved".
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path

import httpx
from pydantic import BaseModel, Field

from . import registry
from .registry import PRICECATCHER_URL, Dataset

log = logging.getLogger(__name__)

USER_AGENT = (
    "col-check/0.1 (+https://github.com/jerkfish/quality-of-life-my) "
    "open-data pipeline; contact via repository issues"
)
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)
MAX_ATTEMPTS = 4


class FetchError(RuntimeError):
    pass


class CatalogueMeta(BaseModel):
    """The subset of the catalogue page's metadata we rely on."""

    id: str
    title: str = ""
    description: str = ""
    frequency: str | None = None
    data_as_of: str | None = None
    last_updated: str | None = None
    next_update: str | None = None
    data_source: list[str] = Field(default_factory=list)
    methodology: str = ""
    caveat: str = ""
    link_parquet: str | None = None
    editions: list[str] = Field(default_factory=list)
    source_base: str = ""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Fetcher:
    """Cached, polite downloader.

    Not concurrent. The volumes here are kilobytes, the upstream is a public good, and a
    burst of parallel requests buys nothing but risk.
    """

    def __init__(self, raw_dir: Path, meta_dir: Path, *, offline: bool = False) -> None:
        self.raw_dir = Path(raw_dir)
        self.meta_dir = Path(meta_dir)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        self.offline = offline
        self.client = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=httpx.Timeout(60.0, connect=20.0),
            follow_redirects=True,
        )
        self._etags: dict[str, str] = {}

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> Fetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ internals

    def _get(self, url: str, *, what: str) -> httpx.Response:
        last: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = self.client.get(url)
                if resp.status_code == 404:
                    raise FetchError(f"{what}: 404 Not Found at {url}")
                resp.raise_for_status()
                return resp
            except FetchError:
                raise
            except Exception as exc:  # noqa: BLE001 - retry any transport/5xx failure
                last = exc
                if attempt < MAX_ATTEMPTS:
                    backoff = 2 ** (attempt - 1)
                    log.warning(
                        "%s: attempt %d/%d failed (%s); retrying in %ds",
                        what,
                        attempt,
                        MAX_ATTEMPTS,
                        exc,
                        backoff,
                    )
                    time.sleep(backoff)
        raise FetchError(f"{what}: all {MAX_ATTEMPTS} attempts failed: {last}")

    def _download_parquet(self, url: str, dest: Path, *, what: str) -> Path:
        if dest.exists() and dest.stat().st_size > 0:
            log.debug("%s: cached at %s", what, dest)
            return dest
        resp = self._get(url, what=what)
        tmp = dest.with_suffix(dest.suffix + ".part")
        tmp.write_bytes(resp.content)
        tmp.replace(dest)
        log.info("%s: fetched %s (%s bytes)", what, url, f"{len(resp.content):,}")
        return dest

    # -------------------------------------------------------------------- public

    def parquet_path(self, ds: Dataset, edition: str | None = None) -> Path:
        name = f"{ds.id}__{edition}.parquet" if edition else f"{ds.id}.parquet"
        return self.raw_dir / name

    def meta_path(self, ds: Dataset) -> Path:
        return self.meta_dir / f"{ds.id}.json"

    def fetch_parquet(self, ds: Dataset, edition: str | None = None) -> Path:
        """Download (or reuse) the parquet for a dataset, or one edition of it."""
        if ds.is_editioned:
            if edition is None:
                raise ValueError(f"{ds.id} is editioned; an edition (e.g. '2026-09') is required")
            url = PRICECATCHER_URL.format(edition=edition)
        else:
            if edition is not None:
                raise ValueError(f"{ds.id} is not editioned")
            if not ds.url:
                raise ValueError(f"{ds.id} has no parquet URL declared")
            url = ds.url
        dest = self.parquet_path(ds, edition)
        if self.offline:
            if not dest.exists():
                raise FetchError(f"{ds.id}: offline mode and {dest} is not cached")
            return dest
        return self._download_parquet(url, dest, what=ds.id)

    def fetch_catalogue_meta(self, ds: Dataset, *, refresh: bool = False) -> CatalogueMeta:
        """Read the dataset's catalogue page and extract its metadata block."""
        path = self.meta_path(ds)
        if path.exists() and not refresh:
            return CatalogueMeta.model_validate_json(path.read_text(encoding="utf-8"))
        if self.offline:
            if path.exists():
                return CatalogueMeta.model_validate_json(path.read_text(encoding="utf-8"))
            raise FetchError(f"{ds.id}: offline mode and no cached catalogue metadata")

        last_exc: Exception | None = None
        for base in registry.CATALOGUE_BASES:
            url = f"{base}/data-catalogue/{ds.id}"
            try:
                resp = self._get(url, what=f"{ds.id} catalogue page")
            except FetchError as exc:
                last_exc = exc
                continue
            meta = _parse_catalogue_page(resp.text, ds.id, base)
            if meta is not None:
                path.write_text(
                    json.dumps(meta.model_dump(), indent=1, ensure_ascii=False), encoding="utf-8"
                )
                return meta
        raise FetchError(f"{ds.id}: could not read catalogue metadata ({last_exc})")

    def editions_for(self, ds: Dataset) -> list[str]:
        """Available editions (newest first), read live with the registry as fallback."""
        meta = self.fetch_catalogue_meta(ds)
        return meta.editions or list(ds.editions)


def _parse_catalogue_page(html: str, dataset_id: str, base: str) -> CatalogueMeta | None:
    for blob in NEXT_DATA_RE.findall(html):
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        props = data.get("props", {}).get("pageProps", {})
        if not isinstance(props, dict) or props.get("id") != dataset_id:
            continue
        return CatalogueMeta(
            id=dataset_id,
            title=props.get("title") or "",
            description=props.get("description") or "",
            frequency=props.get("frequency"),
            data_as_of=props.get("data_as_of"),
            last_updated=props.get("last_updated"),
            next_update=props.get("next_update"),
            data_source=list(props.get("data_source") or []),
            methodology=props.get("methodology") or "",
            caveat=props.get("caveat") or "",
            link_parquet=props.get("link_parquet"),
            editions=list(props.get("link_editions") or []),
            source_base=base,
        )
    return None
