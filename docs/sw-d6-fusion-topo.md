# SW D6 : transcription et fusion pour une IA

> Note du 22 octobre 2026 : ce topo décrit la proposition initiale. Les imports et regroupements algorithmiques ont depuis été exécutés. Voir le [bilan actuel](sw-d6-acquisition-merge-2026-10-22.md) et le [pipeline implémenté](sw-d6-unified-pipeline.md).
> Note initiale de recherche. La [chaîne unifiée implémentée](sw-d6-unified-pipeline.md)
> remplace la proposition de vérification systématique par l’indexation immédiate,
> le text mining, OpenJev puis le Qwen + Strata existant.

21 octobre 2026 — note de synthèse, moins de deux pages de texte.

**Une grande partie de la ressaisie des statistiques devient évitable. Tes transcriptions restent utiles pour les scénarios, le contexte et les preuves.** Je recommande de réorienter l’effort vers l’import et la vérification ciblée, puis vers les contenus narratifs manquants. Aucun pourcentage sérieux de travail « remplacé » ne peut encore être donné.

## Ce qui est déjà disponible

L’[audit détaillé](../catalog/sw-d6-transcription-coverage.json) inventorie les **378 sources locales** et les rapprochements détectables avec les tables externes. L’index marque **375 documents complets**, tous présents en Markdown ; trois sont ignorés. Cela inclut **57 des 60 PDF catalogués**. « Complet » désigne ici l’état de conversion, sans garantir la qualité de chaque page.

| Corpus local | État constaté | Conséquence pour la transcription |
|---|---|---|
| DarkStryder | 4 PDF, 512 pages, tous publiés | Conserver intrigues, scènes, conditions, secrets et références exactes. Les profils externes accélèrent leur mise en base. |
| Autres sourcebooks | 12 PDF, 1 102 pages, tous publiés | Réutiliser le texte pour aventures, lieux et relations ; importer les statistiques déjà structurées. |
| Guides et règles | 39 documents, dont 36 PDF ; 3 ignorés | Réduire la ressaisie des listes et tables. Vérifier éditions, exceptions et règles complètes. |
| Compléments | 323 documents, dont 294 HTML, tous publiés | Garder les adaptations personnelles identifiables et séparées du matériel publié. |

**Découverte supplémentaire : Statbase était déjà dans ce corpus.** Sa conversion directe des 11 feuilles contient **6 110 lignes identifiées** : 677 espèces, 766 créatures, 727 droïdes, 1 411 équipements, 1 214 vaisseaux, 567 véhicules et 748 armes. S’y ajoutent 2 210 lignes d’armements de vaisseaux. Ces nombres précèdent tout dédoublonnage ; ce ne sont pas autant de profils supplémentaires uniques. Le fichier Excel original n’est pas accessible dans ce checkout.

Les téléchargements apportent aussi 2 329 fiches de PNJ, des archétypes et le dump historique de D6 Holocron. **Les recouvrements sont importants.** L’audit signale **61 références candidates discordantes** entre code produit et titre, par exemple « Kathol Outback » associé au code du coffret initial. La présence d’une citation ne vaut pas vérification du livre. L’audit recherche des références par codes/titres DarkStryder ; il ne mesure pas la couverture complète des ouvrages.

## La base à construire

**Faire évoluer la KBase SQLite existante.** Elle possède déjà entités, assertions, relations, citations et corrections éditoriales. Garder trois couches : les fichiers originaux immuables ; les assertions importées avec leur provenance ; les fiches consolidées consultables et modifiables. Les réimports ajoutent des versions sans écraser les corrections personnelles.

Une entité reçoit un ID stable et des alias français/anglais. Ses profils restent distingués par édition, époque et origine : WEG, REUP, conversion de fan ou génération. Deux fiches de Luke à des époques différentes ne doivent pas devenir un profil moyen. Une valeur contradictoire reste visible avec sa source ; la fiche indique explicitement la variante retenue.

**Adapter les preuves aux formats.** Le schéma actuel impose une page entière numérique. Ajouter des localisateurs typés : page PDF et page imprimée distinctes, feuille/ligne/cellule, fichier/identifiant/chemin JSON, page wiki/révision. Une référence web à un livre reste une référence déclarée jusqu’à son rapprochement avec un extrait local vérifié et son hash. Ne jamais fabriquer une page pour satisfaire le schéma.

## Comment l’IA l’utilisera

Combiner recherche textuelle et filtres structurés : édition, époque, catégorie, source, statut de relecture et accès MJ/joueurs. SQLite fournit la [recherche plein texte FTS5](https://www.sqlite.org/fts5.html). Une recherche sémantique pourra compléter les résultats après mesure des besoins.

Exposer quatre opérations simples : **chercher**, **lire une fiche et ses variantes**, **suivre ses relations**, **consulter les preuves**. L’IA reçoit uniquement les fiches et passages utiles, avec citations et incertitudes. Exporter également du JSONL versionné pour les traitements hors application. Les scènes générées citent leurs inspirations ; leurs nouveaux faits restent des propositions, séparées du scénario publié et de l’état réel de partie.

## Ordre de réalisation recommandé

1. **Importer en zone de préparation** Statbase, Toolkit et Essential Companion ; contrôler champs, doublons et traçabilité, sans extraction LLM des tables.
2. **Faire un pilote DarkStryder** : équipage, lieux, profils et quelques scènes, reliés aux transcriptions. Normaliser aussi règles et prérequis par fragments bornés.
3. **Tester des demandes de MJ** : retrouver une règle avec son exception, comparer deux profils, préparer une escale sans transformer une possibilité en événement acquis.
4. **Élargir après revue** aux autres livres et au wiki, puis réviser les priorités OCR selon les lacunes constatées. Le pilote de migration devra vérifier réimport, conflits et préservation des corrections.

La fusion n’a pas encore été exécutée ; les transcriptions et les données de campagne ont été préservées.
