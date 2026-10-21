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

Exit code is `1` when conflicts **or forbidden findings** are present,
so it can gate a deploy or run in CI:

```bash
python3 fact_check.py ./public || {
  status=$?
  echo "fact conflicts need review"
  exit "$status"
}
```

Example output (real run on the bundled test fixture, which deliberately
contains a phone drift, a price drift, prices leaked into `llms.txt` and
`.well-known/ai.txt`, and one unparseable JSON-LD block):

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
[FORBIDDEN] price ¥999 in AI-facing file  <- .well-known/ai.txt (ai.txt)
[FORBIDDEN] price ¥1300 in AI-facing file  <- .well-known/ai.txt (ai.txt)
[FORBIDDEN] price ¥2300 in AI-facing file  <- .well-known/ai.txt (ai.txt)
[FORBIDDEN] price ¥3800 in AI-facing file  <- .well-known/ai.txt (ai.txt)
[FORBIDDEN] invalid JSON-LD block (unparseable)  <- broken.html
[FORBIDDEN] price ¥1500 in AI-facing file  <- llms.txt (llms.txt)
[FORBIDDEN] price ¥1300 in AI-facing file  <- llms.txt (llms.txt)

2 conflicted field(s), 7 forbidden finding(s), 4 field(s) checked.
```

Text output shows up to 4 sources per value (marked `(+N more)`);
`--json` output always carries the full source lists.

## What it checks

**Consistency** — the same fact stated in more than one place must agree:

| Field | Sources |
|---|---|
| name | business entity's JSON-LD `name`, `ai/*.json` root object |
| phone | JSON-LD `telephone`, phone patterns in page text and `llms.txt`, `ai/*.json` (root fields, nested fields and free-text answers) |
| address | JSON-LD `address`, `ai/*.json` root object — postal parts only (street/locality/region/postal code); landmark and directions are ignored, and country is excluded (one copy stating it and another omitting it is not a contradiction) |
| openingHours | JSON-LD `openingHours` and `openingHoursSpecification` |
| price | JSON-LD `priceRange`, currency amounts in page text |
| url | business entity's JSON-LD `url`, `ai/*.json` root object |

Facts are scoped to the **business entity**: in JSON-LD, only nodes that
describe the business itself (LocalBusiness, BeautySalon, Organization
and similar types) contribute facts. Names and URLs of articles, FAQ
questions, people, products and individual pages are different entities'
facts and are ignored — comparing them would manufacture conflicts that
do not exist. In `ai/*.json`, consistency facts come from the file's root
object; nested entries are catalog items, checked for forbidden prices
only.

Values are normalized before comparison: phone numbers lose country codes
and separators (`+86 138-0000-1111` = `13800001111`), and names are
canonicalized (whitespace collapsed, spaces around `·` removed), so
cosmetic variants do not raise false conflicts.

**Forbidden values** — some facts must not appear in AI-facing files at
all. Any price — a `price`/`priceRange` key at any depth, or a currency
amount (`¥1500`, `CNY 1500`, `1500元`) inside any string — found in
`llms.txt`, `llms-full.txt`, `.well-known/ai.txt` or `ai/*.json` is
reported as `FORBIDDEN`, not as a consistency candidate: those surfaces
are exactly where a stale price does the most damage, because AI
assistants quote them verbatim. Bare numbers on a line that is clearly
about prices (`价格…`, `price: …`) count too — a price list without
currency symbols is still a price list. A number governed by a negation
in its own segment (`无 2500 档`, "no 2500 tier") states that the price
does **not** exist and is not reported. Unparseable JSON-LD blocks are
reported as findings too, never silently skipped.

**Scope:** the model is one value per fact. A page that legitimately
publishes several different prices (a per-service price list) will see
them reported as differing values of the single `price` fact; the tool
is built for businesses that publish one price per fact, and for
keeping prices out of AI-facing files entirely.

The tool is **read-only**: it reports and exits non-zero; it never edits
the site. Output is UTF-8 (the tool reconfigures stdout where the
platform allows), so reports survive legacy Windows consoles.

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
