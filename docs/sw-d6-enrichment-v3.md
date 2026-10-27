# Sélection groupée des blocs — état au 26 octobre 2026

**En pause ; aucune autorisation de relancer le modèle.** Le découpage a été
reconstruit par algorithme sous `document-sections/2.2-coherent-blocks` :
128 507 documents, 68 904 références conservées, 767 349 blocs au total,
dont **250 105 blocs dans 59 603 documents candidats**. Les 14 640 anciens appels
et leurs décisions restent archivés dans l'ancien plan ; ils ne sont pas repris
comme votes sur les nouveaux blocs ni sur leur couverture progressive.
`sw-kbase/pause-checkpoint.json` hors dépôt rassemble les chemins et compteurs ;
`sw-kbase/resume-selection.ps1` est prêt mais doit être exécuté seulement après une
demande explicite de reprise. Le script pointe désormais sur
`enrichment-coherent-2.2-2026-10-23`, validé en mode `-ValidateOnly` le 26 octobre.
Il reprend ce nouveau plan sans refaire les références, le scraping ou le découpage.
Strata et les embeddings restent désactivés. Voir le [découpage et sa traçabilité](sw-d6-coherent-chunks.md).

## Prochaines étapes à la reprise autorisée

1. Vérifier les snapshots et l'audit documentaire déjà terminé ; utiliser
   `--selection-only`, sans relancer les questions documentaires ni le 27B.
2. Vérifier que la 5090 est disponible et charger uniquement le moteur joint prévu.
   La 4090 reste exclue ; une demande concurrente de la 5090 impose une pause.
3. Présenter référence, couverture retenue et plusieurs blocs candidats dans chaque
   appel ; retourner tous les scores ensemble, **sans vote global**. Ajouter les
   compléments utiles/incertains avant le lot ou document suivant.
4. Contrôler les **100 nouveaux appels** : qualité des blocs/questions, probabilités,
   fidélité du contexte, nombre de lots, latence et VRAM. Le nouveau dossier ne
   contient pas le marqueur `continue-after-100` de l'ancienne passe.
5. Recalculer le temps restant avec ce débit réel, puis poursuivre après validation
   de l'échantillon. Exporter ensuite référence + compléments avec provenance.
   La rédaction/fusion Strata-Qwen sera une action séparée, ultérieure.

Cette politique remplace la v2, avant son exécution sur le corpus : **aucun vote
global**, tous les scores des paragraphes d'un document dans une réponse JSON.
La passe des ambiguïtés doit se terminer et réussir son audit avant la sélection.

## Périmètre et nombre de comparaisons

128 507 documents, 68 904 entités : une référence par entité, donc **59 603 paires
référence/document supplémentaire**, dans 24 502 entités multi-documents.
Les 44 402 singletons ne demandent aucune comparaison. Plusieurs lots peuvent
être nécessaires pour une paire dépassant le contexte total du moteur ; leur
nombre réel doit être mesuré avec son tokenizer, pas supposé égal au nombre
de paragraphes.

### Estimation CPU du nouveau plan — 26 octobre

Échantillon aléatoire reproductible de **200 entités multi-documents**, soit
**432 candidats et 1 727 blocs**, avec le tokenizer exact, les IDs réels et la
limite globale de 4 096 tokens du moteur joint. Aucun serveur, GPU ou appel LLM.

| Couverture simulée | Lots échantillonnés | Projection corpus | Bootstrap 95 %, échantillonnage seulement |
|---|---:|---:|---:|
| Aucun complément nouveau retenu | 472 | 65 122 | 62 189–68 824 |
| Tous les compléments nouveaux retenus | 471 | 64 984 | 62 164–68 896 |

**Prévoir environ 65 000 appels, avec une enveloppe de préparation de 60 000 à
75 000.** 413 des 432 documents, soit 95,6 %, tiennent en un seul appel ; la
moyenne est de 1,09 appel/document. Les 250 105 scores de blocs ne représentent
donc pas 250 105 requêtes. Ces deux hypothèses ne sont pas des bornes garanties :
les vrais votes changent la couverture et les fenêtres récupérées. Confirmer le
débit et actualiser le temps restant sur les 100 premiers appels autorisés.

Rapport hors dépôt : `sw-kbase/joint-estimate-coherent-2026-10-26.json`.
Reproduction CPU uniquement : `tools/sw-kbase/estimate-joint-calls.py` avec
`--database`, `--selection`, `--runtime`, `--report` ; graine par défaut 20261026.

**Ancien échantillon, avant correction du découpage — historique uniquement :**
200 entités, 481 candidats, 5 642
passages ; 805 lots, dont 362 documents en un lot. Projection indicative :
environ 100 000 lots. La simulation conserve tous les passages déjà évalués et
emploie des IDs de preuve raccourcis ; elle ne constitue pas un décompte exact
ni une mesure GPU. Rapport : `jev-joint/cpu-batch-estimate.json` hors dépôt.
Cette projection n'est pas celle du nouveau plan ; la simulation CPU du 26 octobre
utilise ses blocs et IDs réels, avec deux hypothèses de couverture retenue.

Le précédent chiffre de 1 281 514 comptait des fragments techniques : champs
JSON, titres, lignes de listes/tableaux et morceaux de 1 000 caractères. Il ne
représente pas le nombre de requêtes de cette nouvelle politique. L'ancienne
projection de 6,7 jours n'est pas une estimation du nouveau flux.

## Entrée, sortie et sélection

- Référence choisie par les heuristiques existantes, avec classement explicable.
- Passages cohérents et numérotés : titre rattaché au texte suivant, liste/tableau
  contigus regroupés, objet de statistiques JSON conservé ensemble lorsqu'il est
  court. Les spans techniques restent dans la provenance ; ils ne sont pas des jobs.
- Une entrée contient la référence, les compléments précédemment retenus et tous
  les passages numérotés du candidat. Une sortie contient leurs probabilités
  `yes` (apport utile), `no` (redondant), `unknown`, en pourcentages.
- Écarter uniquement si `no > 80`. À 80 exactement ou en cas d'incertitude, conserver.
  Il s'agit de probabilités de décision, pas du pourcentage mesuré de mots nouveaux.
- Ajouter les passages conservés à la couverture avant le lot ou document suivant.
  Dans un même lot, demander de préférer le premier passage aux répétitions suivantes.
- Dépasser le contexte provoque un découpage en lots puis, seulement si nécessaire,
  une partition des longs passages. Les fenêtres de référence sont explicites et
  sauvegardées ; aucune troncature silencieuse du candidat n'est acceptée.

Le résultat de sélection reste dans `enrichment.sqlite` hors dépôt ; originaux,
versions, sources, scopes et overlays sont conservés. La fusion Strata et les
embeddings restent des étapes séparées, non lancées ici.

Plan actuel terminé : 68 904 références, 59 603 candidats, 250 105 blocs
candidats (environ 4,2 par document). Runtime :
`<runtime>\sw-kbase\enrichment-coherent-2.2-2026-10-23`.
La vue SQL `selected_paragraphs` expose uniquement les références et les ajouts
utiles/incertains, avec statuts de progression, probabilités natives et provenance.
Les copies exactes dans un même scope et une même section sont écartées par preuve
de couverture, même si le modèle hésite ; le score brut n'est jamais remplacé par
une probabilité fabriquée. Les valeurs isolées sans contexte ne suffisent pas.

## Moteur et exécution

`grouped_paragraphs.py` exige un client attestant **une vraie inférence multi-scores**.
Un simple regroupement HTTP de forwards Jebadiah indépendants est refusé.
Le Jebadiah 27B de la passe des ambiguïtés reste inchangé. Le moteur joint Clef
est préparé dans un runtime distinct et doit être testé sur la 5090 avant lancement ;
aucune équivalence de qualité avec le 27B n'est présumée.

Le contrôleur attend `joint-runtime.json` après préparation et vérifications.
La nouvelle sélection s'arrête au checkpoint des 100 premiers appels pour contrôler
réponses, contexte, VRAM et débit avant poursuite. Le guard de priorité GPU reste actif.

**Historique de l'ancienne passe, avant reconstruction des blocs :**
le corpus avait démarré le 23 octobre vers 03 h 12. Checkpoint des
100 appels contrôlé puis libéré : 63 documents supplémentaires évalués,
753 scores de passages, aucune anomalie d'IDs, de troncature, de nombre de forwards
ou de seuil ; 4 preuves de copie exacte. Parmi ces passages : 258 écartés,
24 utiles et 471 incertains conservés. Le score incertain ne vaut pas validation.
Latence des appels : moyenne 1 569 ms, p50 1 344 ms, p95 3 140 ms ; moyenne
3 275 tokens, maximum 4 084. VRAM stable à 22 959 Mio après chauffe 4 096 tokens.
Ancienne projection provisoire : de l'ordre de deux jours continus, susceptible de changer
avec les lots suivants et les interruptions de priorité GPU.
Preuves runtime : `first-100-audit.json`, `first-100.json`, `inferences.jsonl` et
`live-progress.json`. Le checkpoint a été libéré par `continue-after-100` après
vérification ; ce traitement est ensuite arrêté. Ces mesures ne valident pas
les nouveaux blocs : le contrôle des 100 appels doit être refait à leur reprise.

Tests : `tests/sw_grouped_paragraphs.py` couvre regroupement, absence de vote global,
couverture progressive, lots sans perte, reprise exacte et refus des backends sériels.
La v2 reste disponible pour comparaison ; ses décisions ne sont pas réutilisées.

[Smoke réel Clef](sw-d6-joint-jev-smoke-2026-10-23.md) : 8 scores en une passe,
p50 583 ms à 1 779 tokens et 1 384 ms à 4 096 tokens ; pic 22 855 Mio.
Le contexte maximal est chauffé avant de fixer la baseline du guard GPU pour ne
pas confondre la croissance normale de l'arène CUDA avec une allocation concurrente.

Le moteur choisi après le benchmark est `perplexity-ai/pplx-decider-flash-v1.2`,
révision `9ff7cf9838e59df461c276b7699d56b244d2af58`, en BF16 : chemin officiel
optimisé, CUDA Graphs et bucket de 4 096 tokens, sans FP8. Un processus WSL
persistant échange par pipes JSON avec le contrôleur Windows ; la base de
sélection et ses checkpoints restent gérés sous Windows. Le budget est vérifié
avec le tokenizer exact avant chaque lot ; aucun texte n'est tronqué.

`tools/sw-kbase/run-perplexity-selection.py --config <config-runtime.json>`
valide uniquement sur CPU. `--execute` demande un lancement explicite et refuse
de démarrer tant que `WAIT_FOR_USER_GO` existe. Le fichier de configuration
local est préparé par `prepare-perplexity-selection.py` à partir du checkpoint
et de l'environnement du benchmark ; il reste hors Git. La 5090 est identifiée
par UUID, et une demande de GPU concurrente déclenche une pause avec reprise
sur les requêtes déjà enregistrées. Une probabilité de redondance strictement
supérieure à 80 % écarte un bloc ; les cas incertains sont conservés. Les blocs
retenus enrichissent la couverture avant la source suivante.

`watch-selection.py --config <config-runtime.json> --every 5000` compte les
documents candidats entièrement terminés, et produit les paliers, le débit et
le temps restant estimé. Il ne confond pas les documents avec les appels ou
les références. Cette phase ne lance ni Strata, ni embeddings, ni fusion éditoriale.
