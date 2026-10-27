# Jev : désambiguïsation et sélection des sources — contrat v1

Décision demandée dans la conversation parallèle : Jev désambiguïse et filtre
les contributions au futur merge texte. Il ne produit pas les fiches fusionnées.

## Questions

| Question | Formulation | Score affiché |
|---|---|---|
| `entity_match` | Cette source décrit-elle bien l'entité de référence ? Deux noms ambigus désignent-ils le même sujet ? | P(oui), P(non), P(indéterminé) |
| `source_contribution` | Cette source ajoute-t-elle au moins une information utile absente de la référence **et de toutes les sources déjà retenues** ? | P(ajout), P(redondance), P(indéterminé) |
| `link_validity` | Dans ce passage précis, ce lien vers cette entrée est-il correct, avec ce prédicat et ce sens s'ils sont fournis ? | P(valide), P(invalide), P(indéterminé) |

Ce sont des pourcentages de choix du modèle. P(ajout)=80 % ne signifie pas
« 80 % du texte est nouveau ». Les seuils devront être évalués sur les cas locaux.

Prompts anglais complets : `PROMPTS` dans `packages/sw_kbase/disambiguation.py`.
Le protocole natif est `choice`, trois options `yes/no/unknown`, sans génération
de texte libre ni pourcentage déclaré par un assistant génératif.

Pas de passe Jev d'importance narrative, d'univers, ni de typage systématique.
Les types restent des indices issus du text mining et des métadonnées pour
résoudre les ambiguïtés d'identité et de liens.

## Boucle par entité

1. Garder une référence explicite et stable ; les choix éditoriaux personnels
   ne sont jamais remplacés. Fixer un ordre déterministe des autres sources.
2. Le text mining conserve les regroupements simples, alias évidents et liens
   lexicaux. Marquer les attaches réellement ambiguës `identity_ambiguous=true`.
   Sans cette marque, l'attache existante est utilisée sans question d'identité.
3. Écarter du futur merge les copies textuelles reconnues algorithmiquement,
   après vérification des protections de profil. Sinon poser la question d'apport.
   Pour une source ambiguë, les questions d'identité et d'apport partagent le
   même état dans une seule requête. Interpréter l'identité en premier.
4. Une identité rejetée ou indéterminée reste en attente de rattachement. Sa
   matière ne rejoint pas la couverture de référence et ne peut pas servir à
   déclarer d'autres sources redondantes.
5. Une source retenue rejoint immédiatement la couverture utilisée pour la
   suivante. Ainsi, B ajoute X à A ; C répète X : B reste, C peut être écartée.
   Ne jamais décider de supprimer mutuellement B et C en les comparant chacune
   à une union contenant l'autre.
6. Une source redondante reçoit `exclude_redundant`, avec les IDs couvrants et
   une empreinte des preuves. Les documents originaux, leurs liens, leurs versions
   et leur provenance restent disponibles. Le nombre de sources peut rester 25,
   même si seules 4 contribuent au merge texte.

Un fait, une statistique, une relation, une description exploitable, un détail
de scénario ou une contradiction constituent un apport. Une traduction,
reformulation, nouvelle URL ou nouvelle bibliographie seules ne suffisent pas.
Les profils d'édition, époque ou équipement différents restent séparément
disponibles ; aucune moyenne ni sélection automatique de statistiques.

Protections d'entrée `preserve_reasons` : `contradiction`, `distinct_profile`,
`new_edition`, `new_epoch`, `personal_overlay`. Un `profile` explicite nouveau
est aussi protégé, même si le texte est identique.

Politique active du worker documentaire, modifiée à la demande de l'utilisateur
après les 100 premiers appels du 22 octobre : identité acceptée/rejetée
à 95 %, **redondance strictement supérieure à 80 %**, lien validé/rejeté à 95 %.
Le pilote initial à 98 % est archivé séparément ; ses probabilités ne sont
réutilisées que pour une demande et un modèle exactement identiques, et ses
sélections ne sont pas recopiées dans le nouveau run. Une faible P(ajout) avec
une forte P(indéterminé) ne permet pas d'écarter une source. En cas de doute sur
l'apport, la matière est conservée avec ses scores.

## Liens

Les liens simples restent utilisables tels qu'issus du text mining, avec cette
provenance algorithmique. Exemple : une mention de Han Solo et d'un blaster lourd
ne nécessite pas une nouvelle demande Jev pour obtenir un lien de navigation.

Le planificateur marque seulement `needs_review=true` sur les liens à vérifier :
référent ambigu, homonymes, type incohérent, attribution d'espèce ou d'appartenance
à une organisation douteuse, ou relation structurante incertaine. Un lien marqué
reçoit **une seule question**, pas une exploration de toute la taxonomie de relations.
Le passage, ses unités/source et les identités candidates doivent accompagner le lien.
Le sens `member_of` ou `species_of` ne découle pas d'une simple cooccurrence.

## Contraintes d'intégration et livraison

- Le text mining doit préparer des unités bornées et une couverture dédupliquée.
  Un budget dépassé ou un contexte incomplet conserve la source ou met son identité
  en attente ; ce n'est jamais une preuve de redondance. Aucun texte n'est tronqué
  silencieusement. Les longs dossiers demandent un découpage et une couverture
  vérifiée au niveau des unités avant de pouvoir écarter une source entière.
- L'empreinte d'une demande inclut la version de politique, les prompts, le modèle
  déclaré et tout l'état fourni, donc les versions exactes de la source et de la
  couverture. Le client du fil principal doit y ajouter sa révision effective
  de poids, quantification, template et runtime avant de réutiliser un cache.
- Les résultats sont une sélection pour le merge, pas une suppression physique
  ni une fusion automatique d'IDs. Un rattachement rejeté devient une proposition
  de correction à appliquer par le mécanisme existant de la base.

**Implémenté isolément** : `DisambiguationLoop`, requêtes natives injectées par
la fonction `evaluate(request)`, scores et plan de sélection. Aucune ouverture
SQLite, aucun démarrage de modèle, aucune modification du worker historique.

**Raccordement livré** : `native_jev.py` vérifie l'identité effective des poids,
quantification, template et runtime, et compte exactement les tokens avant appel.
`curation_store.py` conserve plans, versions, demandes et sélections dans les tables
séparées `jev_v1_*`, avec contrôle des révisions et reprise par cache natif exact.
Le worker historique et ses décisions ne sont pas repris. Le serveur reste en pause
pour la curation ; seules des préparations algorithmiques ont été persistées.

`document_curation.py` compare les textes complets puis les unités exactes avec
la couverture retenue. Les copies et inclusions textuelles complètes ne demandent
aucun modèle ; les nouveaux nombres/statistiques sont conservés. Seules les unités
encore non couvertes et comparables lexicalement reçoivent la question d'apport.
Une unité utile ou incertaine conserve le document ; toutes ses unités doivent être
couvertes pour l'écarter du merge. Les unités retrouvent leurs versions et offsets
exacts. La recherche de passages utilise seulement les sources déjà retenues.
Les probabilités par unité restent des scores par unité, sans probabilité globale
de document artificiellement calculée. Les contextes dépassant 2 048 tokens sont
explicitement différés, sans troncature et sans exclusion sémantique.

`curation_profiles.py` retire les métadonnées de provenance des signatures de
profil, conserve les mécaniques et protège les variantes édition/époque/continuité.
Les sélections et aperçus des questions sont accessibles dans « Naviguer la base »,
sous chaque agrégat. Les liens restent secondaires et ne figurent pas dans le plan
documentaire exécuté le 22 octobre.

CLI : `tools/sw-kbase/curate-native.py`, avec le Python du runtime Jev contenant
`tokenizers`. Fournir `--database` et `--jev-runtime` avant la sous-commande.
`plan --all --limit 0 --prepare` fait uniquement la préparation CPU et le comptage
local des tokens. `status` est en lecture seule. `run --run-id ID --limit N`
respecte `openjev.pause` ; il ne lance ni Strata, ni embeddings, ni fusion d'IDs.
La construction du client vérifie intégralement les poids une fois (~55 s),
pas à chaque décision. Les imports originaux et les overlays restent intacts.

Validation locale : `python -m unittest discover -s tests -p sw_disambiguation.py -v`.
