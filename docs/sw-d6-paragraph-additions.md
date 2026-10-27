# Passe suivante : paragraphes à ajouter

> Proposition initiale conservée pour comparaison. Le contrat actuel est
> [la chaîne d'enrichissement v2](sw-d6-enrichment-v2.md) : toutes les entrées,
> choix de référence, votes global **et** par paragraphe systématiques,
> couverture progressive et sortie structurée Strata/Qwen. Ne pas lancer
> l'ancienne CLI pour réaliser cette nouvelle phase.

**Code préparé ; aucune exécution sur le corpus réel.** Cette passe est distincte
du dédoublonnage documentaire en cours. Elle exige un run documentaire terminé.

- Lire uniquement les références et documents **retenus**, avec leurs versions
  exactes ; laisser les identités en attente de rattachement hors de cette passe.
- Numéroter les paragraphes `P00001`, `P00002`, etc., avec document, version,
  offsets exacts et empreinte. Les longs paragraphes gardent leur numéro et sont
  évalués par unités bornées ; toutes les unités doivent être couvertes pour
  écarter le paragraphe entier.
- Couvrir les copies exactes sans modèle ; éviter les questions sur des éléments
  purement présentatifs tels que « Previous Page ».
- Pour chaque paragraphe encore comparable, poser la question d'apport utile en
  fournissant son numéro et des passages des sources **déjà retenues**. Les réponses
  donnent les numéros redondants et les passages à ajouter. Un score de redondance
  **strictement supérieur à 80 %** permet l'exclusion ; le doute conserve le texte.
- Enrichir progressivement la couverture, sans comparer un paragraphe à des
  sources futures susceptibles d'être elles-mêmes écartées. Aucune suppression
  mutuelle ; les profils structurés et originaux restent indépendamment conservés.

Sortie : `additions.json`, avec paragraphes `reference`, `add`, `add_uncertain`,
`skip_redundant` ou `metadata`, texte exact, numéros, provenance et preuves.
Les contextes dépassant 2 048 tokens restent conservés comme incertains.
Il n'y a ni rédaction Strata, ni embeddings, ni modification des textes sources.

Le serveur actuel fait un passage par question : numéroter plusieurs paragraphes
ne crée pas un batch GPU. Le script retourne la liste des paragraphes redondants
à partir des décisions bornées ; il ne fournit jamais un livre entier au modèle.

Implémentation : `packages/sw_kbase/paragraph_curation.py`.
CLI : `tools/sw-kbase/curate-paragraphs.py` (Python du runtime Jev pour l'inférence).

```text
curate-paragraphs.py --database DB --output NOUVEAU_DOSSIER plan --document-run-id RUN_TERMINE
curate-paragraphs.py --database DB --output DOSSIER run --jev-runtime RUNTIME --execute
```

`plan` ne démarre aucun modèle. `run` exige séparément `--execute`, respecte
`openjev.pause`, contrôle les versions et garde son cache et ses checkpoints dans
une SQLite distincte du dossier de sortie. Une source, un overlay ou un regroupement
modifié impose de refaire le plan. Reprise déterministe par cache de demandes
exactes, isolé du cache du premier tri documentaire et des anciennes décisions.

Tests : `python -m unittest discover -s tests -p sw_paragraph_curation.py`.
Ces tests utilisent exclusivement des sources et réponses fictives.
