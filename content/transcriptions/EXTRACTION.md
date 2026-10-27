# Extraction structurée du corpus

Pipeline local d'extraction des transcriptions (`<source-id>/pages/*.md`) vers une base de
connaissances, avec un serveur LLM local compatible OpenAI (aucun appel cloud). Les sorties
sont des preuves extraites, non relues, et jamais une vérité éditoriale : elles ne remplacent
ni `content/entities.json` ni `content/campaign.json`.

Scripts : `extract_entities.py` (phases 1 à 3) et `phase4_merge.py` (phase 4).
La phase 5 reste à implémenter.

```
python extract_entities.py all --collections darkstryder,addons,sourcebooks,guides-rules
```

Chaque appel LLM est mis en cache dans `_extraction/` : relancer la même commande reprend
là où le run s'est arrêté. Journal : `_extraction/run.log`.

## Catégories

Chaque entité appartient à une seule catégorie. Les définitions et les frontières complètes
sont dans `CATEGORIES` (script), et sont montrées au modèle à chaque appel de phase 1.

| Catégorie | Contenu | Frontière principale |
|---|---|---|
| `character` | individus nommés (PNJ, PJ, figures historiques, droïdes ou créatures individuels) | l'espèce va dans `species` |
| `species` | espèces, races, types de créatures | jamais une organisation |
| `organization` | groupes structurés : gouvernements, unités, compagnies, gangs, équipages, cultes | pas une espèce entière |
| `location` | régions, systèmes, planètes, villes, bases, sites, stations visitées comme lieux | un vaisseau en tant que tel va dans `craft` |
| `craft` | vaisseaux, véhicules, classes et modèles, stations traitées comme vaisseaux | les droïdes n'y vont pas |
| `technology` | armes, armures, équipement, modèles de droïdes, artefacts, technologies | |
| `history` | faits passés de l'univers présentés comme réels | les récits et croyances vont dans `legend` |
| `legend` | mythes, légendes, prophéties, religions, rumeurs | |
| `adventure` | aventures, épisodes, scènes, rencontres (unités jouables) | les fils transverses vont dans `intrigue` |
| `intrigue` | complots, plans des antagonistes, secrets, indices, accroches, menaces | |
| `rules` | règles, compétences, pouvoirs de la Force, tables, mécaniques de campagne | les stats d'une entité restent avec elle |
| `setting` | informations générales de l'univers (situation galactique, économie, société, voyages) | |

## Phases 1 à 3 (implémentées, par document)

1. **Énumération** (sans thinking). Chunks de 10 pages, 2 pages de recouvrement, un appel par
   catégorie. Réponse en lignes `nom | alias | indice | pages profile | pages role` :
   `profile` = l'entrée propre de l'entité (fiche, stats, description), `role` = page où elle
   agit ou où l'on apprend quelque chose de nouveau ; les simples mentions sont ignorées.
   Les pages quasi vides des longs documents sont ignorées ; les documents de 3 pages ou moins
   sont regroupés dans des chunks partagés. → `phase1/<catégorie>/<source-id>.json`
2. **Dédoublonnage dans le document**. Noms identiques (avec ou sans titre) fusionnés par le
   code ; noms proches et alias communs départagés par le LLM ; nom canonique arbitré par le
   LLM (erreurs d'OCR corrigées). → `phase2/<catégorie>/<source-id>.json`
3. **Un document Markdown par entité et par livre**. Un seul appel (thinking) par entité :
   toutes les pages trouvées pour elle dans le livre (pages `profile` + la page suivante, et
   pages `role`) sont données ensemble au modèle, qui écrit un document complet et bien
   organisé selon la catégorie (structure suggérée, non imposée), avec les données chiffrées
   regroupées comme une fiche et les pages citées `(p. 49)`. Pas de découpage ni de fusion à
   l'intérieur d'un livre : la fusion se fait entre livres (phase 4). Seulement si les pages
   dépassent `--max-doc-chars` (~80k tokens), des pages de scène sont écartées et listées dans
   le document. L'en-tête YAML (id, alias, pages, source, modèle, `reviewed: false`) est écrit
   par le code.
   → `documents/<source-id>/<catégorie>/<nom>--<id8>.md` et `documents/<source-id>/index.json`

## Phase 4 — classification puis fusion entre documents

La phase 4 sépare la présélection CPU (`index`, `candidates`), le classement des paires par
Simple-JEV/Qwen3.8-27B (`classify`), les groupes ancrés sur une fiche réelle (`groups`) et la
réécriture par le LLM actuel (`aggregate`). Aucun lancement implicite de modèle ni commande
qui enchaîne toutes les étapes. Les paires ambiguës sont mises en révision ; une ressemblance
transitive ne suffit pas à fusionner deux sujets.

Chaque paragraphe agrégé cite une ou plusieurs sources documentaires originales, avec format,
page/unité, extrait exact et SHA-256 conservés dans un fichier de provenance associé. Les
originaux et fiches de phase 3 restent conservés ; une fiche agrégée éditée localement ne peut
pas être écrasée. Les nouvelles sorties vont dans `_extraction/corpus-v2/`.

Configuration, installation différée du serveur, commandes et reprise : [PHASE4.md](PHASE4.md).

## Phase 5 — conversion en JSON (à faire)

Objectif : alimenter la base structurée (format des fiches de `build_kbase.py` /
`packages/kbase`), avec des identifiants stables.

- Entrée : les documents fusionnés de la phase 4.
- Un schéma par catégorie (par exemple : personnage = identité, description, relations,
  secrets, bloc de stats D6 ; vaisseau = fiche technique ; règle = texte + tables).
- Conversion par le LLM, document par document, puis validation par le code (types, dés D6,
  références de pages existantes) ; un document en échec est signalé, jamais complété au hasard.
- Chaque assertion conserve sa source (id du livre, page, extrait exact), comme l'exige la KBase.
- Les champs inconnus restent explicitement vides ; aucune coordonnée ni relation n'est inventée.
