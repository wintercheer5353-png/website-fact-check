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


PRICE_CONTEXT_RE = re.compile(r"价格|价目|收费|定价|price|cost", re.I)


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


ADDR_PART_KEYS = ("streetaddress", "street", "addresslocality", "locality",
                  "addressregion", "region", "postalcode")


def core_address(addr: dict) -> str:
    """The comparable core of an address object: postal parts only, in a
    fixed field order. Auxiliary keys — landmark, directions, notes —
    are how-to-find-us prose, not address facts, and never take part.
    Country is excluded on purpose: surfaces routinely state it on one
    copy and omit it on another, and an omission is not a contradiction."""
    lower = {str(k).lower(): v for k, v in addr.items()}
    parts = []
    for key in ADDR_PART_KEYS:
        v = lower.get(key)
        if isinstance(v, str) and v.strip() and v.strip() not in parts:
            parts.append(v.strip())
    return " ".join(parts)


def norm_address(raw: str) -> str:
    """Canonical form for comparison: collapse whitespace and drop a
    space sitting between two CJK characters ('上海市 浦东新区' and
    '上海市浦东新区' are the same address). Spaces in Latin-script
    addresses are significant and are kept."""
    v = re.sub(r"\s+", " ", raw).strip()
    return re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", v)


DAY_CODES = {"monday": "Mo", "tuesday": "Tu", "wednesday": "We",
             "thursday": "Th", "friday": "Fr", "saturday": "Sa",
             "sunday": "Su"}
DAY_ORDER = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]


def norm_hours(raw: str) -> str:
    """Canonical form: drop parenthetical annotations such as
    '（预约制）' / '(by appointment)' and collapse whitespace."""
    v = re.sub(r"[（(][^）)]*[）)]", "", raw)
    return re.sub(r"\s+", " ", v).strip()


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
                joined = core_address(addr)
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
            spec = node.get("openingHoursSpecification")
            if spec:
                for item in (spec if isinstance(spec, list) else [spec]):
                    if not isinstance(item, dict):
                        continue
                    days = item.get("dayOfWeek") or []
                    if isinstance(days, str):
                        days = [days]
                    codes = [DAY_CODES.get(str(d).rsplit("/", 1)[-1]
                                           .strip().lower(), "")
                             for d in days]
                    opens, closes = item.get("opens"), item.get("closes")
                    if codes and all(codes) and opens and closes:
                        if len(set(codes)) == 7:
                            span = "Mo-Su"
                        else:
                            span = ",".join(sorted(set(codes),
                                                   key=DAY_ORDER.index))
                        out.setdefault("openingHours", set()).add(
                            f"{span} {opens}-{closes}")
        for value in node.values():
            if isinstance(value, (dict, list)):
                walk_jsonld(value, out, _root=False)


PRICE_KEYS = ("price", "pricerange", "lowprice", "highprice")


def scan_ai_json(node, source, forbidden, facts=None, _key=""):
    """Deep scan of AI-facing JSON. Forbidden prices at any depth: a
    dedicated price key (price, offers.price, priceRange, ...) with a
    numeric or string value, or a currency amount inside any free-text
    string; findings keep the JSON key so the value can be located.
    Phone numbers are consistency facts wherever they appear in these
    files — a root field, a nested booking.telephone, or a phone number
    inside an FAQ answer string are all copies of the same fact."""
    if isinstance(node, dict):
        for key, value in node.items():
            scan_ai_json(value, source, forbidden, facts, key)
    elif isinstance(node, list):
        for item in node:
            scan_ai_json(item, source, forbidden, facts, _key)
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
        if facts is not None:
            if _key.lower() == "telephone":
                add_fact(facts, "phone", norm_phone(node), source)
            for m in PHONE_RE.finditer(node):
                add_fact(facts, "phone", norm_phone(m.group(1)), source)


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
        rel = path.relative_to(site).as_posix()
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
            tag = "(llms.txt)" if path.name.lower().startswith("llms") \
                else "(ai.txt)"
            text = path.read_text(encoding="utf-8", errors="ignore")
            for m in PHONE_RE.finditer(text):
                add_fact(facts, "phone", norm_phone(m.group(1)),
                         f"{rel} {tag}")
            # Policy check, not consistency: AI-facing files must not
            # carry prices at all, so any amount here is a violation,
            # regardless of whether pages agree with it.
            for n in price_amounts(text):
                forbidden.append((f"price ¥{n} in AI-facing file",
                                  f"{rel} {tag}"))
            # A price list in these files is often bare numbers under a
            # 价格/price heading, with no currency marker at all. On a
            # line that is clearly about prices, 3–5 digit bare numbers
            # are prices too.
            for line in text.splitlines():
                if PRICE_CONTEXT_RE.search(line):
                    marked = set(price_amounts(line))
                    for m in re.finditer(r"(?<![\d.])(\d{3,5})(?![\d.])",
                                         line):
                        if m.group(1) in marked:
                            continue
                        # A number governed by a negation in its own
                        # segment ("无 2500 档", "no 2500 tier") states
                        # that the price does NOT exist; it is not a
                        # price and must not be reported as one.
                        segment = re.split(r"[/、，,;；]", line[:m.start()])[-1]
                        if re.search(r"无|没有", segment) or \
                                re.search(r"\b(no|not|without)\b",
                                          segment, re.I):
                            continue
                        forbidden.append(
                            (f"price ¥{m.group(1)} in AI-facing file",
                             f"{rel} {tag}"))
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
                    joined = core_address(addr)
                    if joined:
                        add_fact(facts, "address", joined, f"{rel} (ai json)")
            # Deep scan: forbidden prices at any depth, and every phone
            # copy (nested fields and free-text answers included).
            scan_ai_json(data, f"{rel} (ai json)", forbidden, facts)
    # Canonicalize values collected from every surface, so that merely
    # typographic differences never masquerade as fact conflicts.
    for field, norm in (("name", norm_name), ("address", norm_address),
                        ("openingHours", norm_hours)):
        if field in facts:
            merged = {}
            for value, sources in facts[field].items():
                merged.setdefault(norm(value), []).extend(sources)
            facts[field] = merged
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
