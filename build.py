#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Static blog generator from Markdown (multilingual FR/EN).

Input:
  content/posts/<key>/fr.md
  content/posts/<key>/en.md   (optional)
  content/posts/<key>/images/...   (local images referenced as images/...)

Required YAML front matter:
  title: "..."        (required)
  date: "YYYY-MM-DD"  (required)
  slug: "..."         (required, per language)
  lang: "fr"|"en"     (required)
  key:  "..."         (required; groups translations)

Templates (in the generator repository):
  templates/base.html
  templates/index.html
  templates/post.html

Content / configuration / assets (in the project using the generator):
  content/...
  config.yaml
  assets/style.css
  assets/theme.js

The CSS path in config.yaml can be a legacy name (style8.css),
a path relative to config.yaml (assets/style8.css), or an absolute path.

Output:
  dist/index.html
  dist/<slug>/index.html
  dist/en/index.html
  dist/en/<slug>/index.html
  dist/assets/style.css
  dist/assets/theme.js
  dist/assets/img/<file.fingerprint.ext>
  dist/sitemap.xml       (if site.url is configured)
  dist/robots.txt        (if site.url is configured)
  dist/rss.xml           (FR, if site.url is configured)
  dist/en/rss.xml        (EN, if site.url is configured)
  dist/404.html           (always generated)

Command from the consumer project root:
  python generator/build.py build --config config.yaml
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, date, time, timezone
from pathlib import Path
from email.utils import format_datetime
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urljoin
import xml.etree.ElementTree as ET

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape
import markdown as md


GENERATOR_ROOT = Path(__file__).resolve().parent

# These paths are initialized from the configuration file.
# This allows the generator to live in a submodule without imposing its own
# directory structure on the consumer project.
PROJECT_ROOT = Path.cwd().resolve()
CONFIG_FILE = PROJECT_ROOT / "config.yaml"
POSTS_DIR = PROJECT_ROOT / "content"
DIST_DIR = PROJECT_ROOT / "dist"
ASSETS_DIR = DIST_DIR / "assets"
ASSETS_SRC_DIR = PROJECT_ROOT / "assets"
TEMPLATES_DIR = GENERATOR_ROOT / "templates"


def configure_paths(config_path: Path) -> None:
    """Configure project paths from the YAML file location."""
    global PROJECT_ROOT, CONFIG_FILE, POSTS_DIR, DIST_DIR, ASSETS_DIR, ASSETS_SRC_DIR

    CONFIG_FILE = config_path.expanduser().resolve()
    PROJECT_ROOT = CONFIG_FILE.parent
    POSTS_DIR = PROJECT_ROOT / "content"
    DIST_DIR = PROJECT_ROOT / "dist"
    ASSETS_DIR = DIST_DIR / "assets"
    ASSETS_SRC_DIR = PROJECT_ROOT / "assets"


@dataclass
class Lang:
    code: str
    label: str
    path: str


@dataclass
class Post:
    key: str
    lang: str
    title: str
    slug: str
    date: date
    show_date: bool
    show_reading_time: bool
    listed: bool
    html: str
    excerpt: str
    translations: List[dict]

    @property
    def date_iso(self) -> str:
        return self.date.isoformat()

    @property
    def date_human(self) -> str:
        return self.date.strftime("%d/%m/%Y")

    @property
    def reading_time(self) -> str:
        words = max(1, len(re.findall(r"\w+", self.html)))
        minutes = max(1, int(round(words / 200)))
        return f"{minutes} min de lecture" if self.lang == "fr" else f"{minutes} min read"


FRONT_MATTER_RE = re.compile(r"^\s*---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
IMG_MD_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
IMG_HTML_RE = re.compile(r'(<img\b[^>]*\bsrc=["\'])([^"\']+)(["\'])', re.IGNORECASE)
IMG_SRC_RE = re.compile(r'<img\b[^>]*\bsrc=["\']([^"\']+)["\']', re.IGNORECASE)


GENERIC_LINK_TEXTS = {
    "cliquez ici", "cliquer ici", "ici", "en savoir plus", "lire plus", "plus",
    "click here", "here", "learn more", "read more", "more",
}


@dataclass(frozen=True)
class AccessibilityIssue:
    severity: str
    message: str


class AccessibilityHTMLParser(HTMLParser):
    """Small dependency-free audit for article HTML fragments."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.issues: List[AccessibilityIssue] = []
        self.headings: List[int] = []
        self._link_depth = 0
        self._link_text: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        attrs_map = {name.lower(): value for name, value in attrs}
        tag = tag.lower()

        if tag == "img":
            if "alt" not in attrs_map:
                self.issues.append(AccessibilityIssue("warning", "image missing an alt attribute"))
            elif not str(attrs_map.get("alt") or "").strip():
                self.issues.append(AccessibilityIssue("advisory", "image has an empty alt attribute (valid only if the image is decorative)"))
            if self._link_depth and attrs_map.get("alt"):
                self._link_text.append(str(attrs_map["alt"]))

        elif tag == "iframe" and not str(attrs_map.get("title") or "").strip():
            self.issues.append(AccessibilityIssue("warning", "iframe missing a title attribute"))

        elif tag == "a":
            self._link_depth += 1
            if self._link_depth == 1:
                self._link_text = []

        elif re.fullmatch(r"h[1-6]", tag):
            self.headings.append(int(tag[1]))

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._link_depth:
            if self._link_depth == 1:
                label = re.sub(r"\s+", " ", "".join(self._link_text)).strip().lower().strip(" .,:;!?…")
                if label in GENERIC_LINK_TEXTS:
                    self.issues.append(AccessibilityIssue("warning", f"link text is too vague: “{label}”"))
            self._link_depth -= 1
            if self._link_depth == 0:
                self._link_text = []

    def handle_data(self, data: str) -> None:
        if self._link_depth:
            self._link_text.append(data)


def normalize_article_headings(html: str) -> str:
    """The page template already owns the h1, so body-level h1 become h2."""
    html = re.sub(r"<h1(\b[^>]*)>", r"<h2\1>", html, flags=re.IGNORECASE)
    return re.sub(r"</h1\s*>", "</h2>", html, flags=re.IGNORECASE)


def audit_accessibility_html(html: str) -> List[AccessibilityIssue]:
    parser = AccessibilityHTMLParser()
    parser.feed(html)
    issues = list(parser.issues)

    previous = 1  # the article title in post.html
    for level in parser.headings:
        if level > previous + 1:
            issues.append(AccessibilityIssue("warning", f"heading level skipped: h{previous} to h{level}"))
        previous = level

    # Keep warnings readable when the same issue occurs several times.
    return list(dict.fromkeys(issues))


def ensure_clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def parse_front_matter(markdown_text: str, path: Path) -> Tuple[Dict[str, Any], str]:
    m = FRONT_MATTER_RE.match(markdown_text)
    if not m:
        raise ValueError(f"{path}: missing YAML front matter.")
    fm_raw, body = m.group(1), m.group(2)
    fm = yaml.safe_load(fm_raw) or {}
    if not isinstance(fm, dict):
        raise ValueError(f"{path}: invalid front matter.")
    return fm, body


def parse_date(value: Any, path: Path) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    raise ValueError(f"{path}: invalid date.")


def make_excerpt(html: str, max_chars: int = 180) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= max_chars else (text[: max_chars - 1].rstrip() + "…")


def is_external(url: str) -> bool:
    u = url.lower().strip()
    return u.startswith("http://") or u.startswith("https://") or u.startswith("data:") or u.startswith("mailto:")


def clean_url(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("<") and raw.endswith(">"):
        raw = raw[1:-1].strip()
    parts = raw.split()
    return unquote(parts[0])


def fingerprint_copy(src: Path, dst_dir: Path) -> str:
    content = src.read_bytes()
    h = hashlib.sha256(content).hexdigest()[:10]
    dst_dir.mkdir(parents=True, exist_ok=True)
    out_name = f"{src.stem}.{h}{src.suffix.lower()}"
    out_path = dst_dir / out_name
    if not out_path.exists():
        out_path.write_bytes(content)
    return out_name


def process_images_in_text(body: str, md_path: Path, rel_from_post: str) -> str:
    """
    Copy locally referenced images (images/...) to dist/assets/img with fingerprinting,
    then rewrite URLs in Markdown (or inline HTML) to point to ../../assets/img/.. based on depth.
    """
    out_img_dir = ASSETS_DIR / "img"

    def copy_and_rewrite(url: str) -> Optional[str]:
        url = clean_url(url)
        if is_external(url) or url.startswith("/"):
            return None
        src = (md_path.parent / url).resolve()
        if not src.exists() or not src.is_file():
            return None
        out_name = fingerprint_copy(src, out_img_dir)
        return f"{rel_from_post}/assets/img/{out_name}"

    def md_repl(m: re.Match) -> str:
        alt, raw = m.group(1), m.group(2)
        new_url = copy_and_rewrite(raw)
        return m.group(0) if not new_url else f"![{alt}]({new_url})"

    body = IMG_MD_RE.sub(md_repl, body)

    def html_repl(m: re.Match) -> str:
        prefix, src, suffix = m.group(1), m.group(2), m.group(3)
        new_url = copy_and_rewrite(src)
        return m.group(0) if not new_url else f"{prefix}{new_url}{suffix}"

    body = IMG_HTML_RE.sub(html_repl, body)
    return body


def load_config() -> Tuple[Dict[str, Any], List[Lang]]:
    data = yaml.safe_load(read_text(CONFIG_FILE)) if CONFIG_FILE.exists() else {}
    data = data or {}
    data.setdefault("site", {})
    data.setdefault("menu", [])
    data.setdefault("pagination", {})
    data.setdefault("languages", [{"code": "fr", "label": "FR", "path": "/"}, {"code": "en", "label": "EN", "path": "/en/"}])
    site = data["site"]
    site.setdefault("title", "My Blog")
    site.setdefault("tagline", "")
    site.setdefault("author", "")
    # Pagination (home page) — defaults to 10 posts/page
    pagination = data["pagination"] if isinstance(data.get("pagination"), dict) else {}
    pagination.setdefault("posts_per_page", 10)
    data["pagination"] = pagination
    langs = [Lang(code=l["code"], label=l.get("label", l["code"].upper()), path=l.get("path", f"/{l['code']}/")) for l in data["languages"]]
    return data, langs


def rel_url(from_dir: Path, to_dir: Path) -> str:
    """Return a relative URL to a directory (with a trailing slash)."""
    rel = Path(os.path.relpath(str(to_dir), str(from_dir))).as_posix()
    if rel == ".":
        return "./"
    if not rel.startswith("."):
        rel = "./" + rel
    if not rel.endswith("/"):
        rel += "/"
    return rel


def iter_markdown_files() -> List[Path]:
    return sorted([p for p in POSTS_DIR.rglob("*.md") if p.is_file()]) if POSTS_DIR.exists() else []


def compute_rel(depth: int) -> str:
    return "." if depth == 0 else "/".join([".."] * depth)


def resolve_asset_path(value: str, default_name: str) -> Path:
    """Resolve an asset from the consumer project.

    Accepted formats:
      - absolute path: /srv/blog/assets/style.css
      - path relative to config.yaml: assets/style.css
      - legacy format: style.css (resolved in <project>/assets/)

    Environment variables and ``~`` are expanded.
    """
    raw = str(value or default_name).strip()
    raw = os.path.expandvars(os.path.expanduser(raw))
    path = Path(raw)

    if path.is_absolute():
        return path.resolve()

    relative_to_project = (PROJECT_ROOT / path).resolve()
    if relative_to_project.exists() or len(path.parts) > 1:
        return relative_to_project

    # Compatibility with the legacy YAML format: style: "style8.css"
    return (ASSETS_SRC_DIR / path).resolve()


def copy_static_assets(style: str = "style.css", theme: str = "theme.js") -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    favicon_src = POSTS_DIR / "favicon.ico"
    if favicon_src.is_file():
        shutil.copy2(favicon_src, DIST_DIR / "favicon.ico")

    og_image_src = POSTS_DIR / "og-image.jpg"
    if og_image_src.is_file():
        shutil.copy2(og_image_src, DIST_DIR / "og-image.jpg")

    style_src = resolve_asset_path(style, "style.css")
    if not style_src.exists() or not style_src.is_file():
        raise FileNotFoundError(f"Missing CSS file: {style_src}")
    shutil.copy2(style_src, ASSETS_DIR / "style.css")

    theme_src = resolve_asset_path(theme, "theme.js")
    if not theme_src.exists() or not theme_src.is_file():
        raise FileNotFoundError(f"Missing theme.js file: {theme_src}")
    shutil.copy2(theme_src, ASSETS_DIR / "theme.js")

    # Engine-owned accessibility baseline, always loaded after the visual theme.
    accessibility_css = GENERATOR_ROOT / "assets" / "accessibility.css"
    if not accessibility_css.is_file():
        raise FileNotFoundError(f"Missing accessibility stylesheet: {accessibility_css}")
    shutil.copy2(accessibility_css, ASSETS_DIR / "accessibility.css")


def resolve_site_url(site: Dict[str, Any]) -> str:
    """Return the public site URL without a trailing slash.

    Priority:
      1. site.url
      2. site.base_url (compatibility)
      3. SITE_URL environment variable
      4. CNAME file at the project root (GitHub Pages)
    """
    raw = str(site.get("url") or site.get("base_url") or os.environ.get("SITE_URL") or "").strip()
    if not raw:
        cname = PROJECT_ROOT / "CNAME"
        if cname.exists() and cname.is_file():
            host = read_text(cname).strip().splitlines()[0].strip()
            if host:
                raw = host if "://" in host else f"https://{host}"

    if not raw:
        return ""
    if not re.match(r"^https?://", raw, re.IGNORECASE):
        raw = "https://" + raw
    return raw.rstrip("/")


def absolute_url(site_url: str, path: str = "") -> str:
    """Build an absolute public URL from a site path."""
    path = str(path or "").strip("/")
    return f"{site_url}/{path}/" if path else f"{site_url}/"


def single_post_image_url(html: str, post_url: str) -> str:
    """Return the article image if it contains exactly one <img> tag."""
    if not post_url:
        return ""
    sources = [src.strip() for src in IMG_SRC_RE.findall(html) if src.strip()]
    if len(sources) != 1 or sources[0].lower().startswith("data:"):
        return ""
    return urljoin(post_url, sources[0])


def lang_root_path(lang_code: str) -> str:
    return "" if lang_code == "fr" else lang_code


def post_public_path(post: Post) -> str:
    root = lang_root_path(post.lang)
    return f"{root}/{post.slug}".strip("/")


def rss_public_path(lang_code: str) -> str:
    root = lang_root_path(lang_code)
    return f"{root}/rss.xml".strip("/")


def page_absolute_url(site_url: str, lang_code: str, page_num: int = 1) -> str:
    root = lang_root_path(lang_code)
    if page_num <= 1:
        return absolute_url(site_url, root)
    path = f"{root}/page/{page_num}" if root else f"page/{page_num}"
    return absolute_url(site_url, path)


def write_sitemap(site_url: str, languages: List[Lang], posts: List[Post]) -> None:
    """Generate an XML sitemap with hreflang alternates for translations."""
    sitemap_ns = "http://www.sitemaps.org/schemas/sitemap/0.9"
    xhtml_ns = "http://www.w3.org/1999/xhtml"
    ET.register_namespace("", sitemap_ns)
    ET.register_namespace("xhtml", xhtml_ns)

    urlset = ET.Element(ET.QName(sitemap_ns, "urlset"))
    latest_by_lang: Dict[str, Optional[date]] = {}
    for lang in languages:
        dates = [p.date for p in posts if p.lang == lang.code and p.listed]
        latest_by_lang[lang.code] = max(dates) if dates else None

    # Home pages by language.
    for lang in languages:
        entry = ET.SubElement(urlset, ET.QName(sitemap_ns, "url"))
        ET.SubElement(entry, ET.QName(sitemap_ns, "loc")).text = absolute_url(site_url, lang_root_path(lang.code))
        if latest_by_lang.get(lang.code):
            ET.SubElement(entry, ET.QName(sitemap_ns, "lastmod")).text = latest_by_lang[lang.code].isoformat()
        for alt in languages:
            ET.SubElement(
                entry,
                ET.QName(xhtml_ns, "link"),
                {
                    "rel": "alternate",
                    "hreflang": alt.code,
                    "href": absolute_url(site_url, lang_root_path(alt.code)),
                },
            )

    by_key_lang = {(p.key, p.lang): p for p in posts}
    for p in sorted(posts, key=lambda item: (item.date, item.lang, item.slug), reverse=True):
        entry = ET.SubElement(urlset, ET.QName(sitemap_ns, "url"))
        ET.SubElement(entry, ET.QName(sitemap_ns, "loc")).text = absolute_url(site_url, post_public_path(p))
        ET.SubElement(entry, ET.QName(sitemap_ns, "lastmod")).text = p.date_iso
        for alt in languages:
            translated = by_key_lang.get((p.key, alt.code))
            if translated is not None:
                ET.SubElement(
                    entry,
                    ET.QName(xhtml_ns, "link"),
                    {
                        "rel": "alternate",
                        "hreflang": alt.code,
                        "href": absolute_url(site_url, post_public_path(translated)),
                    },
                )

    tree = ET.ElementTree(urlset)
    ET.indent(tree, space="  ")
    tree.write(DIST_DIR / "sitemap.xml", encoding="utf-8", xml_declaration=True)


def write_rss_feed(site_url: str, site: Dict[str, Any], lang: Lang, posts: List[Post], max_items: int) -> None:
    """Generate an RSS 2.0 feed for one language."""
    atom_ns = "http://www.w3.org/2005/Atom"
    ET.register_namespace("atom", atom_ns)

    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = str(site.get("title", ""))
    ET.SubElement(channel, "link").text = absolute_url(site_url, lang_root_path(lang.code))
    description = str(site.get("tagline", "") or site.get("title", ""))
    ET.SubElement(channel, "description").text = description
    ET.SubElement(channel, "language").text = lang.code
    ET.SubElement(
        channel,
        ET.QName(atom_ns, "link"),
        {
            "href": f"{site_url}/{rss_public_path(lang.code)}",
            "rel": "self",
            "type": "application/rss+xml",
        },
    )

    feed_posts = [p for p in posts if p.lang == lang.code and p.listed][:max_items]
    if feed_posts:
        latest_dt = datetime.combine(feed_posts[0].date, time.min, tzinfo=timezone.utc)
        ET.SubElement(channel, "lastBuildDate").text = format_datetime(latest_dt)

    for p in feed_posts:
        item = ET.SubElement(channel, "item")
        url = absolute_url(site_url, post_public_path(p))
        ET.SubElement(item, "title").text = p.title
        ET.SubElement(item, "link").text = url
        ET.SubElement(item, "guid", {"isPermaLink": "true"}).text = url
        published = datetime.combine(p.date, time.min, tzinfo=timezone.utc)
        ET.SubElement(item, "pubDate").text = format_datetime(published)
        ET.SubElement(item, "description").text = p.excerpt

    out_path = DIST_DIR / rss_public_path(lang.code)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ")
    tree.write(out_path, encoding="utf-8", xml_declaration=True)


def write_robots(site_url: str, sitemap_enabled: bool = True) -> None:
    content = "User-agent: *\nAllow: /\n"
    if sitemap_enabled:
        content += f"\nSitemap: {site_url}/sitemap.xml\n"
    (DIST_DIR / "robots.txt").write_text(content, encoding="utf-8")


def build() -> None:
    cfg, languages = load_config()
    menu = cfg.get("menu", [])
    site = cfg["site"]
    site_url = resolve_site_url(site)
    rss_cfg = cfg.get("rss", {}) if isinstance(cfg.get("rss", {}), dict) else {}
    rss_enabled = bool(rss_cfg.get("enabled", True))
    rss_items = int(rss_cfg.get("items", 20) or 20)
    if rss_items <= 0:
        rss_items = 20
    sitemap_cfg = cfg.get("sitemap", {}) if isinstance(cfg.get("sitemap", {}), dict) else {}
    sitemap_enabled = bool(sitemap_cfg.get("enabled", True))
    accessibility_cfg = cfg.get("accessibility", {}) if isinstance(cfg.get("accessibility", {}), dict) else {}
    accessibility_audit = bool(accessibility_cfg.get("audit", True))
    accessibility_strict = bool(accessibility_cfg.get("strict", False))
    accessibility_normalize_headings = bool(accessibility_cfg.get("normalize_headings", True))
    accessibility_issues: List[Tuple[Path, AccessibilityIssue]] = []
    posts_per_page = int(cfg.get("pagination", {}).get("posts_per_page", 10) or 10)
    if posts_per_page <= 0:
        posts_per_page = 10

    ensure_clean_dir(DIST_DIR)
    copy_static_assets(cfg.get("style", "style.css"), cfg.get("theme", "theme.js"))
    og_image_url = f"{site_url}/og-image.jpg" if site_url and (POSTS_DIR / "og-image.jpg").is_file() else ""

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    now_year = datetime.now().year

    raw_posts = []
    for md_path in iter_markdown_files():
        fm, body = parse_front_matter(read_text(md_path), md_path)
        title = str(fm.get("title", "")).strip()
        slug = str(fm.get("slug", "")).strip()
        lang = str(fm.get("lang", "")).strip()
        key = str(fm.get("key", "")).strip()
        if not (title and slug and lang and key and fm.get("date")):
            raise ValueError(f"{md_path}: missing required fields (title, date, slug, lang, key).")
        d = parse_date(fm.get("date"), md_path)
        show_date = fm.get("show_date", fm.get("display_date", fm.get("showDate", None)))
        if show_date is None:
            # Support hide_date: true
            hide_date = fm.get("hide_date", fm.get("hideDate", False))
            show_date = not bool(hide_date)
        show_date = bool(show_date)

        show_reading_time = fm.get("show_reading_time", fm.get("display_reading_time", fm.get("showReadingTime", None)))
        if show_reading_time is None:
            hide_rt = fm.get("hide_reading_time", fm.get("hideReadingTime", False))
            show_reading_time = not bool(hide_rt)
        show_reading_time = bool(show_reading_time)

        listed = fm.get("listed", fm.get("show_in_list", fm.get("showInList", None)))
        if listed is None:
            # Support unlisted: true / hide_from_list: true
            unlisted = fm.get("unlisted", fm.get("hide_from_list", fm.get("hideFromList", False)))
            listed = not bool(unlisted)
        listed = bool(listed)
        raw_posts.append({"path": md_path, "title": title, "slug": slug, "lang": lang, "key": key, "date": d, "show_date": show_date, "show_reading_time": show_reading_time, "listed": listed, "body": body})

    # group by key -> lang -> post
    by_key: Dict[str, Dict[str, dict]] = {}
    for rp in raw_posts:
        by_key.setdefault(rp["key"], {})[rp["lang"]] = rp

    posts: List[Post] = []
    for key, langs_map in by_key.items():
        for lang_code, rp in langs_map.items():
            translations = []
            if lang_code == "fr" and "en" in langs_map:
                translations.append({"label": "EN", "lang": "en", "url": f"../en/{langs_map['en']['slug']}/"})
            if lang_code == "en" and "fr" in langs_map:
                translations.append({"label": "FR", "lang": "fr", "url": f"../../{langs_map['fr']['slug']}/"})

            depth = 1 if lang_code == "fr" else 2
            rel_from_post = compute_rel(depth)
            processed = process_images_in_text(rp["body"], rp["path"], rel_from_post)
            html = md.markdown(processed, extensions=["fenced_code", "tables", "toc"], output_format="html5")
            if accessibility_normalize_headings:
                html = normalize_article_headings(html)
            if accessibility_audit:
                for issue in audit_accessibility_html(html):
                    accessibility_issues.append((rp["path"], issue))
            excerpt = make_excerpt(html)
            posts.append(Post(key=key, lang=lang_code, title=rp["title"], slug=rp["slug"], date=rp["date"], show_date=bool(rp.get("show_date", True)), show_reading_time=bool(rp.get("show_reading_time", True)), listed=bool(rp.get("listed", True)), html=html, excerpt=excerpt, translations=translations))

    if accessibility_issues:
        strict_issues = []
        for issue_path, issue in accessibility_issues:
            marker = "⚠️ " if issue.severity == "warning" else "ℹ️ "
            print(f"{marker} A11Y: {issue_path}: {issue.message}", file=sys.stderr)
            if issue.severity == "warning":
                strict_issues.append((issue_path, issue))
        if accessibility_strict and strict_issues:
            raise ValueError(
                f"Accessibility audit failed with {len(strict_issues)} issue(s). "
                "Fix them or set accessibility.strict: false."
            )

    posts_by_lang: Dict[str, List[Post]] = {}
    for p in posts:
        posts_by_lang.setdefault(p.lang, []).append(p)
    for lc in posts_by_lang:
        posts_by_lang[lc].sort(key=lambda x: x.date, reverse=True)

    # Precompute pagination by language (for the language switcher on paginated pages)
    index_posts_by_lang: Dict[str, List[Post]] = {
        lc: [pp for pp in posts_by_lang.get(lc, []) if bool(getattr(pp, "listed", True))]
        for lc in [l.code for l in languages]
    }
    total_pages_by_lang: Dict[str, int] = {
        lc: max(1, (len(index_posts_by_lang.get(lc, [])) + posts_per_page - 1) // posts_per_page)
        for lc in [l.code for l in languages]
    }


    # Precompute post lookup by (key, lang) for the menu
    post_dir_by_key_lang: Dict[tuple, Path] = {}
    post_slug_by_key_lang: Dict[tuple, str] = {}
    for p in posts:
        if p.lang == "fr":
            ddir = DIST_DIR / p.slug
        else:
            ddir = DIST_DIR / "en" / p.slug
        post_dir_by_key_lang[(p.key, p.lang)] = ddir
        post_slug_by_key_lang[(p.key, p.lang)] = p.slug

    def resolve_menu_for_page(out_dir: Path, lang_code: str, current_slug: Optional[str], is_index: bool) -> List[dict]:
        """Build a display-ready menu (relative hrefs + active item).
        Supports:
          - href: (http/https, mailto, /, /en/, internal paths)
          - post_key / post: reference to a post by its key (content/posts/<key>/...).
        """
        home_dir = DIST_DIR if lang_code == "fr" else (DIST_DIR / "en")
        view = []
        for item in menu:
            label = item.get("label", "")
            href_cfg = item.get("href")
            post_key = item.get("post_key", item.get("post", item.get("key_post")))
            target_dir = None
            href = href_cfg

            if post_key:
                # Link to an article identified by its key
                target_dir = post_dir_by_key_lang.get((str(post_key), lang_code))
                if target_dir is None:
                    # Fallback: first available language
                    target_dir = post_dir_by_key_lang.get((str(post_key), "fr")) or post_dir_by_key_lang.get((str(post_key), "en"))
                if target_dir is not None:
                    href = rel_url(out_dir, target_dir)

            elif isinstance(href_cfg, str) and (href_cfg == "/" or href_cfg == "/en/"):
                target_dir = home_dir if href_cfg == ("/" if lang_code == "fr" else "/en/") else (DIST_DIR if href_cfg == "/" else (DIST_DIR / "en"))
                href = rel_url(out_dir, target_dir)

            elif isinstance(href_cfg, str) and href_cfg.startswith("/") and not (href_cfg.startswith("mailto:") or href_cfg.startswith("http://") or href_cfg.startswith("https://")):
                # Internal path such as "/slug/" or "/en/slug/"
                path = href_cfg.strip("/")
                if path.startswith("en/"):
                    target_dir = DIST_DIR / "en" / path[len("en/"):]
                elif path == "":
                    target_dir = DIST_DIR
                else:
                    target_dir = DIST_DIR / path
                href = rel_url(out_dir, target_dir)
              
            if href == "/" :
                href = home_ref

            # Active?
            is_active = False
            if post_key and current_slug:
                # If the current page is this post (by slug)
                slug_here = current_slug
                slug_target = None
                if target_dir is not None:
                    # target_dir name is slug folder
                    slug_target = target_dir.name
                is_active = (slug_target == slug_here)
            elif is_index and target_dir is not None and (target_dir == home_dir or target_dir == DIST_DIR or target_dir == (DIST_DIR / "en")):
                # Home/pagination pages -> "Blog" active (or the item pointing to the home page)
                is_active = True
            elif target_dir is not None and target_dir == out_dir:
                is_active = True

            view.append({"label": label, "href": href, "active": is_active})
        return view

    def resolve_lang_links_for_index(out_dir: Path, page_num: int) -> Dict[str, str]:
        """Return, for each language, the link to the same index page if it exists.
        Otherwise, fall back to that language's home page."""
        links: Dict[str, str] = {}
        for l in languages:
            lc = l.code
            lang_root = DIST_DIR if lc == "fr" else (DIST_DIR / lc)
            # Same page number when possible
            if page_num <= total_pages_by_lang.get(lc, 1):
                target_dir = lang_root if page_num == 1 else (lang_root / "page" / str(page_num))
            else:
                target_dir = lang_root
            links[lc] = rel_url(out_dir, target_dir) + "index.html"
        return links

    def resolve_lang_links_for_post(out_dir: Path, post_key: str) -> Dict[str, str]:
        """Return, for each language, the link to the post translation if it exists.
        Otherwise, fall back to that language's home page."""
        links: Dict[str, str] = {}
        for l in languages:
            lc = l.code
            lang_root = DIST_DIR if lc == "fr" else (DIST_DIR / lc)
            target_dir = post_dir_by_key_lang.get((post_key, lc))
            if target_dir is None:
                target_dir = lang_root
            links[lc] = rel_url(out_dir, target_dir) + "index.html"
        return links

    # Render indexes (with pagination)
    for lang in languages:
        lc = lang.code
        lang_posts = posts_by_lang.get(lc, [])

        lang_root = DIST_DIR if lc == "fr" else (DIST_DIR / lc)
        lang_root.mkdir(parents=True, exist_ok=True)

        index_posts = index_posts_by_lang.get(lc, [])

        total_pages = max(1, (len(index_posts) + posts_per_page - 1) // posts_per_page)
        for page_num in range(1, total_pages + 1):
            # Output directory for this page
            out_dir = lang_root if page_num == 1 else (lang_root / "page" / str(page_num))
            out_dir.mkdir(parents=True, exist_ok=True)

            # Depth needed to reach dist/assets
            # fr: index=0, page/N=2 ; en: index=1, page/N=3
            depth_to_dist = 0 if lc == "fr" else 1
            if page_num > 1:
                depth_to_dist += 2
            rel = compute_rel(depth_to_dist)

            # "Home" link (in the header)
            home_href = "./index.html" if page_num == 1 else rel_url(out_dir, lang_root) + "index.html"

            # Slice of displayed posts
            start = (page_num - 1) * posts_per_page
            end = start + posts_per_page
            page_posts = index_posts[start:end]

            # Relative URL from the index page to posts (inside the language directory)
            depth_in_lang = 0 if page_num == 1 else 2
            post_prefix = compute_rel(depth_in_lang)
            view_posts = []
            for p in page_posts:
                view_posts.append({
                    "title": p.title,
                    "date_iso": p.date_iso,
                    "date_human": p.date_human,
                    "show_date": bool(getattr(p, "show_date", True)),
                    "show_reading_time": bool(getattr(p, "show_reading_time", True)),
                    "reading_time": p.reading_time,
                    "excerpt": p.excerpt,
                    "url": f"{post_prefix}/{p.slug}/" if post_prefix != "." else f"./{p.slug}/",
                })

            # Pagination links
            def page_dir(n: int) -> Path:
                return lang_root if n == 1 else (lang_root / "page" / str(n))

            pages = []
            for n in range(1, total_pages + 1):
                pages.append({
                    "num": n,
                    "url": rel_url(out_dir, page_dir(n)),
                    "is_current": n == page_num,
                })

            prev_url = rel_url(out_dir, page_dir(page_num - 1)) if page_num > 1 else None
            next_url = rel_url(out_dir, page_dir(page_num + 1)) if page_num < total_pages else None

            index_html = env.get_template("index.html").render(
                page_title=site["title"],
                site=site,
                menu=resolve_menu_for_page(out_dir, lc, None, True),
                posts=view_posts,
                pagination={
                    "enabled": total_pages > 1,
                    "current": page_num,
                    "total": total_pages,
                    "prev_url": prev_url,
                    "next_url": next_url,
                    "pages": pages,
                },
                rel=rel,
                now_year=now_year,
                languages=[{"code": l.code, "label": l.label, "path": l.path} for l in languages],
                lang_links=resolve_lang_links_for_index(out_dir, page_num),
                lang={"code": lang.code, "label": lang.label, "path": lang.path},
                home_href=home_href,
                meta_description=site.get("tagline", ""),
                canonical_url=page_absolute_url(site_url, lc, page_num) if site_url else "",
                og_image_url=og_image_url,
                rss_url=(f"{site_url}/{rss_public_path(lc)}" if site_url and rss_enabled else ""),
                hreflang_urls=(
                    {l.code: page_absolute_url(site_url, l.code, page_num if page_num <= total_pages_by_lang.get(l.code, 1) else 1) for l in languages}
                    if site_url else {}
                ),
            )
            (out_dir / "index.html").write_text(index_html, encoding="utf-8")

    # Render posts
    for p in posts:
        if p.lang == "fr":
            out_dir = DIST_DIR / p.slug
            out_dir.mkdir(parents=True, exist_ok=True)
            rel = ".."
            home_href = "../index.html"
            lang_obj = {"code": "fr", "label": "FR", "path": "/"}
        else:
            out_dir = DIST_DIR / "en" / p.slug
            out_dir.mkdir(parents=True, exist_ok=True)
            rel = "../.."
            home_href = "../index.html"
            lang_obj = {"code": "en", "label": "EN", "path": "/en/"}

        post_url = absolute_url(site_url, post_public_path(p)) if site_url else ""
        article_og_image_url = single_post_image_url(p.html, post_url) or og_image_url
        json_ld: Dict[str, Any] = {
            "@context": "https://schema.org",
            "@type": "BlogPosting",
            "headline": p.title,
            "description": p.excerpt,
            "datePublished": p.date_iso,
            "inLanguage": p.lang,
        }
        if post_url:
            json_ld["url"] = post_url
            json_ld["mainEntityOfPage"] = {"@type": "WebPage", "@id": post_url}
        if site.get("author"):
            json_ld["author"] = {"@type": "Person", "name": str(site["author"])}
        if article_og_image_url:
            json_ld["image"] = article_og_image_url

        post_html = env.get_template("post.html").render(
            page_title=f"{p.title} — {site['title']}",
            site=site,
            menu=resolve_menu_for_page(out_dir, lang_obj['code'], p.slug, False),
            post={
                "title": p.title,
                "date_iso": p.date_iso,
                "date_human": p.date_human,
                    "show_date": bool(getattr(p, "show_date", True)),
                "show_reading_time": bool(getattr(p, "show_reading_time", True)),
                "reading_time": p.reading_time,
                "html": p.html,
                "translations": p.translations,
            },
            rel=rel,
            now_year=now_year,
            languages=[{"code": l.code, "label": l.label, "path": l.path} for l in languages],
            lang_links=resolve_lang_links_for_post(out_dir, p.key),
            lang=lang_obj,
            home_href=home_href,
            meta_description=p.excerpt,
            canonical_url=post_url,
            og_type="article",
            json_ld=json.dumps(json_ld, ensure_ascii=False, separators=(",", ":")),
            og_image_url=article_og_image_url,
            rss_url=(f"{site_url}/{rss_public_path(p.lang)}" if site_url and rss_enabled else ""),
            hreflang_urls=(
                {
                    l.code: absolute_url(site_url, post_public_path(next(pp for pp in posts if pp.key == p.key and pp.lang == l.code)))
                    for l in languages
                    if any(pp.key == p.key and pp.lang == l.code for pp in posts)
                }
                if site_url else {}
            ),
        )
        (out_dir / "index.html").write_text(post_html, encoding="utf-8")

    # Single root-level 404 page, suitable for static hosting providers.
    not_found_html = env.get_template("404.html").render(
        page_title=f"404 — {site['title']}",
        site=site,
        menu=[],
        rel=".",
        now_year=now_year,
        languages=[],
        lang_links={},
        lang={"code": "fr", "label": "FR", "path": "/"},
        home_href="./index.html",
        meta_description="Page not found",
        canonical_url="",
        og_image_url=og_image_url,
        rss_url="",
        hreflang_urls={},
    )
    (DIST_DIR / "404.html").write_text(not_found_html, encoding="utf-8")

    if site_url:
        if sitemap_enabled:
            write_sitemap(site_url, languages, posts)
        if rss_enabled:
            for lang in languages:
                write_rss_feed(site_url, site, lang, posts_by_lang.get(lang.code, []), rss_items)
        write_robots(site_url, sitemap_enabled=sitemap_enabled)
    elif sitemap_enabled or rss_enabled:
        print(
            "⚠️  SEO: site.url is missing. sitemap.xml and RSS feeds were not generated. "
            "Add site.url to config.yaml (or define SITE_URL).",
            file=sys.stderr,
        )

    print(f"✅ Build complete: {DIST_DIR}")


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="Static FR/EN blog generator")
    parser.add_argument("command", nargs="?", default="build", choices=["build"])
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="YAML file for the consumer project (default: ./config.yaml)",
    )
    args = parser.parse_args(argv[1:])

    configure_paths(Path(args.config))
    if not CONFIG_FILE.exists():
        parser.error(f"configuration file not found: {CONFIG_FILE}")

    if args.command == "build":
        build()
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
