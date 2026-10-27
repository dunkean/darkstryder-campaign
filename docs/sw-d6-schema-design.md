# Base SW D6 unifiée : architecture proposée

Revue du 21 octobre 2026. Cette note décrit la cible et les étapes ; elle ne prétend pas que toutes les tables ou opérations proposées sont déjà implémentées. Elle tient compte du changement de priorité demandé : indexer et exploiter rapidement les imports, sans validation systématique contre les livres.

## Décision centrale

Conserver les données importées, les identités, les profils de jeu et les fiches de lecture comme quatre choses distinctes. « Fusionner Luke » signifie rattacher plusieurs descriptions au même objet. Cela ne signifie ni remplacer les descriptions originales, ni faire une moyenne de ses compétences, ni confondre ses profils selon l'époque.

Une admission demande un identifiant d'import, une localisation récupérable et un hash. Le lien vers le livre ou la page cité par une base externe reste une référence déclarée. La vérification primaire devient un enrichissement facultatif, jamais une condition pour chercher, consulter, résoudre une identité ou rédiger à partir des imports. Les références déjà vérifiées restent conservées.

Le nouveau magasin peut être construit séparément sous runtime, avec tables préfixées ou base dédiée pendant le pilote, puis devenir la couche unifiée. Ne pas détourner silencieusement les contraintes de `packages/kbase/schema.sql` : ses citations imposent actuellement une page numérique et sa table `nodes` représente surtout des éléments extraits par source. Garder un pont `legacy_node_id → record/object` et les exports existants jusqu'à migration explicite. `campaign.json`, `entities.json` et le futur état de partie restent indépendants.

## Les objets et leurs documents

| Concept | Identité / exemple | Fonction |
|---|---|---|
| Source | Toolkit, archive wiki, livre local | Origine documentaire et famille de dérivation |
| Snapshot | commit, dump, photographie locale | État importé immuable, hash global et date |
| Record | ligne Statbase, entrée JSON, page wiki, fiche phase 3 | Unité réimportable avec clé d'origine stable |
| Version de record | contenu d'une ligne à une date donnée | Corps brut et représentation normalisée, append-only |
| Document / passage | article mixte ou fragment d'une fiche | Support textuel et recherche ; plusieurs sujets possibles |
| Objet | FarStar, modèle de corvette, Luke, espèce humaine | Identité stable indépendante des sources |
| Sujet de record | sujet principal, profil inclus ou sujet secondaire | Permet un record à plusieurs objets sans fusionner ces objets |
| Profil | Luke 2E à une époque donnée | Ensemble cohérent de statistiques et variantes |
| Assertion | « ce profil a telle compétence » | Valeur attribuée, contextualisée, réfutable |
| Relation | FarStar `instance_of` une classe | Assertion à objet cible, orientée et sourcée |
| Mention | occurrence de « Luke » dans un article de planète | Renvoi vers un objet sans changer le sujet de l'article |
| Fiche | synthèse française, profils, renvois et conflits | Vue matérialisée ou éditoriale sur ces éléments |

Deux axes décrivent les objets : `kind` (personnage, droïde, vaisseau, lieu, organisation, espèce, règle, pouvoir, événement, scénario, concept…) et `level` (individu, modèle/classe, archétype, concept, inconnu). Une hiérarchie plus fine reste possible sans multiplier les tables métier dès le départ. Les catégories des sources sont conservées séparément ; elles ne constituent pas une preuve d'identité.

Un « Stormtrooper » générique est un archétype ; un soldat nommé est un individu ; un type d'armure est un modèle d'équipement. Un vaisseau nommé et sa classe sont reliés par `instance_of`. Une classe dérivée est `subclass_of`, une modification distincte peut être `variant_of`. Le mot « modèle » ne doit pas devenir un champ universel qui confond modèle de droïde, exemplaire de droïde et profil statistique.

## Contrat SQLite proposé

Les colonnes JSON conservent les données brutes et les champs variables ; les identifiants, relations et attributs utilisés pour filtrer restent en colonnes et index SQL. Les clés étrangères sont activées. Les migrations sont numérotées et transactionnelles.

| Table | Colonnes essentielles et contraintes |
|---|---|
| `datasets` | `id PK, name, origin_kind, continuity, upstream_family_id, metadata_json` |
| `snapshots` | `id PK, dataset_id FK, content_hash, revision, captured_at, manifest_locator, UNIQUE(dataset_id,content_hash)` |
| `records` | `id PK, dataset_id FK, external_key, current_version_id FK nullable, UNIQUE(dataset_id,external_key)` |
| `record_versions` | `id PK, record_id FK, snapshot_id FK, content_hash, locator_json, raw_json, normalized_json, text, parser_revision, UNIQUE(record_id,content_hash,parser_revision)` |
| `passages` | `id PK, record_version_id FK, ordinal, heading, text, offsets_json, hash, UNIQUE(record_version_id,ordinal)` |
| `objects` | `id PK, preferred_name, kind, level, status, revision, created_at` |
| `aliases` | `object_id FK, name, normalized, language, record_version_id FK nullable, status`; index `normalized` |
| `subject_slots` | `id PK, record_version_id FK, slot_key, label, kind_hint, level_hint, scope_json, UNIQUE(record_version_id,slot_key)` |
| `assignments` | `id PK, subject_slot_id FK, object_id FK, decision_id FK nullable, status, valid_from, valid_to`; au plus une affectation active par slot |
| `profiles` | `id PK, object_id FK, record_version_id FK, slot_key, system, edition, era, continuity, origin_kind, variant_label, raw_stats_json, normalized_stats_json`; unicité locale au record, pas unicité globale du profil |
| `claims` | `id PK, subject_id FK, profile_id FK nullable, predicate, literal_json nullable, object_id FK nullable, scope_json, polarity, record_version_id FK, passage_id FK nullable, derivation, status` |
| `mentions` | `id PK, passage_id FK, start, end, surface, proposed_kind, resolved_object_id FK nullable, decision_id FK nullable, status` |
| `decisions` | `id PK, task, input_fingerprint, inputs_json, raw_decision, decision, scores_json, producer_json, status, response_locator`; unicité sur fingerprint, historique conservé |
| `operations` | `id PK, kind, actor, payload_json, inverse_json, expected_revision, created_at, undone_by nullable` |
| `editorial_overlays` | `object_id FK, field, value_json, scope_json, revision, operation_id FK`; historique séparé ou opérations |
| `documents` | `id PK, object_id FK, format, body, sections_json, input_fingerprint, producer_json, status, revision` |
| `document_support` | `document_id FK, section_key, record_version_id FK, claim_id FK nullable` |
| `tasks` | `id PK, task, input_fingerprint UNIQUE, scope_id, priority, context_json, status, attempts, lease_until, error, output_json` |

Dans `claims`, exactement l'un de `literal_json` ou `object_id` est présent. Une vue `relations` expose les assertions avec objet cible. On peut préférer une table de relations séparée dans le MVP si cela simplifie l'API ; elle doit conserver les mêmes attributs de provenance, de portée et de statut. Les éléments sémantiques spéciaux (règles, scènes, pouvoirs) restent des objets avec leurs prédicats ; ne pas enfouir leurs prérequis dans un résumé uniquement textuel.

Les profils conservent les formes originales des dés, unités, portées, notes et tableaux. Une normalisation `4D+2 → {dice:4,pips:2}` est utile, mais une expression conditionnelle, un bonus spécial ou une échelle de combat n'est pas ramené de force à un nombre. `unknown`, `absent` et une valeur zéro ne sont pas interchangeables. Un conflit entre deux profils n'est pas automatiquement un conflit factuel : édition, époque, équipement ou conversion peuvent l'expliquer.

Une provenance légère tient dans `record_version_id + locator_json + content_hash`. Exemples de localisateurs : `{kind:'json',file,key,pointer}`, `{kind:'ndjson',file,line,id}`, `{kind:'sql',table,primary_key}`, `{kind:'wiki',page_id,revision_id,title}`, `{kind:'sheet',sheet,row,source_id}`, `{kind:'phase3',document_id,path,pages}`. Les pages imprimées/PDF restent distinctes. Aucun faux numéro de page n'est fabriqué pour une ligne JSON.

Conserver séparément `verification_status` (source primaire non contrôlée, lien retrouvé, extrait vérifié), `review_status` (non relu, relu), `derivation` (import déterministe, extraction IA, édition personnelle, génération) et `origin_kind` (WEG, REUP, fan, résumé secondaire…). Un simple champ `trusted` ne représente pas ces distinctions. Une compilation n'est pas un témoin indépendant lorsqu'elle recopie une autre base : `upstream_family_id` ou une relation `derived_from` évite de transformer cinq copies en cinq confirmations.

## Import et indexation sans modèle

1. Identifier les snapshots par manifests/hashes existants, puis traiter les records en flux. La clé est celle du fournisseur, pas le nom affiché. Pour un record sans ID, persister une clé d'adaptateur et signaler sa stabilité moindre ; une position de tableau n'est pas toujours stable entre versions.
2. Importer d'abord les JSON dérivés existants, les packs Foundry NDJSON, les lignes Statbase déjà converties et les fiches phase 3. Parser les données SQL dans le format attendu ; ne jamais exécuter un dump tiers dans la base unifiée. Le wiki importe la révision retenue de chaque page, avec redirections ; les anciennes révisions restent dans le snapshot source, sans constituer autant de nouvelles entités.
3. Conserver le brut, les champs inconnus, la normalisation et les warnings. Un adaptateur connaît déjà beaucoup de types et de relations (prérequis, liens wiki, IDs liés) ; réserver les questions IA aux ambiguïtés.
4. Donner immédiatement un objet provisoire à chaque sujet principal, ou au moins rendre tous les records cherchables. L'absence de résolution n'empêche pas leur consultation. Les objets provisoires ne sont pas présentés comme une déduplication achevée.
5. FTS5 sur noms/alias/corps et passages, trigrammes de noms, index des identifiants externes et termes rares. Les vecteurs lexicaux creux existants sont réutilisables ; ce ne sont pas des embeddings sémantiques. Une couche d'embeddings peut être ajoutée après mesure du rappel, sans devenir un prérequis.

## Résolution : plusieurs décisions, plusieurs coûts

Le contrat local `packages/sw_kbase/semantic.py` définit des tâches à choix fermés et abstention :

| Tâche | Question | Conséquence possible |
|---|---|---|
| `type` / `classification` | Individu, modèle, archétype, règle, document mixte… ? | Classifier le sujet, ou proposer des slots distincts |
| `identity` | Même sujet, sujet différent, simple mention, incertain ? | Proposer une affectation au même objet |
| `mention` | Cette occurrence désigne-t-elle ce candidat ? | Résoudre un renvoi local au passage |
| `relation` | Quelle relation orientée est explicitement exprimée ? | Proposer une assertion relationnelle |
| `importance` | Central, secondaire, arrière-plan ou hors portée dans CE contexte ? | Prioriser préparation/rédaction, jamais décider une identité |
| `merge_utility` | Complément, répétition, conflit, profils séparés… ? | Choisir l'organisation des fiches et le travail de synthèse |

Une relation ne se déduit pas du verdict `different_entity`. Celui-ci peut concerner deux homonymes sans relation, un modèle et un exemplaire, ou deux personnes alliées. De même, la présence d'une personne dans une fiche de planète ne justifie ni leur fusion, ni une relation permanente `located_in` sans passage explicite et contexte temporel.

Commencer par les signaux déterministes (ID externe identique, redirection déclarée), puis les candidats plausibles et les questions nécessaires. Séparer décision d'identité et conservation des profils. Les labels `same_entity` et `different_entity` sont des hypothèses du modèle, pas une mesure de vérité. Le score et la marge ne sont pas calibrés ; examiner un échantillon ciblant noms génériques, individus/modèles, traductions et profils contradictoires avant d'automatiser largement.

Une entrée abrégée retourne `budget_limited` et ne produit pas de décision acceptée, y compris négative. Le rédacteur refuse une entrée trop grande et réclame un découpage explicite. Le code ne lance aucun modèle, ne télécharge rien, ne suit aucune redirection et utilise uniquement des endpoints HTTP de boucle locale.

## Éviter le coût quadratique

Le top-k lexical existant borne les voisins ordinaires, mais le code actuel énumère tous les noms/alias exacts. Un bucket de m homonymes crée m(m−1)/2 paires. « Stormtrooper », des templates et les éditions wiki répétées peuvent donc annuler le gain.

Remplacer les grands buckets par une progression contre des représentants : conserver tous leurs membres dans le bucket, mais ne comparer chaque nouveau record qu'à quelques ancres compatibles et priorisées. Les branches incertaines restent consultables dans une file dédiée. Conserver les raisons de récupération et le nombre de candidats reportés, afin qu'un plafonnement ne soit pas présenté comme une preuve de différence.

Combiner les canaux de candidats : clés/URLs partagées, alias exacts, trigrammes, titres bilingues, contexte rare, relations communes. Réserver un quota par canal évite qu'un même canal lexical mange tous les candidats. Une incompatibilité individu/modèle réduit les candidats d'identité mais les rend utiles au canal relations. Si les types ne sont que prédits ou inconnus, ne pas éliminer définitivement la paire.

Les mentions sont résolues occurrence → candidats d'objet, avec au plus k candidats par occurrence. Les relations sont extraites de passages où les sujets apparaissent ou sont explicitement liés, pas de toutes les paires d'objets. Les gros articles sont découpés en passages ; on n'envoie jamais un livre complet.

## Reprise, fusion et annulation

Les IDs d'objets sont alloués et persistés. Ils ne dérivent pas du nom, de l'ensemble des membres ou d'une ancre remplaçable. Un ancien ID fusionné reste un alias/redirection ; une scission peut le réactiver ou créer un nouvel ID avec historique explicite.

Une fusion ferme et recrée des affectations dans une transaction journalisée. Les records, versions, profils et assertions restent intacts. Une scission réaffecte des slots sélectionnés ; elle invalide les synthèses concernées et remet à réviser les mentions/relations dont la cible était ambiguë. Une annulation vérifie la révision attendue : ne pas rejouer une inverse aveuglément après des modifications ultérieures. Les overlays personnels restent attachés à leur objet/portée et un conflit de déplacement doit être visible.

Conserver la règle existante d'identité directe avec une ancre et les incompatibilités connues ; ne pas faire une union transitive de toutes les paires positives. Un lien A–B et B–C n'établit pas A–C. Les groupes peuvent être provisoires et publiés par portée de travail : la condition actuelle « toutes les paires du corpus sont terminées » ne convient pas à une base qui s'enrichit en continu.

Le fingerprint d'une tâche inclut hash/version des entrées, tâche, prompt et labels, modèle/révision, budgets et règles d'acceptation. Le cache stocke la réponse brute et la décision séparément. Un changement de seuil peut ensuite être réévalué sans inférence si les réponses brutes restent compatibles ; le MVP peut invalider plus largement pour simplifier. Les contextes partagés réduisent la répétition au serveur ; chaque question garde son identité et sa signature. À grande échelle, stocker la réponse de lot une seule fois et des références par décision évite de la dupliquer huit fois.

## Synthèse et consultation par l'IA

Le rédacteur reçoit des records choisis, leurs IDs et leurs variantes, éventuellement des relations et conflits. Il retourne des paragraphes `{section,text,record_ids}` et une liste explicite de conflits. Le programme valide les IDs et les budgets, puis garde les hashes des entrées. Une référence à un record est vérifiée mécaniquement ; son soutien sémantique reste non relu. Aucune vérification PDF n'est imposée pour autoriser cette fiche.

L'utilisateur précise que Qwen avec Strata fonctionne déjà dans le projet de transcription pour les gros volumes de contexte. Réutiliser cette chaîne existante après le text mining et OpenJev ; ne pas la remplacer par un nouveau modèle ou un nouveau service. Garder son URL locale, l'ID de modèle réellement servi, sa révision et ses budgets configurables. `WriterClient` expose le contrat de synthèse par chat ; si la chaîne existante ajoute un transport particulier, l'adaptateur réutilise son fonctionnement validé.

L'API de lecture doit fournir : recherche avec filtres ; objet avec profils/variantes ; relations et mentions ; consultation d'un record et de sa provenance. Chaque réponse a un budget et une liste d'IDs stables, un statut provisoire/relu, une continuité, une époque si connue et une visibilité MJ/joueurs. Les secrets MJ se filtrent avant la remise de contexte au modèle. Une réponse manquante déclenche une recherche complémentaire ciblée, pas un chargement global du corpus. Les exports sont versionnés et contiennent ces mêmes identités.

## MVP et suite

**MVP utile** : imports déterministes, provenance légère, FTS, objets provisoires, profils préservés, candidats bornés, tâches locales explicites, décisions historisées, consultation/recherche et export ; quelques fusions/scissions réversibles. Pilote DarkStryder d'abord, puis extension progressive. Le classement doit pouvoir fonctionner sur une portée ou un quota, sans attendre toutes les paires.

**Étape suivante** : résolution des mentions, relations contextuelles, rôle narratif, priorité de rédaction, conflits de profils, synthèses record-backed et overlays modifiables. Ajouter ensuite normalisation fine des règles, prérequis, tables, scènes et interface de curation.

**À mesurer avant de complexifier** : rappel des alias bilingues et OCR, taille des buckets génériques, coût par mille décisions, fraction d'abstentions, nombre de profils injustement rapprochés, latence de consultation, reprise après interruption et invalidation incrémentale. Le GPU ne doit pas être occupé par des questions auxquelles les tables répondent déjà.

**Régressions indispensables** : réimport idempotent sans écraser les overlays ; type classe/individu ; homonymes ; absence de transitivité automatique ; plusieurs profils d'une identité ; scission/annulation ; citations de record interdites si inconnues ; refus des réponses tronquées ; budget limité explicite ; accès local et confinement des chemins ; aucun démarrage automatique de modèle ; préservation des transcriptions et sorties historiques.
