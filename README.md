# Static blog generator (FR/EN)

Ce dépôt contient uniquement le moteur réutilisable :

- `build.py`
- `templates/`
- `requirements.txt`

Le générateur produit aussi les fichiers SEO/RSS lorsque l'URL publique du site est connue :

- `dist/sitemap.xml`
- `dist/robots.txt`
- `dist/rss.xml` (FR)
- `dist/en/rss.xml` (EN)
- balises `canonical`, `hreflang` et découverte RSS dans les pages HTML

Le contenu, la configuration YAML, les assets du site et le workflow GitHub Actions restent dans le dépôt consommateur.

## Créer une dépôt consommateur

Exemple ici [https://github.com/sfonteneau/blog.git](https://github.com/sfonteneau/blog.git)


## SEO, sitemap et flux RSS

Pour générer des URLs absolues valides dans le sitemap et les flux RSS, indiquez l'URL publique du site dans le `config.yaml` du projet consommateur :

```yaml
site:
  title: "Mon Blog"
  tagline: "..."
  author: "..."
  url: "https://example.com"

# Optionnel : 20 articles par flux par défaut
rss:
  enabled: true
  items: 20

# Optionnel : activé par défaut
sitemap:
  enabled: true
```

`site.base_url` est aussi accepté pour compatibilité. À défaut, le générateur utilise la variable d'environnement `SITE_URL`, puis un éventuel fichier `CNAME` à la racine du projet. Sans URL publique, le build continue mais affiche un avertissement et n'écrit pas le sitemap/RSS afin d'éviter des fichiers SEO avec des URLs invalides.
