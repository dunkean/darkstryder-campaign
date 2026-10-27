# Découpage algorithmique en blocs cohérents

`document-sections/2.2-coherent-blocks` corrige le découpage des cartes web qui séparait notamment `Move` et `10`. Aucun modèle n'intervient.

- Un titre Markdown, wiki, HTML ou une rubrique connue accompagne son contenu.
- Les séries de libellés, valeurs et compétences à dés sont regroupées, même lorsque le HTML converti a introduit des lignes vides entre les cellules.
- Les paragraphes narratifs restent distincts, y compris lorsqu'ils mentionnent des dés.
- Les fiches JSON compactes restent des objets entiers. Les longues structures conservent leur découpage et leurs chemins JSON.
- La cible de 1 600 caractères borne les regroupements de petites unités ; un libellé n'est pas coupé de sa valeur scalaire. Les paragraphes narratifs longs restent entiers et seront fractionnés selon le budget du modèle à son exécution ultérieure.
- Un document réellement court est conservé tel quel et signalé comme tel. Aucun remplissage, aucune synthèse, aucune suppression de contenu n'est effectué.

Les nouveaux blocs sont des sous-chaînes exactes des documents importés. Chaque ancien passage doit être contenu dans exactement un nouveau bloc, avec vérification du texte et des positions. La table `legacy_paragraph_map` relie les anciens IDs aux nouveaux ; les positions et les unités du parseur sont conservées.

La reconstruction utilise un nouveau `enrichment.sqlite`. Elle conserve les références, leur classement et l'ordre des documents. L'ancienne base reste intacte, avec ses requêtes et décisions. Tous les candidats du nouveau plan sont en attente : les votes ne sont pas transférés, car la couverture progressive des comparaisons suivantes dépendait de l'ancien découpage. Même un bloc inchangé ne justifie pas de réutiliser une décision prise avec un autre contexte.

```powershell
python tools/sw-kbase/rechunk.py --database <knowledge.sqlite> --previous <ancien-plan> --output <nouveau-plan>
```

Le programme ne lance ni serveur, ni modèle, ni inférence. Il refuse une source modifiée et ne publie un plan complet qu'après contrôle de l'inventaire et de l'intégrité. `planning-progress.json` donne la progression ; `plan.json` contient le bilan final. Les vues de navigation lisent le nouveau plan après changement explicite de leur configuration locale. La pause Jev est conservée.

Les lectures sont préchargées en RAM pour éviter les accès aléatoires SQLite sur cette station ; `--stream` permet de réduire la mémoire sur une machine plus petite. `activate-chunks.py --database <knowledge.sqlite> --output <nouveau-plan>` met à jour les pointeurs locaux et le checkpoint uniquement si la pause est présente et le plan vérifié. Les anciens fichiers de contrôle sont sauvegardés dans `control-before/`. Cette activation ne lance aucun calcul.

Les insertions SQLite sont groupées avec checkpoint final du journal WAL. `--resume` reprend une reconstruction CPU interrompue à partir des documents déjà validés ; il refuse un plan terminé ou contenant des requêtes modèle.
