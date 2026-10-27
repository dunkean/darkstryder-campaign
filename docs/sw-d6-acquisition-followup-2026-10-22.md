# SW D6 — acquisitions complémentaires du 22 octobre 2026

**Acquisitions et import SQLite terminés, audit réussi.** 33 740 nouvelles entrées ajoutées : **128 507 documents**, **68 904 agrégats**, dont **24 502 avec plusieurs documents**. Le [tri documentaire Jev](sw-d6-native-curation-2026-10-22.md) a ensuite été lancé ; Strata, embeddings et seconde passe par paragraphes restent inactifs.

| Type | Source / liens | Contenu récupéré |
|---|---|---|
| Articles HTML | [RPGGamer D6](https://rpggamer.org/) | **9 344 articles**, tous les identifiants des 11 menus D6. PNJ, espèces, créatures, droïdes, vaisseaux, véhicules, lieux, équipement, armes, pouvoirs et suppléments. |
| HTML + JSON natif | [D6DB compendium](https://d6db.com/compendium) | **20 478 fiches**, 18 catégories. Statistiques structurées natives conservées, avec le texte et les crédits de source. |
| Textes sources HTML | [D6DB Library](https://d6db.com/library) · [DarkStryder](https://d6db.com/library/darkstryder-campaign-box-set) · [Outback](https://d6db.com/library/kathol-outback) · [Rift](https://d6db.com/library/kathol-rift) · [Endgame](https://d6db.com/library/endgame) | **153 ouvrages, 682 unités de lecture et 153 notices**, dont les quatre volumes DarkStryder. Sommaires et pagination précédent/suivant contrôlés. Pagination du site, sans assimilation aux pages des PDF originaux. |
| Articles et pages | [Seawolf Star Wars / DarkStryder](https://www.theswden.net/star-wars-darkstryder/) | **330 documents**. Catégories D6/D20 et appartenance aux campagnes héritées de la hiérarchie du site. Adaptations personnelles séparées des références générales. |
| Cartes HTML de base de données | [Seawolf Astrogation](https://theswden.net/bgci4/swatlas/atlas) | **1 377 planètes et 213 groupes**. Tous les noms publiés dans les critères de recherche couverts. Relations natives planète/groupe conservées. |
| PDF + texte natif par page | [Pièces jointes Seawolf](https://www.theswden.net/files/Addons_to_DS.pdf) | **34 fichiers, 755 pages**, dont les deux extensions DarkStryder. Extraction locale sans OCR ni modèle ; originaux conservés. |
| Cartes HTML de règles | [OpenD6.net SRD](https://opend6.net/srd) | **406 fiches et 2 pages d’index/licence**, huit catégories. Univers générique D6 distinct de Star Wars ; les pouvoirs génériques ne deviennent pas des pouvoirs de la Force. |

## Limites constatées

- **D6DB** : écart de **140** entre les totaux annoncés et les URL uniques exposées : droïdes 2, pouvoirs de la Force 127, magick 7, armes 4. Leur identité et leur disponibilité ne sont pas vérifiées ; aucune fiche inventée pour combler l’écart.
- **Seawolf** : `PrestigeClasses.for.Jedi.pdf` renvoie **404**. **13 pages** ont peu ou pas de texte natif ; les cartes et scans restent présents avec leur page et leur original, sans texte fabriqué.
- **Rancor Pit** : index joignable, acquisition des PDF arrêtée au `robots.txt` qui interdit tous les robots. L’exception explicite de l’utilisateur concernait D6DB.
- **OpenD6 Project / RPG Library OGC** : toujours distincts d’OpenD6.net, non récupérés ; ne pas confondre leurs couvertures.

Les compilations réutilisent parfois les mêmes sources : plusieurs occurrences ne prouvent pas autant de sources indépendantes. Les assertions importées restent non revues.

## Intégration

Effectué : empreintes HTTP et fragments exacts → import versionné → liens natifs → regroupement des noms et alias → liens de mentions → union des blocs textuels identiques → audit SQLite et conservation de l’existant.

- **94 767 documents antérieurs**, versions, annotations, historique et décisions acceptées conservés à l'identique ; aucune erreur d'intégrité, de clé étrangère ou de hash sur les **128 507 versions courantes**.
- **2 046 870 blocs textuels distincts** ; **274 001 répétitions de blocs** stockées comme occurrences avec provenance. Les variantes et contradictions restent séparées.
- **6 674 119 liens entre agrégats**, dont navigation explicite vers les **682 unités de livres**, **755 pages PDF** et **406 fiches SRD**. Validation sémantique des liens secondaire, non planifiée.

## Préparation du dédoublonnage documentaire

Passage CPU sans inférence, **307 s**, sur les **59 603 documents supplémentaires** des agrégats : **10 498 documents entièrement couverts** par des textes retenus, marqués `exclude_redundant` pour le futur merge. Originaux et provenance conservés. **23 052 variantes protégées** (profils, éditions, époques, continuités ou overlays) ; les autres apports numériques et textes incertains sont également conservés.

Premier passage prévu : **10 563 comparaisons d'apport utile**, dont **9 611 compatibles avec 2 048 tokens** et **952 différées**. À part : **5 097 questions de rattachement** pour les ambiguïtés de type/sujet, dont **1 758 différées**. Les sources différées restent conservées ou en attente de rattachement. Ce n'est pas un nombre final d'appels : la couverture retenue évolue et l'exclusion d'un document exige de couvrir toutes ses unités.

Le [nouveau contrat Jev](sw-d6-jev-disambiguation-v1.md) est raccordé au serveur natif et à des tables `jev_v1_*` séparées. **Zéro ancienne décision recyclée, zéro nouvelle décision sémantique.** Sélections préparatoires et questions prévues sont visibles depuis chaque agrégat. Le compteur historique de **67 869 questions** reste une archive de l'ancien système, pas la file du nouveau Jev.

[Naviguer la base](http://127.0.0.1:8765/review#browse) · [Requêtes SQL](http://127.0.0.1:8766/query) · [Inventaire complet](sw-d6-databases-inventory.md).

Les fichiers et reçus sont hors Git dans le runtime. Le tunnel temporaire a été arrêté ; disparition de son écoute locale vérifiée. Le tri Jev est suivi séparément dans le bilan lié ci-dessus.
