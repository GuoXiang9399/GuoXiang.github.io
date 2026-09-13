#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scan files/ for newly added literature PDFs and generate matching
_publications/*.md entries in the same format as the existing ones.

For every new PDF the script:
  1. extracts text from its first pages (PyMuPDF or pypdf, if installed);
  2. finds the paper's DOI (from the PDF text, or by searching CrossRef with
     the title encoded in the PDF filename, e.g. "2026-<Paper Title>.pdf");
  3. fetches metadata (title, journal, date, abstract) from CrossRef;
  4. writes _publications/<date>-<slug>.md in the site's standard format.

It also backfills abstracts for entries that still carry a TODO placeholder.

The script is idempotent:
  - PDFs whose DOI or title already appears in _publications/ are skipped;
  - existing markdown files are never overwritten.

Usage:
    python auto_publications.py            # normal run
    python auto_publications.py --dry-run  # only report, write nothing

Dependencies: Python 3 standard library only. PyMuPDF (pip install pymupdf)
or pypdf are used opportunistically to read PDF text; without them the
script still works via CrossRef title search on the PDF filename.
"""

import argparse
import difflib
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FILES_DIR = ROOT / "files"
PUBS_DIR = ROOT / "_publications"

CROSSREF_API = "https://api.crossref.org/works"
CONTACT_EMAIL = "guoxiang@henu.edu.cn"
USER_AGENT = "GuoXiang.github.io publication importer (mailto:%s)" % CONTACT_EMAIL

TODO_ABSTRACT = "<!-- TODO: abstract -->"
MAX_PDF_PAGES = 3

# Category auto-assignment rules. Lists are checked in order and the first
# keyword found in title+abstract wins. Every auto-assigned category is
# flagged in the report for manual review.
PARASITE_KEYWORDS = [
    "malaria", "myiasis", "parasit", "plasmodi", "chrysomya", "helminth",
    "filaria", "schistosom",
]
VIR_TECH_KEYWORDS = [
    "crispr", "cas12", "cas13", "metavirome", "virome", "virus-derived dna",
    "viral dna", "vdna", "metagenom", "metatranscriptom",
    "isothermal amplification", "lateral flow", "point-of-care",
]
VECTOR_FIELD_KEYWORDS = [
    "ovitrap", "oviposit", "insecticid", "larvicid", "adulticid", "diapaus",
    "autecology", "vector surveillance", "mosquito surveillance",
    "vector control", "mosquito control", "field investigation", "blood meal",
    "biting rhythm",
]
EV_DENV_KEYWORDS = [
    "phylodynamic", "phylogen", "genotyping", "serotype", "epidemic",
    "outbreak", "evolution", "genomic", "sequence database", "epidemiolog",
    "diffusion", "reproduction number", "dengue", "chikungunya", "zika",
    "arboviru",
]
AE_HABIT_KEYWORDS = [
    "aedes", "mosquito", "vector", "surveillance", "trap", "ecolog",
    "habitat", "population",
]

DATE_FIELDS = ("published-online", "published-print", "published", "issued", "created")

DOI_BODY = r"10\.\d{4,9}/[^\s\"'<>\]\),;]+"

_TAG_RE = re.compile(r"<[^>]+>")

# "A B S T R A C T"-style spaced headings (common in Elsevier PDFs) are
# matched by allowing optional whitespace between every letter.
_ABSTRACT_HEADING = re.compile(
    r"(?:^|\n)\s*(?:a\s?b\s?s\s?t\s?r\s?a\s?c\s?t|s\s?u\s?m\s?m\s?a\s?r\s?y)\s*[:.\-]?\s*",
    re.IGNORECASE,
)
_ABSTRACT_STOP = re.compile(
    r"(?:^|\n)\s*(?:k\s?e\s?y\s?w\s?o\s?r\s?d\s?|©|(?:1\s*\.?\s+)?introduction"
    r"|citation\s*:|correspondence|author contributions?|conflicts? of interest"
    r"|abbreviations?|acknowledge?ments?|article info)",
    re.IGNORECASE,
)


class CrossrefUnavailable(Exception):
    """Network / API problem - the PDF should be retried on the next run."""


# --------------------------------------------------------------------------
# PDF helpers

def pdf_text(path):
    """Best-effort text extraction from the first pages of a PDF."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        fitz = None
    if fitz is not None:
        try:
            chunks = []
            with fitz.open(str(path)) as doc:
                for i, page in enumerate(doc):
                    if i >= MAX_PDF_PAGES:
                        break
                    chunks.append(page.get_text())
            return "\n".join(chunks)
        except Exception:
            return ""
    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader  # noqa: N813 - old package name
        except ImportError:
            return ""
    try:
        reader = PdfReader(str(path))
        chunks = []
        for i, page in enumerate(reader.pages):
            if i >= MAX_PDF_PAGES:
                break
            chunks.append(page.extract_text() or "")
        return "\n".join(chunks)
    except Exception:
        return ""


def find_doi(text):
    """Return the most likely DOI mentioned in the text (the paper's own).

    All candidates are collected and the most frequent one wins, so a DOI
    cited in the references does not outvote the paper's own DOI (which
    usually appears several times on the first pages).
    """
    if not text:
        return None
    candidates = [m.group(1) for m in
                  re.finditer(r"doi\.org/(" + DOI_BODY + r")", text, re.IGNORECASE)]
    if not candidates:
        candidates = [m.group(1) for m in
                      re.finditer(r"(?i)doi[\s:.\-]*\(?(" + DOI_BODY + r")", text)]
    if not candidates:
        candidates = [m.group(0) for m in
                      re.finditer(r"(?<![A-Za-z0-9])" + DOI_BODY, text)]
    if not candidates:
        return None
    counts = Counter(c.rstrip(".,;").lower() for c in candidates)
    return counts.most_common(1)[0][0]


def title_from_filename(path):
    """'2026-Some Paper Title.pdf' -> 'Some Paper Title'."""
    stem = path.stem.strip()
    m = re.match(r"^\d{4}[\s\-_.]+(.+)$", stem)
    if m and m.group(1).strip():
        return m.group(1).strip()
    return stem


def year_from_filename(path):
    m = re.match(r"^\s*(\d{4})", path.stem)
    return m.group(1) if m else None


def extract_abstract(text):
    """Heuristically extract the abstract from the first pages of a PDF."""
    if not text:
        return ""
    m = _ABSTRACT_HEADING.search(text)
    if not m:
        return ""
    rest = text[m.end():]
    stop = _ABSTRACT_STOP.search(rest)
    if stop:
        rest = rest[:stop.start()]
    rest = re.sub(r"\s+", " ", rest).strip()
    if not (100 <= len(rest) <= 3500):
        return ""
    return rest


# --------------------------------------------------------------------------
# CrossRef

def _http_get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise CrossrefUnavailable("HTTP %s for %s" % (exc.code, url))
    except Exception as exc:  # URLError, timeout, bad JSON, ...
        raise CrossrefUnavailable("%s (%s)" % (exc, url))


def crossref_by_doi(doi):
    """Return the CrossRef message dict for a DOI, or None."""
    if not doi:
        return None
    data = _http_get_json("%s/%s" % (CROSSREF_API, urllib.parse.quote(doi)))
    if not data:
        return None
    return data.get("message") or None


def crossref_search(title):
    """Search CrossRef by bibliographic title; return the best item or None."""
    if not title:
        return None
    url = CROSSREF_API + "?" + urllib.parse.urlencode(
        {"query.bibliographic": title, "rows": "5"})
    data = _http_get_json(url)
    if not data:
        return None
    items = (data.get("message") or {}).get("items") or []
    best_item, best_ratio = None, 0.0
    for item in items:
        cr_title = (item.get("title") or [""])[0].strip()
        if not cr_title:
            continue
        ratio = difflib.SequenceMatcher(
            None, title.lower(), cr_title.lower()).ratio()
        if ratio > best_ratio:
            best_item, best_ratio = item, ratio
    if best_item is not None and best_ratio >= 0.75:
        return best_item
    return None


def pick_date(message):
    """(date_str, precise) from a CrossRef message.

    Prefers the most specific date (year+month+day) among the publication
    fields; ties are broken by field priority. 'precise' is True when the
    chosen date carried an explicit day.
    """
    best = None  # ((n_parts, -field_index), date_str, precise)
    for i, field in enumerate(DATE_FIELDS):
        parts = (message.get(field) or {}).get("date-parts") or []
        if not parts or not parts[0]:
            continue
        p = parts[0]
        y = p[0]
        m = p[1] if len(p) > 1 else 1
        d = p[2] if len(p) > 2 else 1
        key = (len(p), -i)
        if best is None or key > best[0]:
            best = (key, "%04d-%02d-%02d" % (y, m, d), len(p) >= 3)
    if best is None:
        return None, False
    return best[1], best[2]


def pick_venue(message):
    for field in ("container-title", "short-container-title"):
        value = message.get(field) or []
        if value:
            return html.unescape(str(value[0])).strip()
    return ""


def clean_abstract(raw):
    """Strip JATS/XML tags and entities from a CrossRef abstract."""
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------
# Entry generation

def classify_category(title, abstract):
    text = "%s %s" % (title, abstract).lower()
    for category, keywords in (
        ("Parasite", PARASITE_KEYWORDS),
        ("Vir_tech", VIR_TECH_KEYWORDS),
        ("Ae_habit", VECTOR_FIELD_KEYWORDS),
        ("Ev_denv", EV_DENV_KEYWORDS),
        ("Ae_habit", AE_HABIT_KEYWORDS),
    ):
        if any(k in text for k in keywords):
            return category
    return "Ae_habit"


def slugify(title, max_tokens=8):
    """First ~8 words of the title, hyphenated, punctuation stripped.

    Matches the naming convention of the existing files, e.g.
    'An actionable field practice integrated with real-time ...' ->
    'An-actionable-field-practice-integrated-with-real'.
    """
    parts = []
    for token in re.split(r"[\s\-\u2013\u2014]+", title):
        token = re.sub(r"[^\w]", "", token, flags=re.UNICODE)
        if token:
            parts.append(token)
            if len(parts) >= max_tokens:
                break
    return "-".join(parts)


def build_md(title, category, date_str, slug, venue, doi, abstract):
    """Render the markdown entry in the site's standard format."""
    permalink = "%s-%s" % (date_str, slug)
    yaml_title = title.replace("\\", "\\\\").replace('"', '\\"')
    if venue:
        venue_line = "venue: '%s'" % venue.replace("'", "''")
    else:
        venue_line = "venue: 'TODO: venue'"
    if doi:
        paperurl_line = "paperurl: 'https://doi.org/%s'" % doi
    else:
        paperurl_line = "paperurl: 'TODO: paperurl'"

    lines = [
        "---",
        'title: "%s"' % yaml_title,
        "collection: publications",
        "category: %s" % category,
        "permalink: /publication/%s" % permalink,
        "#excerpt: 'This paper is about the number 1. The number 2 is left for future work.'",
        "date: %s" % date_str,
        venue_line,
        paperurl_line,
        "image: 'images/publications/%s.png'" % permalink,
        "---",
    ]
    body = [abstract if abstract else TODO_ABSTRACT]
    if doi:
        body.append("https://doi.org/%s" % doi)
    return "\n".join(lines) + "\n" + "\n\n".join(body) + "\n"


def write_file(path, content):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)


# --------------------------------------------------------------------------
# Main

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate _publications/*.md entries from new PDFs in files/.")
    parser.add_argument("--dry-run", action="store_true",
                        help="only report what would be done, write nothing")
    args = parser.parse_args(argv)

    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    if not FILES_DIR.is_dir():
        print("!! files/ directory not found: %s" % FILES_DIR)
        return 1
    PUBS_DIR.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(p for p in FILES_DIR.iterdir()
                  if p.is_file() and p.suffix.lower() == ".pdf")
    if not pdfs:
        print("No PDFs found in files/ - nothing to do.")
        return 0

    # ---- index existing entries -------------------------------------
    existing_contents = {}
    for md_path in PUBS_DIR.glob("*.md"):
        try:
            existing_contents[md_path.name] = md_path.read_text(
                encoding="utf-8", errors="replace")
        except Exception:
            existing_contents[md_path.name] = ""
    existing_dois = set()
    existing_titles = set()
    for content in existing_contents.values():
        for m in re.finditer(r"doi\.org/(" + DOI_BODY + r")", content, re.IGNORECASE):
            existing_dois.add(m.group(1).rstrip(".,;").lower())
        m = re.search(r'^title:\s*"(.*)"\s*$', content, re.MULTILINE)
        if m:
            existing_titles.add(m.group(1).strip().lower())

    # existing entries still waiting for an abstract
    todo_entries = [(name, content) for name, content in existing_contents.items()
                    if TODO_ABSTRACT in content]

    pdf_texts = {}
    pdf_dois = {}
    created, failed = [], []
    review_notes = []

    # ---- pass 1: create entries for new PDFs ------------------------
    for pdf in pdfs:
        print("\n== %s" % pdf.name)
        text = pdf_text(pdf)
        pdf_texts[pdf.name] = text
        doi_key = find_doi(text)

        try:
            meta = crossref_by_doi(doi_key)
            fname_title = title_from_filename(pdf)
            if meta is not None:
                # The DOI regex may have picked up a cited reference; if the
                # DOI's title disagrees with the filename, trust a title
                # search result instead (when one matches well).
                cr_title = (meta.get("title") or [""])[0]
                if fname_title and difflib.SequenceMatcher(
                        None, fname_title.lower(), cr_title.lower()).ratio() < 0.45:
                    search_meta = crossref_search(fname_title)
                    if search_meta is not None:
                        meta = search_meta
            else:
                meta = crossref_search(fname_title)
        except CrossrefUnavailable as exc:
            print("   !! CrossRef unreachable (%s) - will retry on the next run" % exc)
            failed.append(pdf.name)
            continue

        doi_raw = ""
        if meta is not None:
            doi_raw = str(meta.get("DOI") or "").strip()
        if not doi_raw and doi_key:
            doi_raw = doi_key
        if doi_raw:
            doi_key = doi_raw.lower()
            pdf_dois[pdf.name] = doi_key

        if doi_key and doi_key in existing_dois:
            print("   -> already listed in _publications/ (DOI match) - skipped")
            continue

        title = ""
        if meta is not None:
            titles = meta.get("title") or []
            if titles:
                title = str(titles[0]).strip()
        if not title:
            title = title_from_filename(pdf)
        if title.lower() in existing_titles:
            print("   -> already listed in _publications/ (title match) - skipped")
            continue

        venue = pick_venue(meta) if meta is not None else ""
        date_str, precise = pick_date(meta) if meta is not None else (None, False)
        notes = []
        if not date_str:
            year = year_from_filename(pdf)
            date_str = "%s-01-01" % year if year else date.today().isoformat()
            notes.append("date is a fallback (no CrossRef date) - please verify")
        elif not precise:
            notes.append("CrossRef only had year/month - please verify the day")
        if meta is None:
            notes.append("no CrossRef record found - title/venue/DOI need manual completion")
        if not venue:
            notes.append("venue missing - please fill in")
        if not doi_raw:
            notes.append("DOI not found - please fill in paperurl")

        abstract = clean_abstract(meta.get("abstract")) if meta is not None else ""
        if not abstract:
            abstract = extract_abstract(text)
            if abstract:
                notes.append("abstract extracted heuristically from the PDF - please verify")

        category = classify_category(title, abstract)
        notes.append("category auto-assigned as %s - please review" % category)

        slug = slugify(title) or "untitled"
        md_name = "%s-%s.md" % (date_str, slug)
        md_path = PUBS_DIR / md_name
        if md_path.exists():
            print("   -> %s already exists - skipped" % md_name)
            continue

        if args.dry_run:
            print("   [dry-run] would write _publications/%s" % md_name)
        else:
            write_file(md_path, build_md(title, category, date_str, slug,
                                         venue, doi_raw, abstract))
            print("   -> wrote _publications/%s" % md_name)
        created.append(md_name)
        review_notes.append((md_name, notes))

    # ---- pass 2: backfill TODO abstracts from matching PDFs ---------
    backfilled = []
    if todo_entries and not args.dry_run:
        for name, content in todo_entries:
            m = re.search(r"paperurl:\s*'https?://doi\.org/([^']+)'", content)
            md_doi = m.group(1).rstrip(".,;").lower() if m else None
            tmatch = re.search(r'^title:\s*"(.*)"\s*$', content, re.MULTILINE)
            md_title = tmatch.group(1).strip() if tmatch else ""
            source_pdf, source_text = None, ""
            for pdf in pdfs:
                if md_doi and pdf_dois.get(pdf.name) == md_doi:
                    source_pdf, source_text = pdf, pdf_texts.get(pdf.name, "")
                    break
            if not source_text and md_title:
                for pdf in pdfs:
                    ft = title_from_filename(pdf)
                    if ft and difflib.SequenceMatcher(
                            None, ft.lower(), md_title.lower()).ratio() >= 0.6:
                        source_pdf, source_text = pdf, pdf_texts.get(pdf.name, "")
                        break
            if not source_text:
                continue
            abstract = extract_abstract(source_text)
            if not abstract:
                continue
            write_file(PUBS_DIR / name,
                       content.replace(TODO_ABSTRACT, abstract, 1))
            backfilled.append(name)
            print("\n== %s" % name)
            if source_pdf is not None:
                print("   -> abstract backfilled from %s (please verify)" % source_pdf.name)
            else:
                print("   -> abstract backfilled (please verify)")

    # ---- report ------------------------------------------------------
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print("PDFs scanned:         %d" % len(pdfs))
    print("entries created:      %d" % len(created))
    for name in created:
        print("   + _publications/%s" % name)
    print("abstracts backfilled: %d" % len(backfilled))
    for name in backfilled:
        print("   ~ _publications/%s" % name)
    if failed:
        print("failed (retry next run): %d" % len(failed))
        for name in failed:
            print("   ! %s" % name)
    if review_notes:
        print("\nManual follow-up needed for new entries:")
        for name, notes in review_notes:
            print("   * %s" % name)
            print("     - add a figure image at images/publications/%s.png" % name[:-3])
            for n in notes:
                print("     - %s" % n)
    if not created and not backfilled and not failed:
        print("Nothing new - all PDFs already have their publication entries.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
