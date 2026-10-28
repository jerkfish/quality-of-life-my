import re, sys, json, html

pat = re.compile(
    r'data-catalogue/(?P<slug>[a-z0-9_]+)">.*?'
    r'<p class="truncate text-lg font-bold[^"]*" title="(?P<title>[^"]*)">.*?'
    r'<p class="text-sm truncate">(?P<desc>.*?)</p>',
    re.S,
)

for path, out in ((sys.argv[1], sys.argv[2]),):
    raw = open(path, encoding="utf-8", errors="replace").read()
    rows, seen = [], set()
    for m in pat.finditer(raw):
        slug = m.group("slug")
        if slug in seen:
            continue
        seen.add(slug)
        rows.append((slug, html.unescape(m.group("title")), html.unescape(m.group("desc"))))
    with open(out, "w", encoding="utf-8") as fh:
        for slug, title, desc in rows:
            fh.write(f"{slug}\t{title}\t{desc}\n")
    print(f"{path}: {len(rows)} datasets -> {out}")
