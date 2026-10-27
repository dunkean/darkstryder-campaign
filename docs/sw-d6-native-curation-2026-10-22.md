# Tri documentaire Jev — 22 octobre 2026

**Passe terminée et auditée le 23 octobre à 02 h 57 sur la 5090.**
Run : `jev-v1-3136fcf8-b223-4ce2-8f42-f5df8b257284`.
Périmètre : 24 502 agrégats comportant plusieurs documents. Le
[contrôleur de reprise](sw-d6-reference-workflow-2026-10-23.md) a vérifié les
snapshots et les sélections avant la sélection groupée des références/paragraphes.
Les liens, Strata et embeddings ne sont pas exécutés par ce contrôleur.

## Bilan final de cette passe

24 502 jobs complets, aucun job en erreur, aucun snapshot modifié et aucune
anomalie de seuil. 10 902 sources écartées du merge : 10 060 copies normalisées,
29 couvertures exactes par unités et 813 couvertures par l'ensemble des unités.
Les originaux restent tous présents.

5 155 sources sont en attente d'identité : 5 144 non résolues et 11 classées comme
une entité différente. 3 311 requêtes dépassent le budget natif et 63 observations
signalent un contexte incomplet/trop grand. Une passe terminée ne signifie donc
pas que toutes les ambiguïtés ont un verdict sémantique.

Audit final : `sw-kbase/reference-workflow-grouped-20261023-024027/document-final-audit.json`
hors dépôt. La [sélection v3](sw-d6-enrichment-v3.md) reprend toutes les sources,
sans recycler les anciennes sélections comme scores de paragraphes.

## Checkpoint historique de pause — 22 octobre

9 824 appels natifs exécutés dans cette session ; 3 903 agrégats terminés sur
24 502. Sur les documents candidats de ces agrégats : 1 927 exclus du futur
merge (1 284 copies normalisées, 18 couvertures exactes par unités, 625 couvertures
sémantiques), 18 identités en attente ; les autres restent conservés.
581 demandes dépassant le budget de tokens sont différées, ainsi que 18 contextes
refusés avant appel. Ce bilan est partiel, pas une validation complète du corpus.

Moyenne native mesurée sur les 9 824 appels : 463 ms ; médiane 447 ms ; p95 639 ms.
Le cache contient aussi les réponses identiques du pilote précédent.
Les originaux (128 507 entrées) sont conservés. Audit de pause :
`sw-kbase/native-live-80-2026-10-22/pause-audit.json`.

Le worker est arrêté, `openjev.pause` est présent et le serveur isolé Ollaya a
été arrêté proprement pour décharger le modèle. Mesure après arrêt : 5090 à
84 Mio, utilisation 0 %. Aucun autre service GPU n'a été arrêté.
Un job interrompu porte le marqueur `Native curation paused` ; ses réponses
déjà acquises restent en cache pour une reprise dans le même ordre.

Pour la reprise autorisée ultérieurement : relancer le serveur isolé avec
`jev-fast/start-server.ps1`, puis le worker sur **ce même run**, avec un **nouveau
dossier de sortie** et `--sample 0`. Le worker contrôlera l'identité du modèle,
retirera la pause pour cette exécution et reprendra les jobs restants par cache.
Ne pas relancer le plan, le scraping, Strata ni la passe par paragraphes.

## Mesure des 100 premiers appels réels

| Mesure | Résultat |
|---|---:|
| Temps du premier échantillon, persistance comprise | 45,5 s |
| Moyenne par appel natif | 434 ms |
| Médiane | 435 ms |
| p95 | 578 ms |
| Erreurs | 0 |
| Tokens d'entrée | 135 608 |

Serveur chaud, Jebadiah 27B v2.1 Q8_0 sur la 5090. Le contrôle initial des
poids est compté séparément. Le pilote porte sur des passages de documents
réels ; ce n'est pas une mesure de précision sur un corpus annoté.

Le pilote à 98 % a donné 58 choix « redondant », 41 « ajout » et 1 « indéterminé »,
mais aucune exclusion sémantique à ce seuil. À la demande de l'utilisateur,
la politique active est **P(redondance) strictement supérieure à 80 %**.
L'ancien pilote reste archivé ; ses sélections ne sont pas recopiées.
Seules les probabilités d'une demande strictement identique peuvent être rejouées.

Chaque unité d'un document doit être couverte avant de l'écarter du futur merge.
Un apport, une contradiction ou un résultat incertain conserve le document.
Profils distincts, éditions, époques, continuités et overlays restent protégés.
Les originaux ne sont jamais supprimés et les identifiants ne sont pas fusionnés
par ce worker. Les contextes trop longs sont différés sans troncature.

## Suivi et reprise

Worker : `tools/sw-kbase/run-native-monitored.py`. Les décisions, versions et
sélections sont persistées dans les tables `jev_v1_*`, sans réutiliser les
anciennes décisions du worker historique. Les aperçus et scores par passage
sont disponibles sous chaque agrégat dans [la navigation](http://127.0.0.1:8765/review#browse).

Fichiers runtime hors Git : `sw-kbase/native-live-2026-10-22/sample.json`
(pilote), `sw-kbase/native-live-80-2026-10-22/progress.json`,
`requests.jsonl`, `running-summary.json`, puis `final.json` en fin de traitement.
Le worker restaure la pause à sa sortie. Une interruption reprend les demandes
exactes par cache et reconstruit la couverture dans le même ordre.

## Passe suivante préparée, inactive

[Chaîne d'enrichissement préparée](sw-d6-enrichment-v2.md).
Elle reprend toutes les entrées, choisit une référence par heuristique, numérote
les paragraphes et conserve versions, offsets, source et empreintes. Elle demande
les votes global et par paragraphe, avec couverture enrichie après chaque ajout.
`score --execute` et `merge --execute` sont deux actions distinctes ; aucune
exécution sur le corpus réel n'a été effectuée.
