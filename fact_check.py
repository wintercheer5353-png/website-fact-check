#!/usr/bin/env python3
"""website-fact-check: find inconsistent business facts across a static website.

A local business repeats the same facts — name, phone, address, opening
hours, prices — in visible copy, JSON-LD structured data, FAQ pages and
llms.txt. When one copy is edited and the others are not, search engines
and AI assistants pick up conflicting facts. This tool scans a static site
directory, extracts the facts it can find deterministically (no model
calls), and reports every field that has more than one distinct value,
with the file and source of each value.

Usage:
    python3 fact_check.py <site-dir> [--json]

Exit code: 0 = no conflicts, 1 = conflicts found, 2 = usage error.
Standard library only.
"""

import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

PHONE_RE = re.compile(r"(?<!\d)(?:\+?86[-\s]?)?(1\d{2}[-\s]?\d{4}[-\s]?\d{4})(?!\d)")
PRICE_RE = re.compile(r"(?:¥|￥|CNY\s?|RMB\s?)(\d[\d,]*(?:\.\d+)?)"
                      r"|(\d[\d,]*(?:\.\d+)?)\s*元")


def price_amounts(text):
    """All currency amounts in a string, normalized (no thousands
    separators). Matches ¥1500, CNY 1500 and the suffix form 1500元."""
    out = []
    for m in PRICE_RE.finditer(text):
        out.append((m.group(1) or m.group(2)).replace(",", ""))
    return out


def norm_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("86") and len(digits) == 13:
        digits = digits[2:]
    return digits


def norm_text(raw: str) -> str:
    return re.sub(r"\s+", " ", raw).strip().lower()


def norm_name(raw: str) -> str:
    """Canonical form for names: collapse whitespace and drop spaces
    around a middle dot, so 'Brand · Studio' and 'Brand·Studio' count
    as the same fact instead of a false conflict."""
    v = re.sub(r"\s+", " ", raw).strip()
    v = re.sub(r"\s*·\s*", "·", v)
    return v


class PageParser(HTMLParser):
    """Collect JSON-LD blocks and visible text from one HTML page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.jsonld = []
        self.jsonld_errors = 0
        self.text_parts = []
        self._in_jsonld = False
        self._in_hidden = 0  # inside script/style
        self._buf = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("type") == "application/ld+json":
            self._in_jsonld = True
            self._buf = []
        elif tag in ("script", "style"):
            self._in_hidden += 1

    def handle_endtag(self, tag):
        if tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            try:
                self.jsonld.append(json.loads("".join(self._buf)))
            except json.JSONDecodeError:
                self.jsonld_errors += 1
        elif tag in ("script", "style") and self._in_hidden:
            self._in_hidden -= 1

    def handle_data(self, data):
        if self._in_jsonld:
            self._buf.append(data)
        elif not self._in_hidden:
            self.text_parts.append(data)

    @property
    def visible_text(self) -> str:
        return " ".join(self.text_parts)


BUSINESS_TYPE_HINTS = ("business", "organization", "salon", "store",
                       "shop", "restaurant", "dentist", "clinic", "studio")


def _is_business_node(node) -> bool:
    """A node describes the business itself (LocalBusiness, BeautySalon,
    Organization...), as opposed to an article, FAQ question, person,
    product or breadcrumb that merely appears on the same page."""
    t = node.get("@type", "")
    types = t if isinstance(t, list) else [t]
    return any(isinstance(x, str) and
               any(h in x.lower() for h in BUSINESS_TYPE_HINTS)
               for x in types)


def walk_jsonld(node, out, _root=True):
    """Pull business-fact fields out of JSON-LD, scoped to the business
    entity. Names and URLs of other entities on the page (articles, FAQ
    questions, people, works) are not the business's facts and are not
    collected, so they cannot masquerade as name/URL conflicts."""
    if isinstance(node, list):
        for item in node:
            walk_jsonld(item, out, _root=False)
    elif isinstance(node, dict):
        if "@graph" in node:
            walk_jsonld(node["@graph"], out, _root=False)
        bare_entity = _root and "name" in node and "telephone" in node
        if _is_business_node(node) or bare_entity:
            for key in ("name", "telephone", "priceRange", "url"):
                if isinstance(node.get(key), str):
                    out.setdefault(key, set()).add(node[key].strip())
            addr = node.get("address")
            if isinstance(addr, dict):
                parts = [addr.get(k, "") for k in
                         ("streetAddress", "addressLocality",
                          "addressRegion", "postalCode")]
                joined = " ".join(p for p in parts if p).strip()
                if joined:
                    out.setdefault("address", set()).add(joined)
            elif isinstance(addr, str):
                out.setdefault("address", set()).add(addr.strip())
            hours = node.get("openingHours")
            if isinstance(hours, str):
                out.setdefault("openingHours", set()).add(hours.strip())
            elif isinstance(hours, list):
                for h in hours:
                    if isinstance(h, str):
                        out.setdefault("openingHours", set()).add(h.strip())
        for value in node.values():
            if isinstance(value, (dict, list)):
                walk_jsonld(value, out, _root=False)


PRICE_KEYS = ("price", "pricerange", "lowprice", "highprice")


def scan_ai_json(node, source, forbidden, _key=""):
    """Forbidden-price scan for AI-facing JSON, at any depth: a dedicated
    price key (price, offers.price, priceRange, ...) with a numeric or
    string value, or a currency amount inside any free-text string.
    Findings keep the JSON key so the value can be located."""
    if isinstance(node, dict):
        for key, value in node.items():
            scan_ai_json(value, source, forbidden, key)
    elif isinstance(node, list):
        for item in node:
            scan_ai_json(item, source, forbidden, _key)
    elif isinstance(node, bool):
        return
    elif isinstance(node, (int, float)):
        if _key.lower() in PRICE_KEYS:
            forbidden.append((f"price value at key {_key!r}: {node}",
                              source))
    elif isinstance(node, str):
        if _key.lower() in PRICE_KEYS and node.strip():
            forbidden.append((f"price value at key {_key!r}: {node.strip()!r}",
                              source))
        else:
            for n in price_amounts(node):
                forbidden.append((f"price ¥{n} in AI-facing file", source))


def add_fact(facts, field, value, source):
    value = value.strip()
    if value:
        facts.setdefault(field, {}).setdefault(value, []).append(source)


def collect(site: Path):
    facts = {}  # field -> value -> [sources]
    forbidden = []  # (description, source) findings that must never appear
    for path in sorted(site.rglob("*")):
        if path.is_dir() or ".git" in path.parts:
            continue
        rel = str(path.relative_to(site))
        if path.suffix.lower() in (".html", ".htm"):
            parser = PageParser()
            parser.feed(path.read_text(encoding="utf-8", errors="ignore"))
            for _ in range(parser.jsonld_errors):
                forbidden.append(("invalid JSON-LD block (unparseable)", rel))
            for block in parser.jsonld:
                found = {}
                walk_jsonld(block, found)
                for field, values in found.items():
                    for v in values:
                        if field == "telephone":
                            add_fact(facts, "phone", norm_phone(v), f"{rel} (JSON-LD)")
                        elif field == "priceRange":
                            amounts = price_amounts(v)
                            if not amounts:
                                amounts = re.findall(r"\d[\d,]*(?:\.\d+)?", v)
                            for n in amounts:
                                add_fact(facts, "price", n,
                                         f"{rel} (JSON-LD priceRange)")
                        else:
                            add_fact(facts, field, v, f"{rel} (JSON-LD)")
            for m in PHONE_RE.finditer(parser.visible_text):
                add_fact(facts, "phone", norm_phone(m.group(1)), f"{rel} (page text)")
            for n in price_amounts(parser.visible_text):
                add_fact(facts, "price", n, f"{rel} (page text)")
        elif path.name.lower() in ("llms.txt", "llms-full.txt", "ai.txt"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            for m in PHONE_RE.finditer(text):
                add_fact(facts, "phone", norm_phone(m.group(1)), f"{rel} (llms.txt)")
            # Policy check, not consistency: AI-facing files must not
            # carry prices at all, so any amount here is a violation,
            # regardless of whether pages agree with it.
            for n in price_amounts(text):
                forbidden.append((f"price ¥{n} in AI-facing file",
                                  f"{rel} (llms.txt)"))
        elif path.suffix.lower() == ".json" and "ai" in path.parts:
            try:
                data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
            except json.JSONDecodeError:
                forbidden.append(("invalid JSON", rel))
                continue
            # Consistency facts come from the file's root object only:
            # it describes the business. Nested entries are catalog items
            # (services, FAQs) whose names are not the business's name.
            if isinstance(data, dict):
                for key in ("name", "telephone", "url", "openingHours"):
                    v = data.get(key)
                    if isinstance(v, str):
                        if key == "telephone":
                            add_fact(facts, "phone", norm_phone(v),
                                     f"{rel} (ai json)")
                        elif key == "name":
                            add_fact(facts, "name", norm_name(v),
                                     f"{rel} (ai json)")
                        else:
                            add_fact(facts, key, v, f"{rel} (ai json)")
                addr = data.get("address")
                if isinstance(addr, str):
                    add_fact(facts, "address", addr, f"{rel} (ai json)")
                elif isinstance(addr, dict):
                    joined = " ".join(str(x) for x in addr.values()
                                      if isinstance(x, str)).strip()
                    if joined:
                        add_fact(facts, "address", joined, f"{rel} (ai json)")
            # Forbidden prices are scanned at any depth, in any shape.
            scan_ai_json(data, f"{rel} (ai json)", forbidden)
    # Canonicalize names collected from every surface.
    if "name" in facts:
        merged = {}
        for value, sources in facts["name"].items():
            merged.setdefault(norm_name(value), []).extend(sources)
        facts["name"] = merged
    return facts, list(dict.fromkeys(forbidden))


def conflicts(facts):
    return {f: vals for f, vals in facts.items() if len(vals) > 1}


def main(argv):
    try:  # ¥ and CJK output must not crash legacy Windows consoles
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = [a for a in argv[1:] if not a.startswith("--")]
    as_json = "--json" in argv
    if len(args) != 1 or not Path(args[0]).is_dir():
        print(__doc__)
        return 2
    facts, forbidden = collect(Path(args[0]))
    bad = conflicts(facts)
    if as_json:
        print(json.dumps({"facts": {f: {v: s for v, s in vals.items()}
                                    for f, vals in facts.items()},
                          "conflicts": list(bad),
                          "forbidden": [{"finding": d, "source": s}
                                        for d, s in forbidden]},
                         ensure_ascii=False, indent=2))
    else:
        for field in sorted(facts):
            vals = facts[field]
            mark = "CONFLICT" if field in bad else "ok"
            print(f"[{mark}] {field}: {len(vals)} distinct value(s)")
            for value, sources in sorted(vals.items()):
                uniq = sorted(set(sources))
                shown = ", ".join(uniq[:4])
                if len(uniq) > 4:
                    shown += f" (+{len(uniq) - 4} more)"
                print(f"    - {value!r}  <- {shown}")
        for desc, source in forbidden:
            print(f"[FORBIDDEN] {desc}  <- {source}")
        print(f"\n{len(bad)} conflicted field(s), {len(forbidden)} forbidden "
              f"finding(s), {len(facts)} field(s) checked.")
    return 1 if (bad or forbidden) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
