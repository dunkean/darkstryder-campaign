# Pilote Haiku : fiche structurée depuis les références et compléments

Préparation CPU uniquement au 27 octobre 2026. **Attendre le GO de l’utilisateur après revue du schéma et du prompt.**
La lecture authentifiée de la fiche modèle confirme `claude-haiku-5-5` ; aucune génération n’a été lancée.

- Contrat complet : [sw-rich-article.schema.json](../config/sw-rich-article.schema.json).
- Prompt effectivement utilisé : [sw-article-extraction.prompt.txt](../config/sw-article-extraction.prompt.txt), 101 mots.
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

50 fiches maximum, sélection déterministe par quotas de types, dont 10 `unknown` ciblés. Taille bornée à 500–45 000 caractères de preuves retenues par fiche et 32 000 tokens d’entrée par requête. Les exclusions de taille sont comptées : le coût de cet échantillon ne suffit pas à extrapoler directement tout le corpus. Les gros ensembles demanderont un traitement par paquets.

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

Avant le GO : seuls `prepare` et `report` sont utilisés. Aucun appel Messages ou soumission batch.
