# Static blog generator (FR/EN)

This repository contains only the reusable generator engine:

- `build.py`
- `templates/`
- `requirements.txt`

The generator also produces SEO/RSS files when the public site URL is known:

- `dist/sitemap.xml`
- `dist/robots.txt`
- `dist/rss.xml` (FR)
- `dist/en/rss.xml` (EN)
- `canonical`, `hreflang`, Open Graph, and RSS discovery tags in HTML pages

The content, YAML configuration, site assets, and GitHub Actions workflow remain in the consumer repository.

## Create a consumer repository

Example: [https://github.com/sfonteneau/blog.git](https://github.com/sfonteneau/blog.git)

## SEO, sitemap, and RSS feeds

To generate valid absolute URLs in the sitemap and RSS feeds, set the public site URL in the consumer project's `config.yaml`:

```yaml
site:
  title: "My Blog"
  tagline: "..."
  author: "..."
  url: "https://example.com"

# Optional: defaults to 20 articles per feed
rss:
  enabled: true
  items: 20

# Optional: enabled by default
sitemap:
  enabled: true
```

`site.base_url` is also accepted for compatibility. If it is not set, the generator uses the `SITE_URL` environment variable, then an optional `CNAME` file at the project root. Without a public URL, the build continues but prints a warning and does not write the sitemap/RSS files, preventing SEO files with invalid URLs.

For articles containing exactly one image, that image is automatically used as `og:image` and in the JSON-LD. Otherwise, you can add `content/og-image.jpg` as a global fallback image; it is automatically copied to `dist/og-image.jpg` when the public site URL is known.

### JSON-LD and 404 page

The build automatically adds `BlogPosting` JSON-LD to article pages and generates `dist/404.html`. No additional configuration is required.
