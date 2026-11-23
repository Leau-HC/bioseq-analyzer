# Énergie Congo : prototype local

Cette application est un prototype indépendant. Elle utilise exclusivement un compte, une adresse, un compteur, des relevés et une facture fictifs. L'OTP est un code fixe de démonstration (`246810`). Les modes de paiement sont des simulations locales : aucune passerelle, banque ou donnée E2C n'est connectée.

## Lancer l'application

Depuis la racine du dépôt, après installation des dépendances du projet :

```powershell
python -m pip install -r requirements.txt
python energie_congo.py
```

Ouvrir ensuite `http://127.0.0.1:5000`. Connexion de démonstration :

- NIU : `NIU-DEMO-001`
- Code : `246810`

La base SQLite locale est créée dans `energie_congo_demo.db`. Elle ne contient que les données de démonstration.

## Limites avant tout déploiement

Ce prototype ne doit pas être exposé publiquement ni présenté comme une application officielle. Avant tout usage réel, il faut notamment remplacer l'identité de démonstration par une intégration autorisée aux systèmes E2C, connecter un fournisseur d'identité et un service OTP, définir les règles de protection et d'accès aux données, ajouter une base PostgreSQL opérée et sauvegardée, et intégrer des opérateurs de paiement via des notifications vérifiées. Les transactions de ce prototype ne règlent jamais la facture.

Pour les tests :

```powershell
python -m pytest tests/test_energie_congo.py -q
```
