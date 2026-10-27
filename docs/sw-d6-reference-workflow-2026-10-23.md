# Reprise de la sélection — consignes actualisées le 26 octobre 2026

**Arrêt demandé le 23 octobre à 09 h 30 ; ne reprendre que sur demande explicite.**
Le plan actif a été reconstruit sans LLM : 68 904 références conservées,
59 603 candidats et 250 105 blocs candidats, tous en attente de nouveaux votes.
Les 14 640 appels et 9 500 documents évalués de l'ancienne passe sont archivés.
L'estimation CPU du 26 octobre prévoit environ **65 000 nouveaux appels groupés**
(enveloppe de préparation 60 000–75 000), à confirmer après le nouveau contrôle
des 100 premiers appels. [Méthode et résultats](sw-d6-enrichment-v3.md).
Checkpoint et commande préparée dans `sw-kbase/pause-checkpoint.json` et
`sw-kbase/resume-selection.ps1` hors dépôt. Le pointeur actif conserve le dossier
de contrôle arrêté ; le script créera un nouveau dossier lors de la future reprise.

Historique du 23 octobre : terminer le run documentaire
`jev-v1-3136fcf8-b223-4ce2-8f42-f5df8b257284`, vérifier son bilan, puis préparer
et lancer [la sélection groupée des références et paragraphes](sw-d6-enrichment-v3.md).
Le seuil de redondance reste **strictement supérieur à 80 %**.

État effectif : passe documentaire terminée et auditée à 02 h 57 ; plan complet
de 68 904 références terminé ensuite ; sélection groupée lancée sur la 5090.
Les 100 premiers appels réels ont été contrôlés (753 scores, aucune anomalie)
puis le checkpoint a été libéré pour poursuivre le corpus. Voir le
[bilan et les limites de la v3](sw-d6-enrichment-v3.md).

Contrôleur : `tools/sw-kbase/run-reference-workflow.py`, processus persistant
masqué sous Windows. Aucune rédaction Strata, aucun embedding, aucun scraping.
Les originaux et overlays restent conservés ; la nouvelle sélection utilise
une SQLite runtime séparée et lit la base principale sans la modifier.

## Priorité de la 5090

- Le lanceur isolé reste épinglé sur l'UUID de la 5090 ; la 4090 est exclue.
- DXGI fournit le LUID réel de la carte Windows. Le contrôleur observe ses
  processus GPU et sa mémoire, avec vérification de l'UUID par `nvidia-smi` natif
  Windows (WSL seulement en repli si le binaire natif est absent).
- Une allocation étrangère d'au moins 64 Mio, une activité GPU étrangère d'au
  moins 1 %, ou une hausse inexpliquée de mémoire après chargement provoque la
  pause. Un pic d'activité avec moins de 64 Mio doit être confirmé sur deux
  observations consécutives ; une allocation réelle est prioritaire immédiatement.
  Le bureau Windows et les processus appartenant au serveur sont distingués.
- Le fichier `jev-fast/gpu5090.request`, dans le runtime, permet aussi de demander
  la carte **avant** une allocation concurrente. Sa présence suffit ; le contrôleur
  ne le supprime pas. `reference-workflow-…/stop` arrête également cette chaîne.
- La pause sauvegarde le cache, écrit `openjev.pause`, puis arrête uniquement
  notre serveur isolé pour libérer sa VRAM. Aucun processus tiers n'est terminé.
  Une impossibilité de surveiller correctement la carte provoque aussi une pause.
- Une pause de priorité termine le contrôleur. La reprise est explicite, dans
  un nouveau dossier de contrôle, avec les mêmes IDs et caches de travail.

## Prochaine exécution et fichiers de suivi

1. Utiliser la reprise `--selection-only` et le nouveau plan déjà préparé ;
   ne relancer ni le 27B documentaire ni le choix des références.
2. Vérifier l'état `complete` des anciens jobs documentaires et leurs snapshots.
   Les contextes différés restent signalés : « job terminé » ne signifie pas
   « toutes les incertitudes sémantiques résolues ».
3. Conserver le découpage `document-sections/2.2-coherent-blocks`. Aucun vote de
   l'ancien plan n'est transféré, car le contexte progressif des décisions change.
4. Recontrôler la disponibilité de la carte et utiliser un moteur joint validé
   pour les scores groupés des paragraphes, sans vote global, avec couverture progressive.
   L'ancien moteur ne sert pas de repli sériel pour cette phase.
5. Contrôler les 100 nouveaux appels avant de libérer la poursuite. Les anciens
   audits de débit et le marqueur de continuation restent dans l'ancien dossier.

Le dossier de contrôle contient `workflow.json`, `gpu-priority.json`, les logs,
`native-resume/progress.json`, puis `document-final-audit.json`. Une cession de
priorité est documentée dans `yield.json`.
Le dossier d'enrichissement contient `plan.json`, `enrichment.sqlite`, puis
`live-progress.json` et `inferences.jsonl` pendant les votes. Les gros résultats
restent hors dépôt, sur le disque disposant de place.
La base de sélection active est
`<runtime>\sw-kbase\enrichment-coherent-2.2-2026-10-23\enrichment.sqlite`.
Le pointeur runtime `sw-kbase/active-reference-workflow.json` donne le contrôleur
actif. Une reprise de la seule sélection utilise `--selection-only` et
`--native-run-output` vers les journaux de la passe documentaire terminée ;
l'audit reste obligatoire, sans rechargement du 27B.

Validations hors ligne : `sw_gpu_priority.py` et les tests existants de curation,
de sélection progressive, de reprise et de provenance. Le client natif reste
inchangé, pour préserver l'identité et la validité du cache précédent.

Le test réel de cession par marqueur a réussi à 01 h 38 : le worker s'est arrêté
après 325 nouveaux appels et 130 agrégats supplémentaires, le serveur isolé a été
arrêté et la 5090 est revenue à 58 Mio / 0 %. Le marqueur créé pour ce test a été
retiré après vérification ; la reprise utilise le même run et ses réponses en cache.

## Ancien comptage technique — remplacé par la v3

**Ce comptage décrivait le découpage de la v2, pas le nombre de requêtes de la
v3.** La v3 compare 59 603 paires documentaires, subdivisées en lots uniquement
si le contexte du moteur joint l'exige. Le vote global séparé est supprimé.

Comptage CPU en lecture seule à 02 h 25, sur les 24 502 agrégats multi-documents,
avec le même classement de référence et le même découpage que le plan :

- 59 603 documents candidats ;
- 1 255 999 paragraphes candidats, soit 1 281 514 fragments après partition ;
- un vote par fragment, plus les votes globaux (un par document s'il tient dans
  le contexte, plusieurs blocs sinon). Les subdivisions de budget peuvent ajouter
  des appels ; les chiffres ne sont pas des inférences déjà réalisées.

À 450 ms par appel, les seuls votes par fragment représentent environ 6,7 jours
de calcul continu. Ce débit provient de la passe documentaire ; une projection
de la nouvelle passe doit être remplacée par ses mesures réelles dès son lancement.
Une sélection complète le lendemain n'est donc pas garantie. Le choix CPU des
références et le découpage seront disponibles avant la fin des votes.
Rapport runtime : `paragraph-workload-estimate.json` dans le dossier de contrôle.
