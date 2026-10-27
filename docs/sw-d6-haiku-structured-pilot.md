# Pilote Haiku : fiche structurée depuis les références et compléments

Pilote exécuté le 27 octobre 2026 après le GO explicite de l’utilisateur et le renforcement de la fidélité du prompt. **50 appels, 50 réponses enregistrées ; aucune soumission batch.**

Résultat : 45 JSON conformes et 5 sorties hors contrat. Coût estimé d’après l’usage fournisseur : **0,0884151 USD** pour 239 626 tokens d’entrée et 128 905 de sortie. Équivalent batch indicatif à mêmes volumes : 0,04420755 USD. Le JSON conforme ne garantit pas l’exhaustivité sémantique.

La revue locale sur **http://127.0.0.1:8769/** expose les 50 résultats et leurs **104 documents sources**, avec distinction entre référence, compléments transmis et sources écartées. Chaque source originale est lisible intégralement dans sa version exacte. Les 155 pages de navigation/source ont répondu HTTP 200. Le serveur n’expose ni configuration privée ni SQLite.

Présentation simplifiée à la demande de l’utilisateur : `/compare` et la page d’accueil affichent directement **l’entrée enregistrée à gauche et la réponse reçue à droite**. Un sélecteur parcourt les 50 fiches. Les textes transmis sont ouverts, le prompt système et la requête exacte sont consultables ; le JSON reçu est mis en forme, avec bascule vers le brut inchangé. Les anciennes URL de fiches ouvrent aussi ce comparateur. Cette lecture utilise les snapshots de requête/réponse, sans reconstruction du contexte depuis le code courant et sans nouvel appel Haiku.

Les 5 écarts : copie de l’enveloppe d’entrée pour une grosse fiche, champs supplémentaires à la racine, statistiques imbriquées ou numériques. Les 45 fiches conformes ont un sous-type vide dans 43 cas. Parmi les 10 `unknown` du pilote, 6 ont un type renseigné dans une sortie conforme, 2 restent `unknown` et 2 ont une sortie hors contrat. Ces points doivent être examinés avant le batch global. Les retours bruts sont préservés ; les fences JSON et sous-types vides ont été traités localement sans repayer d’appels.

- Contrat complet : [sw-rich-article.schema.json](../config/sw-rich-article.schema.json).
- Prompt effectivement utilisé : [sw-article-extraction.prompt.txt](../config/sw-article-extraction.prompt.txt), 111 mots. Reformulation fidèle, aucune information omise, rien inventé ni comblé.
- Configuration publique sans chemin privé ni secret : [sw-anthropic-extraction.example.json](../config/sw-anthropic-extraction.example.json).

## Données produites

Le JSON généré contient uniquement `type`, `subtype`, `sections`. Le modèle ne gère aucun ID, score, citation ou relation. Les liens restent internes à SQLite. La provenance et les identifiants des entrées sont conservés par le moteur hors du contexte modèle et dans des métadonnées séparées.

| Champ | Fonction |
|---|---|
| `type`, `subtype` | Noms existants fournis au modèle ; nouveaux noms autorisés si nécessaire, sinon `unknown`. Pas de confiance ni de justification obligatoire. |
| `sections[].title`, `subtitle` | Titre et sous-titre facultatif, vide s’il est absent. |
| `sections[].content` | Texte lisible ; paragraphes séparés par une ligne vide. |
| `sections[].type` | Suggestions : `description`, `rule`, `stats`, `scenario`, `gm_note`, `conflict`, `note`. |
| `sections[].stats` | Dictionnaire libre clé/valeur texte, ou `{}`. Valeurs, unités et dés conservés sans normalisation. |

Les contradictions sont des sections ordinaires de type `conflict`, avec variantes narratives, règles ou valeurs dans `content`/`stats`. Les règles et profils restent dans des sections séparées. La formalisation des stats, le parsing et les IDs de sections sont reportés à une étape du moteur. La validation locale contrôle la forme du JSON et ne prétend plus vérifier une couverture sémantique ou des citations par assertion. Les sources exactes restent dans les métadonnées de la fiche ; leur rattachement fin aux sections est explicitement non résolu.

Le dictionnaire libre `stats` est validé localement ; le pilote ne force pas la grammaire fermée des sorties structurées Anthropic. Les réponses tronquées ou invalides restent enregistrées et comptabilisées, sans relance automatique.

## Échantillon et arrêt

Un document du pilote est **une fiche d’entité consolidée**, pas un paragraphe : référence et compléments utiles/incertains issus de toutes ses sources. Les sous-fragments redondants d’un bloc partiellement retenu sont exclus exactement, par offsets et votes. Aucun nouveau tri JEV.

50 fiches maximum, sélection déterministe par quotas de types, dont 10 `unknown` ciblés. Taille bornée à 500–45 000 caractères de preuves retenues par fiche. Le budget d’entrée a été porté de 32 000 à 64 000 tokens après comptage exact : les profils structurés joints portent une grosse fiche à 48 142 tokens. Le budget de sortie est d’au moins 12 000 tokens, adapté jusqu’à deux fois l’entrée pour les grosses fiches, avec plafond 64 000. Les exclusions de taille sont comptées : le coût de cet échantillon ne suffit pas à extrapoler directement tout le corpus. Les gros ensembles demanderont un traitement par paquets.

Les sources et la base de sélection sont ouvertes en lecture seule. Le pilote a son propre SQLite, ses réponses brutes, JSON validés, aperçus HTML et rapports hors Git. La clé est lue depuis son fichier privé uniquement lors d’un appel ; elle n’est copiée dans aucun export. `WAIT_FOR_USER_GO` bloque génération et lancement implicite. Le comptage gratuit est une commande séparée, qui transmet le texte au fournisseur sans générer de réponse ; il attend également la revue demandée avant son utilisation ici.

## Coût et reprise

Le rapport enregistre usage fournisseur, latence et coût estimé par fiche, y compris une réponse refusée, tronquée ou invalide. Ce coût n’est pas une facture. Plafond de dépenses configuré à 1 USD pour le pilote ; aucune relance automatique après erreur réseau d’issue inconnue. Une réponse complète en cache n’est pas refacturée à la reprise.

Tarifs vérifiés : [Haiku 5.5](https://platform.claude.com/docs/en/models/haiku-5-5/overview), 0,10 USD/M tokens en entrée et 0,50 USD/M en sortie jusqu’à 100 000 tokens d’entrée ; tarifs multipliés par cinq au-delà. [Message Batches](https://platform.claude.com/docs/en/build-with-claude/batch-processing) : remise de 50 % sur entrée/sortie. Les requêtes du pilote n’activent pas le cache de prompt ; les éventuels compteurs de cache sont néanmoins pris en charge dans le suivi. La génération batch globale reste une étape ultérieure à valider ; `batch-file` prépare seulement un fichier local.

```text
anthropic-extract.py --output RUNTIME prepare --config CONFIG_LOCAL
anthropic-extract.py --output RUNTIME count
anthropic-extract.py --output RUNTIME run --execute
anthropic-extract.py --output RUNTIME report
anthropic-extract.py --output RUNTIME batch-file --destination FICHIER_JSONL
```

Avant un nouveau GO : `prepare` et `report` n’appellent aucun modèle. Ce pilote est terminé ; une nouvelle exécution ne doit pas renvoyer les réponses déjà enregistrées. Le batch global reste non autorisé à ce stade.
