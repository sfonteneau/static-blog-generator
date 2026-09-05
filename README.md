# Static blog generator (FR/EN)

Ce dépôt contient uniquement le moteur réutilisable :

- `build.py`
- `templates/`
- `requirements.txt`

Le contenu, la configuration YAML, les assets du site et le workflow GitHub Actions restent dans le dépôt consommateur.

## Utilisation comme submodule

Depuis le dépôt du blog :

```bash
git submodule add <URL_DU_DEPOT_GENERATEUR> generator
git commit -m "Add static blog generator submodule"
```

Puis :

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r generator/requirements.txt
python generator/build.py build --config config.yaml
```

Le répertoire du projet est déduit de l'emplacement de `config.yaml` et non de l'emplacement de `build.py`.

## Chemins d'assets

Le champ `style` accepte les trois formes suivantes :

```yaml
# Recommandé : relatif au fichier config.yaml
style: "assets/style.css"

# Compatibilité historique : cherche dans <projet>/assets/
style: "style.css"

# Chemin absolu
style: "/srv/www/mon-blog/assets/style.css"
```

`~` et les variables d'environnement sont également développés, par exemple :

```yaml
style: "${BLOG_ASSETS}/style.css"
```

Le champ optionnel `theme` suit les mêmes règles et vaut `theme.js` par défaut.
