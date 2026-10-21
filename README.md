# website-fact-check

Find inconsistent business facts across a static website — before search
engines and AI assistants do.

A local business repeats the same facts — name, phone, address, opening
hours, prices — in visible page copy, JSON-LD structured data, FAQ pages
and `llms.txt`. Edit one copy and forget the others, and the site starts
telling different stories in different places. Humans rarely notice;
search engines and AI answer engines do, and they repeat the wrong
version with confidence.

`website-fact-check` scans a static site directory, extracts the facts it
can find **deterministically (no model calls, standard library only)**,
and reports every field that has more than one distinct value, with the
file and source of each value.

## Usage

```bash
python3 fact_check.py path/to/site
python3 fact_check.py path/to/site --json   # machine-readable output
```

Exit code is `1` when conflicts are found, so it can gate a deploy or run
in CI:

```bash
python3 fact_check.py ./public || echo "fact conflicts need review"
```

Example output (real run on the bundled test fixture, which deliberately
contains a phone drift, a price drift, and a price leaked into `llms.txt`):

```
[ok] address: 1 distinct value(s)
    - '1 Demo Road Shanghai'  <- index.html (JSON-LD)
[ok] name: 1 distinct value(s)
    - 'Example Studio'  <- index.html (JSON-LD)
[CONFLICT] phone: 2 distinct value(s)
    - '13800001111'  <- index.html (JSON-LD)
    - '13900002222'  <- index.html (page text)
[CONFLICT] price: 2 distinct value(s)
    - '1300'  <- index.html (JSON-LD priceRange)
    - '1500'  <- index.html (page text)
[FORBIDDEN] price ¥1500 in AI-facing file  <- llms.txt (llms.txt)

2 conflicted field(s), 1 forbidden finding(s), 4 field(s) checked.
```

## What it checks

**Consistency** — the same fact stated in more than one place must agree:

| Field | Sources |
|---|---|
| name | JSON-LD `name`, `ai/*.json` |
| phone | JSON-LD `telephone`, phone patterns in page text and `llms.txt`, `ai/*.json` |
| address | JSON-LD `address` (string or PostalAddress parts) |
| openingHours | JSON-LD `openingHours` |
| price | JSON-LD `priceRange`, `¥`/`CNY` amounts in page text |
| url | JSON-LD `url` |

Values are normalized before comparison: phone numbers lose country codes
and separators (`+86 138-0000-1111` = `13800001111`), and names are
canonicalized (whitespace collapsed, spaces around `·` removed), so
cosmetic variants do not raise false conflicts.

**Forbidden values** — some facts must not appear in AI-facing files at
all. Any price amount found in `llms.txt` or `ai/*.json` is reported as
`FORBIDDEN`, not as a consistency candidate: those surfaces are exactly
where a stale price does the most damage, because AI assistants quote
them verbatim.

The tool is **read-only**: it reports and exits non-zero; it never edits
the site.

## Why

This tool was extracted from the maintenance workflow of a production
small-business website that is deliberately built to be read correctly by
AI search (semantic HTML, schema.org data, `llms.txt`). Keeping visible
copy and machine-readable data consistent turned out to be the most
failure-prone part of that maintenance — stale prices and diverging
descriptions in files nobody remembered editing. The checks that are
purely syntactic run without any model; a model-assisted semantic review
step is planned as an optional layer on top.

## Tests

```bash
python3 tests/test_fact_check.py
```

The fixture site in `tests/fixtures/site` deliberately contains a phone
and a price conflict.

## License

MIT — see [LICENSE](LICENSE).
