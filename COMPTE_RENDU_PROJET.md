# Compte Rendu d'Avancement du Projet

**Tunisia Energy RAG — Tableau de bord énergétique tunisien**
**Date du rapport : 17 août 2026**

---

## 1. Vue d'ensemble du projet

Le projet est une plateforme RAG (Retrieval-Augmented Generation) dédiée au secteur énergétique tunisien. Elle permet de :

- **Chat sourcé** : poser des questions en français ou arabe standard sur des documents PDF spécialisés (STEG, ANME, IRENA, ADEME, etc.) et recevoir des réponses avec citations (nom du fichier, numéro de page).
- **Carte des pannes** : visualiser en temps réel les coupures d'électricité signalées par les utilisateurs (carte Leaflet + carte SVG animée de la Tunisie par gouvernorat).
- **Calculateur solaire** : calculer le retour sur investissement d'une installation photovoltaïque (ROI).
- **Administration** : statistiques de purge, configuration runtime, via une page `/admin` protégée par clé API.

L'application est destinée à devenir un produit complet — le plan détaillé est dans [`PRODUCT_PLAN.md`](PRODUCT_PLAN.md).

---

## 2. Architecture technique

| Couche | Technologie |
|---|---|
| **Backend** | FastAPI (Python asyncio), API REST + streaming SSE |
| **Pipeline RAG** | PDF → OCR arabe → chunks → embeddings ChromaDB → récupération hybride (vectoriel + BM25) → génération LLM (client OpenAI asynchrone, Mistral Large) |
| **Données** | PostgreSQL 16 (données applicatives réelles, dev = prod), SQLite en mémoire (laboratoire de tests jetable, isolé), ChromaDB (stockage vectoriel) |
| **Schéma** | Géré par **Alembic** (migrations versionnées) |
| **Frontend** | React 18 + Vite + TypeScript, i18n 2 langues (fr/ar), RTL pour l'arabe, Tailwind, Zustand, TanStack Query, react-leaflet, react-markdown |
| **Infra** | docker-compose (postgres, db-seed, pg-backup, backend, frontend/nginx, ngrok), healthchecks basés sur `/ready` |
| **Sécurité** | Auth JWT (email/mot de passe, bcrypt), rate limiting (slowapi), en-têtes de sécurité, clé admin à comparaison en temps constant, aucun secret dans le repo |

---

## 3. Étapes réalisées (par ordre chronologique)

### 3.1 Prototype initial
- Mise en place de la structure du dépôt, de l'environnement (`requirements.txt`, `.env`) et d'un premier prototype fonctionnel.
- Commit « prototype ready ».

### 3.2 Pipeline de collecte et de traitement des données
- **Collecteur de PDF** : recherche hybride SerpApi → DuckDuckGo (`src/ingestion/collector.py`) avec journal de téléchargement.
- **Triage multi-critères** : Gate 1 (métadonnées) + Gate 2 (analyse profonde via LLM) pour filtrer les documents pertinents (`src/utils/triage.py`), avec rapport Markdown et historique des scores.
- **Ingestion** : extraction du texte (pdfplumber), OCR arabe via EasyOCR (gestion des formes de présentation arabes), découpage en chunks (RecursiveCharacterTextSplitter), normalisation Unicode (`src/ingestion/`, `src/utils/verify_unicode.py`).
- **Indexation** : embeddings (sentence-transformers, `paraphrase-multilingual-MiniLM-L12-v2`) dans ChromaDB.

### 3.3 Pipeline RAG asynchrone
- Conversion du client OpenAI en version **asynchrone**.
- Récupération ChromaDB exécutée dans un thread pool (`run_in_threadpool`) pour ne pas bloquer la boucle d'événements.
- **Stratégie de budget de tokens** (`src/utils/token_manager.py`) : l'historique de conversation est tronqué dynamiquement (du plus récent au plus ancien) au lieu d'un simple `chat_history[-4:]` — évite les erreurs 400 (dépassement de fenêtre de contexte) quand l'utilisateur colle un très gros texte.
- **Streaming SSE** pour le chat (endpoint `/api/chat/stream`).
- Correction d'un **crash Windows sur l'affichage de l'arabe** en console (encodage UTF-8 forcé).

### 3.4 Base de données applicative
- Couche SQLAlchemy asynchrone (`src/database/`) : modèles `users`, `conversations`, `messages`, `outage_reports`, `settings`.
- Fichier de seed (`src/database/seed.py`) pour peupler la base.
- Service PostgreSQL dans docker-compose avec healthchecks.
- **Purge automatique** des signalements de panne après expiration (TTL 5 h par défaut) + endpoint admin de statistiques de purge.
- Endpoint admin de **configuration runtime** (GET/PUT settings).

### 3.5 Migration du frontend
- Remplacement de l'interface **Streamlit** par une **SPA React 18 + Vite + TypeScript** (`frontend/`).
- Modules : chat (bulles, citations, dropdown des sources), carte des pannes (react-leaflet), **carte SVG animée de la Tunisie** (tunisia-geo + node-pulse), calculateur solaire (Recharts), page `/admin`.
- i18n 2 langues (fr/ar) avec bascule RTL/LTR.
- **Dropdown des sources** après chaque réponse IA : liste structurée des chunks avec nom de fichier, date et page, bouton « copier » et vue plein texte extensible.
- Sélection des **24 gouvernorats de Tunisie** dans le formulaire de signalement.
- Liaison de la carte SVG aux **données réelles de l'API** (comptage des pannes par région), filtres par statut (EN ATTENTE / VÉRIFIÉ / RÉSOLU), clic sur un nœud pour filtrer la carte Leaflet sur le gouvernorat.

### 3.6 Sécurité
- **Authentification réelle** : inscription/connexion email + mot de passe (bcrypt), JWT, endpoints `/api/auth/register`, `/login`, `/me`, propriété des conversations et signalements par utilisateur.
- **Rate limiting** par IP sur tous les endpoints (chat, auth, outages, admin), 429 + en-tête `Retry-After`, stockage Redis optionnel.
- **En-têtes de sécurité** (nosniff, DENY, Referrer-Policy, HSTS/CSP optionnels) et CORS piloté par variable d'environnement.
- **Page `/admin`** protégée par clé API (`X-Admin-Key`) avec comparaison en temps constant et réponses identiques en cas d'échec (pas d'oracle pour les attaquants). Tests de sécurité dédiés.
- Aucun secret commité ; `.env` gitignoré (un token ngrok accidentellement commité a été **purgé de l'historique git**).

### 3.7 Migrations de schéma (Alembic) et unification Postgres
- Mise en place d'Alembic : `alembic.ini`, `env.py` asynchrone, migration initiale `0001_initial.py` correspondant exactement aux 5 modèles.
- `ensure_schema()` (`src/database/schema.py`) : base neuve → `upgrade head` ; base « époque create_all » → `stamp head` (adoption sans perte de données) ; base migrée → no-op.
- Suppression des hacks (`create_all` du seeder, `ensure_user_columns`).
- **Dev = prod** : le développement local utilise le même Postgres que la production ; SQLite en mémoire réservé aux tests.
- Génération de `schema.sql` (instantané de référence du schéma PG).
- Le seeder passe par les migrations ; docker-compose attend la fin du `db-seed` avant de démarrer le backend.

### 3.8 Fiabilité et opérations
- **Split liveness/readiness** : `/health` (toujours 200 si le processus tourne) vs `/ready` (vérifie PostgreSQL + ChromaDB, renvoie 503 avec la liste des dépendances en panne). Les healthchecks Docker s'appuient désormais sur `/ready`.
- **Sauvegardes Postgres** : service `pg-backup` (pg_dump programmé, rétention configurable) + scripts `scripts/backup_db.sh` et `scripts/restore_db.sh`.

### 3.9 Programme qualité RAG (moitié récupération)
- **Récupération hybride** (`src/rag/hybrid.py`) : fusion vectoriel (ChromaDB, top-25) + BM25 (mots-clés, top-25) via **Reciprocal Rank Fusion** → top-5.
- **Reranking optionnel** par cross-encoder multilingue (chargé paresseusement, activable par variable d'environnement, repli gracieux en cas d'échec).
- **Harnais d'évaluation hors ligne** (`src/eval/`) : jeu d'or `data/eval/golden_qa.json` (6 requêtes fr/ar, ancrées dans le corpus réel) + métriques recall@k et MRR.
- Résultat mesuré : **recall@5 0,83 → 1,00** et **MRR 0,700 → 1,000** (sur 6 requêtes, k=5).

---

## 4. État des tests

| Suite | Nombre | Détail |
|---|---|---|
| **Backend (full)** | 134 tests | 128 « fast » · 130 « medium » · 134 « full » |
| — dont auth | 20 | register/login/me, sécurité (no-oracle, bcrypt, propriété) |
| — dont API+DB | 25 | CRUD pannes, conversations, SSE, admin purge/config |
| — dont hybrid + eval | 22 | 13 hybrides + 9 métriques d'évaluation |
| — dont rate limit + sécu | 9 | 429, en-têtes, CORS, clé admin |
| — dont migrations / readiness | 8 | 4 migrations + 4 readiness |
| — dont seed / tokens | 14 | 8 seed + 6 budget de tokens |
| **Frontend (Vitest)** | 108 tests | store chat, parseur SSE, composants chat, logique carte, carte SVG, filtres, auth store/services/modal |
| **Eval RAG** | — | `python -m src.eval.evaluate` (vector vs hybrid) |
| **Stress check** | 2 | gros payloads (~36k tokens) via `/api/chat` |

> Le fichier [`tests/auto_test_config.md`](tests/auto_test_config.md) documente les durées moyennes par test et définit 3 scénarios :
>
> | Scénario | Quand | Commande |
> |---|---|---|
> | ⚡ **FAST RUN** | après chaque modification de code (aucun appel LLM) | voir §3 du fichier |
> | 🚀 **MEDIUM RUN** | après modification des prompts / du client LLM | voir §3 du fichier |
> | 🏁 **FULL RUN** | avant commit / push ou changements structurels | `python -m pytest` |

---

## 5. Ce qui reste à faire (feuille de route)

| Priorité | Tâche | Statut |
|---|---|---|
| P0 | **Sentry** — traces de pile réelles + alertes prod | ⬜ (choix : à ajouter le jour de la mise en ligne, projet solo) |
| — | **CI/CD** | ⬜ volontairement ignoré (projet solo, pas de déploiement encore) |
| P1 | **Modération des signalements** (rôle modérateur, file d'approbation, détection de doublons, vote « moi aussi ») | ⬜ |
| P1 | **Notifications de pannes** (abonnement par région : Telegram / e-mail / push) | ⬜ |
| P1 | **Profils utilisateurs** et historique multi-appareils | ⬜ |
| P1 | **Ingestion programmée** du corpus (cron / job conteneurisé, ré-embedding des seuls documents modifiés) | ⬜ |
| P1 | **Quotas d'utilisation** par utilisateur (plafonds journaliers configurables depuis l'admin) | ⬜ |
| P2 | Cache Redis, CDN, tests de charge (k6/locust) | ⬜ |
| P2 | Landing page + documentation (fr/ar), conformité RGPD, analytique privée, revue de sécurité (pip-audit, npm audit), runbook | ⬜ |

---

## 6. Comment lancer le projet

**Option 1 — Docker (recommandé)**

```bash
cp .env.example .env    # remplir NGROK_AUTHTOKEN, clés API
docker compose up --build
# Frontend : http://localhost   Backend : http://localhost:8000
```

**Option 2 — Développement local (Windows)**

```bash
start_dev.bat   # démarre Postgres (cible :5433), applique les migrations
                # Alembic puis lance le backend
cd frontend && npm run dev    # http://localhost:5173
```

---

## 7. Chiffres clés

- **Tests backend** : 134 (128 rapides) · **Tests frontend** : 108
- **Corpus documentaire** : 73 PDF (secteur énergétique tunisien) découpés en **11 826 chunks** indexés dans ChromaDB
- **Qualité RAG** : recall@5 **0,83 → 1,00** ; MRR **0,700 → 1,000**
- **Modèles de base de données** : 5 (users, conversations, messages, settings, outage_reports) — 1 migration Alembic initiale
- **Git** : 11 commits sur `main` (3 locaux non poussés au moment du rapport)

---

*Fin du compte rendu.*
