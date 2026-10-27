# SW D6 — acquisition et merge algorithmique du 22 octobre 2026

État historique avant les acquisitions par tunnel. Voir le [complément du même jour](sw-d6-acquisition-followup-2026-10-22.md) pour RPGGamer, D6DB, Seawolf et OpenD6.net.

Base active : SQLite SW unifiée, séparée des JSON de campagne et de l’ancienne KBase. OpenJev et Strata restent arrêtés.

**94 767 documents → 53 088 clés**, dont **16 989 regroupements de plusieurs documents**. 4 548 229 liens de navigation. **69 171 propositions restent à examiner**, sans exécution de modèle. Elles correspondent à **30 789 combinaisons distinctes** au niveau des agrégats (paires d’identité ou classements), pas à un nombre prédéterminé d’appels OpenJev par lots.

## Acquisitions ajoutées

| Source | Entrées importées | Couverture |
|---|---:|---|
| SWRPGGM | 7 621 | Tous les 6 926 posts publics et 695 pages, contrôlés contre les totaux API. |
| Episode D6 | 10 467 | Tous les articles, catégories et modèles du miroir actuel. Même lignée que D6 Holocron 2023, sans indépendance de source supposée. |
| Wookieepedia | 1 906 | Livres/hubs DarkStryder, leurs liens directs et catégories ciblées Kathol/équipage ; pas toute l’encyclopédie. |
| Anakinworld | 93 | Hubs DarkStryder/Kathol/FarStar et références liées ; pas toute l’encyclopédie. |
| HoloNet FR | 9 | Index de catégories seulement ; aucun article DarkStryder découvert dans ces index. Métadonnées, pas un corpus encyclopédique complet. |
| Ancien dump Holocron | 130 | Modèles wiki précédemment exclus, maintenant conservés comme métadonnées. |

Total : **20 226 entrées ajoutées**. Les 74 541 documents antérieurs étaient tous importés : audit par clé et hash, zéro absence. Les 20 096 nouveaux documents web sont également vérifiés un à un. **570 réponses HTTP / 151 112 056 octets**, hashes contrôlés, originaux et reçus conservés hors Git.

## Ce qui est effectivement fusionné

Les noms équivalents partagent une clé. Les alias restent consultables et créent des liens, mais une classe ou une affiliation ne peut plus produire une chaîne arbitraire d’identité. FarStar, Nouvelle République, Alliance Rebelle et Lance of Endor sont de nouveau distincts. Les anciennes décisions restent dans l’historique et leurs désaccords sont signalés.

L’union textuelle conserve **823 753 blocs distincts**, avec 973 997 occurrences sources et leurs versions/positions. **150 244 répétitions identiques** sont stockées une seule fois sous leur clé. Des statistiques ou formulations différentes restent séparées : aucune moyenne, aucune résolution sémantique, aucune synthèse rédigée.

| Partie de la base | Documents |
|---|---:|
| Index et métadonnées | 569 |
| Informations générales | 83 703 |
| Scénarios et intrigues | 4 378 |
| Unités des textes sources | 6 117 |

## Manques explicites

**RPGGamer, Seawolf DarkStryder et Astrogation, Rancor Pit, D6DB** : hôtes bloqués par le filtre réseau local Fortinet ; aucun contournement tenté, aucun faux document de blocage importé. Les PDF Rancor Pit et Seawolf restent donc à acquérir. **OpenD6 Project** : échec de connexion TLS ; aucune acquisition ajoutée. Les anciens liens morts et catalogues bibliographiques de l’inventaire ne sont pas des corpus complets récupérés.

Ce bilan ne garantit pas la présence intégrale de chaque manuel WEG, ni un miroir intégral de toutes les encyclopédies. Les images des wikis ne sont pas acquises.

## Accès et validation

[Naviguer la base](http://127.0.0.1:8765/review#browse) : filtres par partie/type/source, doublons seulement, volumes par clé, texte agrégé et passages sources. Le panneau « Sources et couverture » expose les imports et les manques.

API de travail : `/aggregates`, `/aggregates/<id>`, `/aggregates/<id>/text`, `/aggregate-links/evidence`, `/search`, `/records/<id>`. `aggregate_id` est la clé de travail ; `entity_id` conserve l’historique. Les exports JSONL incluent les partitions, blocs textuels et toutes leurs provenances. Aucun embedding lancé.

Validation : 35 tests du projet réussis ; régressions ciblées sur les alias abusifs, partitions, variantes de statistiques et reprise d’import partiel. Navigation réelle testée : sections → doublons → FarStar → texte agrégé → lien → passage source, sans erreur. SQLite `quick_check` : OK. Tous les anciens records/versions, décisions acceptées et ajouts éditoriaux sont préservés. Sauvegarde SQLite avant modification dans le runtime ; reçus et `verification.json` conservés à côté.
