"""Extract dataset metadata from OpenDOSM / data.gov.my catalogue pages.

Each catalogue page inlines its full metadata (fields, frequency, source, as-of
dates, methodology, caveat, and bulk parquet/csv links) inside the
`__NEXT_DATA__` script tag. This is a website fetch, not an api.data.gov.my
call, so it does not consume the 4 req/min API quota.

Usage:  uv run python fetch_meta.py <slug> [<slug> ...]
        uv run python fetch_meta.py < slugs.txt
"""

import json
import os
import re
import sys
import time
import urllib.request

BASES = ["https://open.dosm.gov.my", "https://data.gov.my"]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "meta")
os.makedirs(OUT, exist_ok=True)
UA = {"User-Agent": "col-check-discovery/0.1 (Phase 0 dataset reconnaissance)"}
NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)


def fetch(slug):
    for base in BASES:
        url = f"{base}/data-catalogue/{slug}"
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=45) as r:
                html = r.read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            print(f"  {base}: {exc}")
            continue
        for blob in NEXT_DATA.findall(html):
            try:
                data = json.loads(blob)
            except json.JSONDecodeError:
                continue
            props = data.get("props", {}).get("pageProps", {})
            if isinstance(props, dict) and props.get("id") == slug:
                props["_source_base"] = base
                return props
    return None


def summarise(meta):
    fields = [f.get("name") for f in meta.get("fields", [])]
    return (
        f"freq={meta.get('frequency')} as_of={meta.get('data_as_of')} "
        f"updated={meta.get('last_updated')} fields={fields}\n"
        f"      parquet={meta.get('link_parquet')}"
    )


def main(slugs):
    for i, slug in enumerate(slugs):
        path = os.path.join(OUT, f"{slug}.json")
        if os.path.exists(path) and os.path.getsize(path) > 50:
            print(f"[skip] {slug}")
            continue
        meta = fetch(slug)
        if meta is None:
            print(f"[MISS] {slug}")
        else:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(meta, fh, indent=1, ensure_ascii=False)
            print(f"[ok]   {slug}: {summarise(meta)}")
        if i < len(slugs) - 1:
            time.sleep(1.0)


if __name__ == "__main__":
    args = sys.argv[1:]
    main(args if args else [l.strip() for l in sys.stdin if l.strip()])
