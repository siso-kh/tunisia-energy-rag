"""Seed the database with realistic demo data for the Tunisia Energy RAG app.

Populates the four models from ``src.database.models``:

* **users** — a handful of demo users
* **conversations / messages** — chat exchanges about the Tunisian energy
  sector (renewables, STEG, ANME, self-consumption, subsidies...), where
  assistant messages carry the ``sources`` JSON metadata like the real
  RAG pipeline produces
* **outage_reports** — crowdsourced outages across Tunisian governorates
  with real-ish coordinates, mixing STEG / SONEDE and all three statuses

The script is **idempotent**: if users already exist it skips unless
``--reset`` is passed (which drops and recreates all tables).

Usage:
    python -m src.database.seed                  # seed (skip if already seeded)
    python -m src.database.seed --reset          # drop tables and reseed
    python -m src.database.seed --url sqlite+aiosqlite:///./dev.db
"""

import argparse
import asyncio
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select, text

from src.database.connection import build_engine, build_session_factory
from src.database.models import (
    Base,
    Conversation,
    Message,
    OutageReport,
    ReportStatus,
    User,
    UtilityType,
)
from src.database.schema import ensure_schema

# ---------------------------------------------------------------------------
# Demo data
# ---------------------------------------------------------------------------

# (region, latitude, longitude) for Tunisian governorates
_TUNISIA_COORDS = {
    "Tunis": (36.8065, 10.1815),
    "Ariana": (36.8665, 10.1936),
    "Ben Arous": (36.7531, 10.2222),
    "Manouba": (36.8078, 10.1009),
    "Nabeul": (36.4564, 10.7356),
    "Zaghouan": (36.4029, 10.1434),
    "Bizerte": (37.2744, 9.8739),
    "Béja": (36.7256, 9.1817),
    "Jendouba": (36.5011, 8.7803),
    "Le Kef": (36.1742, 8.7049),
    "Siliana": (36.0849, 9.3744),
    "Sousse": (35.8256, 10.6083),
    "Monastir": (35.7833, 10.8333),
    "Mahdia": (35.5047, 11.0622),
    "Kairouan": (35.6781, 10.0963),
    "Kasserine": (35.1676, 8.8365),
    "Sidi Bouzid": (35.0382, 9.4858),
    "Sfax": (34.7406, 10.7603),
    "Gabès": (33.8886, 10.0982),
    "Médenine": (33.3548, 10.5083),
    "Tataouine": (32.9295, 10.4524),
    "Gafsa": (34.4250, 8.7842),
    "Tozeur": (33.9197, 8.1335),
    "Kébili": (33.7053, 8.9698),
}

# Conversations: (title, [(role, content, sources_or_None), ...])
_CONVERSATIONS: List[Dict[str, Any]] = [
    {
        "title": "Énergies renouvelables en Tunisie",
        "messages": [
            (
                "user",
                "Quel est l'objectif de la Tunisie en matière d'énergies renouvelables à l'horizon 2030 ?",
                None,
            ),
            (
                "assistant",
                "La Tunisie vise 35 % d'énergies renouvelables dans le mix électrique à l'horizon 2030, "
                "conformément à la stratégie nationale pilotée par l'ANME et le ministère de l'Industrie. "
                "Le solaire photovoltaïque et l'éolien sont les deux filières prioritaires.",
                [{"source_file": "anme_strategie_2030.pdf", "page": 3}],
            ),
            (
                "user",
                "Quels projets solaires sont en cours de développement ?",
                None,
            ),
            (
                "assistant",
                "Plusieurs projets de centrales solaires photovoltaïques sont en cours (Tozeur, Kairouan, "
                "Gafsa) dans le cadre des appels d'offres de la STEG, ainsi que des projets "
                "d'autoconsommation pour les industriels et les ménages.",
                [{"source_file": "steg_solar_projects.pdf", "page": 7}],
            ),
        ],
    },
    {
        "title": "Aides et subventions ANME",
        "messages": [
            (
                "user",
                "Quelles subventions l'ANME offre-t-elle pour l'installation de panneaux solaires ?",
                None,
            ),
            (
                "assistant",
                "L'ANME subventionne une partie du coût d'installation des chauffe-eau solaires et des "
                "systèmes photovoltaïques pour les particuliers, les entreprises et les établissements "
                "publics, via les programmes PROSOL et le Fonds de transition énergétique.",
                [{"source_file": "anme_prosol_guide.pdf", "page": 12}],
            ),
            (
                "user",
                "Quelles sont les conditions d'éligibilité ?",
                None,
            ),
            (
                "assistant",
                "L'éligibilité dépend du type de bénéficiaire (ménage, PME, collectivité), de la conformité "
                "de l'installation aux normes techniques et de l'agrément de l'installateur. Les dossiers "
                "se déposent auprès de l'ANME ou de ses antennes régionales.",
                [{"source_file": "anme_prosol_guide.pdf", "page": 15}],
            ),
        ],
    },
    {
        "title": "Autoconsommation et raccordement",
        "messages": [
            (
                "user",
                "Comment fonctionne le régime d'autoconsommation en Tunisie ?",
                None,
            ),
            (
                "assistant",
                "Le régime d'autoconsommation permet de produire sa propre électricité (photovoltaïque le "
                "plus souvent) et de consommer directement cette production. L'excédent peut être injecté "
                "dans le réseau selon le contrat signé avec la STEG.",
                [{"source_file": "regime_autoconsommation_2023.pdf", "page": 5}],
            ),
            (
                "user",
                "Quelles sont les démarches de raccordement au réseau basse tension ?",
                None,
            ),
            (
                "assistant",
                "Le raccordement passe par une demande auprès de la STEG, une étude de faisabilité, puis "
                "l'installation du compteur bidirectionnel. Les installations doivent respecter les normes "
                "techniques de raccordement en vigueur.",
                [{"source_file": "steg_raccordement_bt.pdf", "page": 22}],
            ),
        ],
    },
    {
        "title": "Efficacité énergétique dans le bâtiment",
        "messages": [
            (
                "user",
                "Quelles mesures d'efficacité énergétique recommande-t-on pour les bâtiments en Tunisie ?",
                None,
            ),
            (
                "assistant",
                "L'isolation thermique de l'enveloppe, l'éclairage LED, la climatisation performante et "
                "l'audit énergétique sont les leviers principaux. La réglementation thermique des bâtiments "
                "neufs (RT 2014) impose des exigences minimales de performance énergétique.",
                [{"source_file": "anme_batiment_rt2014.pdf", "page": 9}],
            ),
        ],
    },
    {
        "title": "Rôle de la STEG et de l'ANME",
        "messages": [
            (
                "user",
                "Quel est le rôle respectif de la STEG et de l'ANME ?",
                None,
            ),
            (
                "assistant",
                "La STEG (Société tunisienne de l'électricité et du gaz) gère la production, le transport "
                "et la distribution de l'électricité et du gaz. L'ANME (Agence nationale pour la maîtrise "
                "de l'énergie) pilote les programmes d'efficacité énergétique et d'énergies renouvelables.",
                [{"source_file": "systeme_energetique_tunisie.pdf", "page": 2}],
            ),
        ],
    },
]

# Outage reports: (utility, region, status, description)
_OUTAGE_REPORTS: List[Dict[str, Any]] = [
    {
        "utility": UtilityType.STEG,
        "region": "Tunis",
        "status": ReportStatus.PENDING,
        "description": "Coupure de courant dans le quartier Lafayette suite à un incident sur le transformateur.",
    },
    {
        "utility": UtilityType.STEG,
        "region": "Sousse",
        "status": ReportStatus.VERIFIED,
        "description": "Panne électrique confirmée avenue Habib Bourguiba, équipe STEG dépêchée sur place.",
    },
    {
        "utility": UtilityType.SONEDE,
        "region": "Sfax",
        "status": ReportStatus.PENDING,
        "description": "Baisse de pression de l'eau potable dans le secteur de la zone industrielle.",
    },
    {
        "utility": UtilityType.STEG,
        "region": "Bizerte",
        "status": ReportStatus.RESOLVED,
        "description": "Coupure rétablie après remplacement du disjoncteur du poste HT.",
    },
    {
        "utility": UtilityType.SONEDE,
        "region": "Nabeul",
        "status": ReportStatus.VERIFIED,
        "description": "Fuite d'eau signalée sur la conduite principale de Hammamet, intervention planifiée.",
    },
    {
        "utility": UtilityType.STEG,
        "region": "Kairouan",
        "status": ReportStatus.PENDING,
        "description": "Microcoupures répétées en soirée, probablement dues à la surcharge estivale.",
    },
    {
        "utility": UtilityType.OTHER,
        "region": "Gabès",
        "status": ReportStatus.PENDING,
        "description": "Défaut d'éclairage public signalé sur la route de la zone industrielle.",
    },
    {
        "utility": UtilityType.STEG,
        "region": "Gafsa",
        "status": ReportStatus.RESOLVED,
        "description": "Panne du poste de distribution réparée, retour à la normale.",
    },
    {
        "utility": UtilityType.SONEDE,
        "region": "Tozeur",
        "status": ReportStatus.PENDING,
        "description": "Interruption de l'alimentation en eau dans la médina.",
    },
    {
        "utility": UtilityType.STEG,
        "region": "Monastir",
        "status": ReportStatus.VERIFIED,
        "description": "Coupure confirmée au centre-ville, travaux en cours.",
    },
    {
        "utility": UtilityType.STEG,
        "region": "Médenine",
        "status": ReportStatus.RESOLVED,
        "description": "Incident sur la ligne moyenne tension, alimentation rétablie.",
    },
    {
        "utility": UtilityType.OTHER,
        "region": "Kasserine",
        "status": ReportStatus.PENDING,
        "description": "Problème de réseau signalé dans le quartier El Alia.",
    },
]


# ---------------------------------------------------------------------------
# Seeding logic
# ---------------------------------------------------------------------------

def _make_console_users(n: int = 3) -> List[User]:
    """Create ``n`` anonymous demo users (the model has no name field)."""
    return [User() for _ in range(n)]


async def seed(
    database_url: Optional[str] = None,
    reset: bool = False,
    num_users: int = 3,
    verbose: bool = True,
) -> Dict[str, int]:
    """Create tables (if needed) and populate with demo data.

    Returns a summary dict of inserted row counts, e.g. ``{"users": 3,
    "conversations": 5, "messages": 11, "outage_reports": 12}``.

    Idempotent: if users already exist and ``reset`` is False, nothing is
    inserted. With ``reset=True`` all tables are dropped and recreated first.
    """
    # Guard against invalid user counts (avoid IndexError / ZeroDivisionError)
    num_users = max(1, int(num_users))

    engine = build_engine(database_url)
    factory = build_session_factory(engine)

    summary: Dict[str, int] = {
        "users": 0,
        "conversations": 0,
        "messages": 0,
        "outage_reports": 0,
    }

    if reset:
        async with engine.begin() as conn:
            if verbose:
                print("Dropping existing tables...")
            await conn.run_sync(Base.metadata.drop_all)
            # alembic_version belongs to Alembic, not the app metadata — drop it
            # too so a reset starts from a truly empty database and the next
            # ensure_schema re-runs all migrations.
            await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))

    # The schema is owned by Alembic migrations (alembic/versions) — never by
    # create_all. ensure_schema upgrades fresh DBs and stamps create_all-era
    # DBs that predate migrations.
    try:
        await asyncio.to_thread(ensure_schema, database_url)
    except Exception as e:
        if verbose:
            print(f"Warning: schema setup failed ({e}) — continuing anyway.")

    try:

        async with factory() as session:
            existing_users = await session.scalar(select(func.count()).select_from(User))
            if existing_users and not reset:
                if verbose:
                    print(
                        f"Database already contains {existing_users} user(s); "
                        "skipping. Use --reset to reseed from scratch."
                    )
                return summary

            users = _make_console_users(num_users)
            session.add_all(users)
            await session.flush()

            summary["users"] = len(users)

            # Rotate conversations across users so every demo user has chat history
            for conv_idx, conv in enumerate(_CONVERSATIONS):
                owner = users[conv_idx % len(users)]
                conversation = Conversation(user_id=owner.id, title=conv["title"])
                for role, content, sources in conv["messages"]:
                    conversation.messages.append(
                        Message(role=role, content=content, sources=sources)
                    )
                    summary["messages"] += 1
                session.add(conversation)
                summary["conversations"] += 1

            # Outage reports: rotate the reporting user, leave some anonymous
            for idx, report in enumerate(_OUTAGE_REPORTS):
                user_id = users[idx % len(users)].id if idx % 3 else None
                session.add(
                    OutageReport(
                        user_id=user_id,
                        utility=report["utility"],
                        region=report["region"],
                        latitude=_TUNISIA_COORDS[report["region"]][0],
                        longitude=_TUNISIA_COORDS[report["region"]][1],
                        description=report["description"],
                        status=report["status"],
                    )
                )
                summary["outage_reports"] += 1

            await session.commit()

        if verbose:
            print(
                f"Seeded: {summary['users']} users, {summary['conversations']} conversations, "
                f"{summary['messages']} messages, {summary['outage_reports']} outage reports."
            )
        return summary

    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed the Tunisia Energy RAG database with demo data."
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop all tables and reseed from scratch.",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="Database URL override (defaults to DATABASE_URL env or the "
        "postgresql+asyncpg localhost default).",
    )
    parser.add_argument(
        "--users",
        type=int,
        default=3,
        help="Number of demo users to create (default: 3).",
    )
    args = parser.parse_args()

    asyncio.run(
        seed(database_url=args.url, reset=args.reset, num_users=args.users)
    )


if __name__ == "__main__":
    main()
