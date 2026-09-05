#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Générateur de blog statique depuis Markdown (multi-langues FR/EN).

Entrée:
  content/posts/<key>/fr.md
  content/posts/<key>/en.md   (optionnel)
  content/posts/<key>/images/...   (images locales référencées comme images/...)

Front matter YAML requis:
  title: "..."        (obligatoire)
  date: "YYYY-MM-DD"  (obligatoire)
  slug: "..."         (obligatoire, par langue)
  lang: "fr"|"en"     (obligatoire)
  key:  "..."         (obligatoire; regroupe les traductions)

Templates (dans le dépôt du générateur):
  templates/base.html
  templates/index.html
  templates/post.html

Contenu / configuration / assets (dans le projet qui utilise le générateur):
  content/...
  config.yaml
  assets/style.css
  assets/theme.js

Le chemin du CSS dans config.yaml peut être un nom historique (style8.css),
un chemin relatif à config.yaml (assets/style8.css), ou un chemin absolu.

Sortie:
  dist/index.html
  dist/<slug>/index.html
  dist/en/index.html
  dist/en/<slug>/index.html
  dist/assets/style.css
  dist/assets/theme.js
  dist/assets/img/<fichier.fingerprint.ext>

Commande depuis la racine du projet consommateur:
  python generator/build.py build --config config.yaml
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape
import markdown as md


GENERATOR_ROOT = Path(__file__).resolve().parent

# Ces chemins sont initialisés à partir du fichier de configuration.
# Le générateur peut ainsi vivre dans un submodule sans imposer sa propre
# arborescence au projet consommateur.
PROJECT_ROOT = Path.cwd().resolve()
CONFIG_FILE = PROJECT_ROOT / "config.yaml"
POSTS_DIR = PROJECT_ROOT / "content"
DIST_DIR = PROJECT_ROOT / "dist"
ASSETS_DIR = DIST_DIR / "assets"
ASSETS_SRC_DIR = PROJECT_ROOT / "assets"
TEMPLATES_DIR = GENERATOR_ROOT / "templates"


def configure_paths(config_path: Path) -> None:
    """Configure les chemins du projet à partir de l'emplacement du YAML."""
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
        return f"{minutes} min" if self.lang == "en" else f"{minutes} min de lecture"


FRONT_MATTER_RE = re.compile(r"^\s*---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
IMG_MD_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
IMG_HTML_RE = re.compile(r'(<img\b[^>]*\bsrc=["\'])([^"\']+)(["\'])', re.IGNORECASE)


def ensure_clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def parse_front_matter(markdown_text: str, path: Path) -> Tuple[Dict[str, Any], str]:
    m = FRONT_MATTER_RE.match(markdown_text)
    if not m:
        raise ValueError(f"{path}: front matter YAML manquant.")
    fm_raw, body = m.group(1), m.group(2)
    fm = yaml.safe_load(fm_raw) or {}
    if not isinstance(fm, dict):
        raise ValueError(f"{path}: front matter invalide.")
    return fm, body


def parse_date(value: Any, path: Path) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    raise ValueError(f"{path}: date invalide.")


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
    Copie les images référencées en local (images/...) vers dist/assets/img avec fingerprint,
    puis réécrit les URLs dans le markdown (ou HTML inline) pour pointer vers ../../assets/img/.. selon profondeur.
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
    site.setdefault("title", "Mon Blog")
    site.setdefault("tagline", "")
    site.setdefault("author", "")
    # Pagination (page d'accueil) — par défaut 10 posts/page
    pagination = data["pagination"] if isinstance(data.get("pagination"), dict) else {}
    pagination.setdefault("posts_per_page", 10)
    data["pagination"] = pagination
    langs = [Lang(code=l["code"], label=l.get("label", l["code"].upper()), path=l.get("path", f"/{l['code']}/")) for l in data["languages"]]
    return data, langs


def rel_url(from_dir: Path, to_dir: Path) -> str:
    """Retourne une URL relative vers un dossier (avec trailing slash)."""
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
    """Résout un asset depuis le projet consommateur.

    Formats acceptés:
      - chemin absolu: /srv/blog/assets/style.css
      - chemin relatif à config.yaml: assets/style.css
      - ancien format: style.css (résolu dans <projet>/assets/)

    Les variables d'environnement et ``~`` sont développés.
    """
    raw = str(value or default_name).strip()
    raw = os.path.expandvars(os.path.expanduser(raw))
    path = Path(raw)

    if path.is_absolute():
        return path.resolve()

    relative_to_project = (PROJECT_ROOT / path).resolve()
    if relative_to_project.exists() or len(path.parts) > 1:
        return relative_to_project

    # Compatibilité avec l'ancien YAML: style: "style8.css"
    return (ASSETS_SRC_DIR / path).resolve()


def copy_static_assets(style: str = "style.css", theme: str = "theme.js") -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    style_src = resolve_asset_path(style, "style.css")
    if not style_src.exists() or not style_src.is_file():
        raise FileNotFoundError(f"Fichier CSS manquant: {style_src}")
    shutil.copy2(style_src, ASSETS_DIR / "style.css")

    theme_src = resolve_asset_path(theme, "theme.js")
    if not theme_src.exists() or not theme_src.is_file():
        raise FileNotFoundError(f"Fichier theme.js manquant: {theme_src}")
    shutil.copy2(theme_src, ASSETS_DIR / "theme.js")


def build() -> None:
    cfg, languages = load_config()
    menu = cfg.get("menu", [])
    site = cfg["site"]
    posts_per_page = int(cfg.get("pagination", {}).get("posts_per_page", 10) or 10)
    if posts_per_page <= 0:
        posts_per_page = 10

    ensure_clean_dir(DIST_DIR)
    copy_static_assets(cfg.get("style", "style.css"), cfg.get("theme", "theme.js"))

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
            raise ValueError(f"{md_path}: champs requis manquants (title, date, slug, lang, key).")
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
                translations.append({"label": "EN", "url": f"../en/{langs_map['en']['slug']}/"})
            if lang_code == "en" and "fr" in langs_map:
                translations.append({"label": "FR", "url": f"../../{langs_map['fr']['slug']}/"})

            depth = 1 if lang_code == "fr" else 2
            rel_from_post = compute_rel(depth)
            processed = process_images_in_text(rp["body"], rp["path"], rel_from_post)
            html = md.markdown(processed, extensions=["fenced_code", "tables", "toc"], output_format="html5")
            excerpt = make_excerpt(html)
            posts.append(Post(key=key, lang=lang_code, title=rp["title"], slug=rp["slug"], date=rp["date"], show_date=bool(rp.get("show_date", True)), show_reading_time=bool(rp.get("show_reading_time", True)), listed=bool(rp.get("listed", True)), html=html, excerpt=excerpt, translations=translations))

    posts_by_lang: Dict[str, List[Post]] = {}
    for p in posts:
        posts_by_lang.setdefault(p.lang, []).append(p)
    for lc in posts_by_lang:
        posts_by_lang[lc].sort(key=lambda x: x.date, reverse=True)

    # Pré-calcul pagination par langue (pour le sélecteur de langue sur les pages paginées)
    index_posts_by_lang: Dict[str, List[Post]] = {
        lc: [pp for pp in posts_by_lang.get(lc, []) if bool(getattr(pp, "listed", True))]
        for lc in [l.code for l in languages]
    }
    total_pages_by_lang: Dict[str, int] = {
        lc: max(1, (len(index_posts_by_lang.get(lc, [])) + posts_per_page - 1) // posts_per_page)
        for lc in [l.code for l in languages]
    }


    # Pré-calcul: retrouver un post par (key, lang) pour le menu
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
        """Construit un menu 'prêt à afficher' (href relatifs + item actif).
        Supporte:
          - href: (http/https, mailto, /, /en/, chemins internes)
          - post_key / post: référence à un post par sa key (content/posts/<key>/...).
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
                # lien vers un article identifié par sa key
                target_dir = post_dir_by_key_lang.get((str(post_key), lang_code))
                if target_dir is None:
                    # fallback: première langue dispo
                    target_dir = post_dir_by_key_lang.get((str(post_key), "fr")) or post_dir_by_key_lang.get((str(post_key), "en"))
                if target_dir is not None:
                    href = rel_url(out_dir, target_dir)

            elif isinstance(href_cfg, str) and (href_cfg == "/" or href_cfg == "/en/"):
                target_dir = home_dir if href_cfg == ("/" if lang_code == "fr" else "/en/") else (DIST_DIR if href_cfg == "/" else (DIST_DIR / "en"))
                href = rel_url(out_dir, target_dir)

            elif isinstance(href_cfg, str) and href_cfg.startswith("/") and not (href_cfg.startswith("mailto:") or href_cfg.startswith("http://") or href_cfg.startswith("https://")):
                # chemin interne type "/slug/" ou "/en/slug/"
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
                # si la page courante est ce post (par slug)
                slug_here = current_slug
                slug_target = None
                if target_dir is not None:
                    # target_dir name is slug folder
                    slug_target = target_dir.name
                is_active = (slug_target == slug_here)
            elif is_index and target_dir is not None and (target_dir == home_dir or target_dir == DIST_DIR or target_dir == (DIST_DIR / "en")):
                # pages d'accueil/pagination -> "Blog" actif (ou l'item qui pointe vers la home)
                is_active = True
            elif target_dir is not None and target_dir == out_dir:
                is_active = True

            view.append({"label": label, "href": href, "active": is_active})
        return view

    def resolve_lang_links_for_index(out_dir: Path, page_num: int) -> Dict[str, str]:
        """Retourne, pour chaque langue, le lien vers la "même" page d'index si elle existe.
        Sinon, fallback vers la home de la langue."""
        links: Dict[str, str] = {}
        for l in languages:
            lc = l.code
            lang_root = DIST_DIR if lc == "fr" else (DIST_DIR / lc)
            # Même numéro de page si possible
            if page_num <= total_pages_by_lang.get(lc, 1):
                target_dir = lang_root if page_num == 1 else (lang_root / "page" / str(page_num))
            else:
                target_dir = lang_root
            links[lc] = rel_url(out_dir, target_dir) + "index.html"
        return links

    def resolve_lang_links_for_post(out_dir: Path, post_key: str) -> Dict[str, str]:
        """Retourne, pour chaque langue, le lien vers la traduction du post si elle existe.
        Sinon, fallback vers la home de la langue."""
        links: Dict[str, str] = {}
        for l in languages:
            lc = l.code
            lang_root = DIST_DIR if lc == "fr" else (DIST_DIR / lc)
            target_dir = post_dir_by_key_lang.get((post_key, lc))
            if target_dir is None:
                target_dir = lang_root
            links[lc] = rel_url(out_dir, target_dir) + "index.html"
        return links

    # Render indexes (avec pagination)
    for lang in languages:
        lc = lang.code
        lang_posts = posts_by_lang.get(lc, [])

        lang_root = DIST_DIR if lc == "fr" else (DIST_DIR / lc)
        lang_root.mkdir(parents=True, exist_ok=True)

        index_posts = index_posts_by_lang.get(lc, [])

        total_pages = max(1, (len(index_posts) + posts_per_page - 1) // posts_per_page)
        for page_num in range(1, total_pages + 1):
            # Dossier de sortie pour cette page
            out_dir = lang_root if page_num == 1 else (lang_root / "page" / str(page_num))
            out_dir.mkdir(parents=True, exist_ok=True)

            # Profondeur pour atteindre dist/assets
            # fr: index=0, page/N=2 ; en: index=1, page/N=3
            depth_to_dist = 0 if lc == "fr" else 1
            if page_num > 1:
                depth_to_dist += 2
            rel = compute_rel(depth_to_dist)

            # Lien "Accueil" (dans le header)
            home_href = "./index.html" if page_num == 1 else rel_url(out_dir, lang_root) + "index.html"

            # Slice des posts affichés
            start = (page_num - 1) * posts_per_page
            end = start + posts_per_page
            page_posts = index_posts[start:end]

            # URL relative depuis la page d'index vers les posts (dans le dossier de la langue)
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
        )
        (out_dir / "index.html").write_text(post_html, encoding="utf-8")

    print(f"✅ Build terminé: {DIST_DIR}")


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="Générateur de blog statique FR/EN")
    parser.add_argument("command", nargs="?", default="build", choices=["build"])
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Fichier YAML du projet consommateur (défaut: ./config.yaml)",
    )
    args = parser.parse_args(argv[1:])

    configure_paths(Path(args.config))
    if not CONFIG_FILE.exists():
        parser.error(f"fichier de configuration introuvable: {CONFIG_FILE}")

    if args.command == "build":
        build()
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
