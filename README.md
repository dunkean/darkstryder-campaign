# DarkStryder — base de campagne

Site local personnel en français : équipage, organisation, FarStar, cartes, PNJ,
factions, scénario, événements, planètes, sources OCR et fiches éditables.

## Démarrer

Node 24 est requis. Dans WSL, l'installation portable existante peut être activée
dans le shell avec `source tools/environment.sh` (ou utiliser `nvm use`).

```bash
source tools/environment.sh
npm ci
npm start
```

Ouvrir http://localhost:4321. Le serveur écrit réellement les modifications dans
`content/campaign.json` et `content/entities.json`. Chaque sauvegarde conserve la
version précédente dans `darkstryder_runtime/backups`. Ctrl+K ouvre la recherche.

Les 120 membres d'équipage et leurs portraits proviennent de la console existante.
La fiche d'un personnage propose une édition normale et une édition JSON avancée
pour ses statistiques, relations et affectations. L'éditeur des données FarStar
permet aussi de modifier salles, droïdes, postes et équipes.

Les autres catégories sont prêtes pour produire du contenu sourcé. Elles ne sont
pas remplies automatiquement avec des interprétations des livres. « Sources »
permet de rechercher le texte extrait, consulter une page, vérifier le PDF au besoin
et créer une fiche avec cette référence déjà renseignée.

## Sources et fichiers locaux

- Originaux : miroir Drive sur Windows, lu depuis `/mnt/d/Drive_google/DarkStryder` sous WSL, sans modification.
- Code et données éditées : ce dépôt Git privé.
- OCR, scans, images, backups et modèles : runtime Linux configuré, hors Git.
- `config.local.json` contient les chemins locaux et n'est pas versionné.

Les chemins Linux doivent être renseignés dans `config.local.json` : `sourceRoot`,
`runtimeRoot`, `pythonExecutable`, puis les réglages du moteur OCR. Le lanceur
Python utilise cet environnement existant ; il ne modifie pas les dépendances de
Biosense. `DARKSTRYDER_PYTHON` peut remplacer le chemin de Python.
L'ancien `tools/setup.ps1` reste disponible pour Windows ; ne pas relancer
`bootstrap.py` sur un site déjà édité, car il régénère les modules historiques.
Drive reste la source des médias : les images ne sont pas répliquées massivement.
Les quelques portraits embarqués dans la console sont extraits dans le runtime.

Une fois l'OCR terminé, `npm run transcripts:publish` copie les Markdown des livres
et des pages dans `content/transcriptions/<source-id>/`, pour construire la base de
connaissances depuis le projet. Les images restent dans le runtime, accessibles
par des liens locaux non versionnés. La publication conserve les références et
les rapports de qualité, et refuse d'écraser une transcription modifiée localement.

## OCR GPU

```bash
npm run inventory
npm run ocr
# Lancement détaché : continue après fermeture du terminal
npm run ocr:background
# Seulement les quatre livres DarkStryder :
npm run ocr -- --collection darkstryder
```

Sous WSL, le moteur choisi est Docling avec RapidOCR, analyse de mise en page et
TableFormer en mode précis, sur la RTX 3090. L'environnement OCR de Biosense et
ses modèles déjà installés sont réutilisés. Le traitement est limité à une page
à la fois, avec sauvegarde de la progression après chaque page. Aucun service OCR
externe ni appel LLM payant.

Le code du backend Chandra OCR 2 reste disponible pour une comparaison ponctuelle
avec un environnement Chandra distinct. Les extractions de moteurs différents restent séparées ; les deux
pages Chandra précédemment validées sont conservées hors Git.

L'inventaire cible `Material/DarkStryder SourceBook`, `Material/SourceBooks` et les
PDF locaux de `sourcebooks`. Les doublons sont identifiés par SHA-256 : 16 sources
uniques, 1 614 pages. Les autres PDF Drive attendent une extension du périmètre.

Une sortie par source :

```text
runtime/extracted/<source-id>/
  book.md                 # Livre assemblé
  pages/0001.md           # Markdown avec provenance
  pages/0001.jpg          # Scan pour vérification ponctuelle
  images/                 # Illustrations extraites
  structure/0001.json     # Mise en page et blocs
  manifest.json           # Couverture, métriques, anomalies
  search.json             # Recherche locale
```

La conversion reprend les pages terminées. La rubrique « Sources » affiche la
progression globale et par livre, actualisée toutes les cinq secondes.
Les indicateurs signalent le texte
insuffisant, les sorties possiblement tronquées, les répétitions et les faibles scores
de reconnaissance ou de mise en page ; ils ne certifient
pas l'exactitude de chaque statistique. Lors de la rédaction, vérifier uniquement
les passages douteux dans les PDF originaux. Le numéro cité est celui de la page PDF.
Les avertissements de qualité apparaissent sur la page consultée dans « Sources ».
Relancer `npm run ocr` reprend les pages validées par leur manifeste ; un verrou
empêche deux conversions d'écrire simultanément dans le même runtime.
Le lancement détaché écrit son PID et le chemin du journal dans `runtime/ocr-job.json`.
Un superviseur renouvelle le processus OCR toutes les 96 pages sauvegardées afin
de libérer les buffers natifs et de contenir la mémoire. Les pages validées ne sont
pas retraitées. Un arrêt ou une erreur interrompt le travail ; il n'y a pas de
redémarrage automatique après une erreur.

## Plans, cartes et heure

Le FarStar dispose de six plans SVG interactifs : clique dans une zone pour consulter
les affectations de la salle et ses images. Les 130 zones ont été relevées sur les
plans sources ; 61 salles possèdent une association explicite et 13 restent à associer manuellement. Les correspondances
incertaines restent vides et peuvent être renseignées dans l'inspecteur.

« Calibrer les zones » permet de déplacer les sommets à la souris ou avec les flèches,
de saisir leurs coordonnées en pourcentage et de tracer de nouvelles zones. Les
ajustements deviennent persistants avec « Enregistrer la zone ». « Comparer avec
l'original » affiche le scan de référence séparément du plan vectoriel.

Depuis une salle, « Ajouter / modifier les images » importe PNG, JPEG, WebP ou GIF
(8 Mo maximum), ou associe une référence locale existante. Les fichiers restent dans
`runtime/media/rooms` ; leurs références, légendes et provenance sont sauvegardées
dans `campaign.ship.rooms[].media`. Les tracés sont dans `campaign.ship.deckPlans`. Les relevés
initiaux de `tools/plans/farstar-traces.mjs` ne doivent pas être réappliqués après une
calibration utilisateur.

Pour la carte stellaire, activer « Placer un repère », cliquer puis associer une fiche.
Les coordonnées sont persistées en pourcentage. Aucune position n'est inventée.

L'heure sélectionne un quart selon les débuts configurables (00h / 12h initialement).
Les présences sont dérivées des affectations ; les postes partagés et extras sont
identifiés comme tels. L'alerte désactive les extras. « Instantané » conserve la
répartition aléatoire de la première console. Ce n'est pas une simulation de mouvement.

## Validation et intégration

```bash
npm run check
npm run export
```

CI minimale : build et tests, sans déploiement. L'export `knowledge-v1.json` est
produit dans `runtime/exports` pour le futur projet de mastering, qui conservera
l'état des sessions séparément. Ce site sert toutes les données MJ sur loopback ;
la visibilité des fiches est une métadonnée, pas un contrôle d'accès joueurs.

Voir [docs/architecture.md](docs/architecture.md) et [AGENTS.md](AGENTS.md).
