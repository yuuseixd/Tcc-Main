"""Cria e migra o banco de dados do SentCrypto.

Rode este script sempre que atualizar o projeto:

    python setup_db.py

O que ele faz, de forma idempotente (pode rodar quantas vezes quiser):

1. Cria as tabelas que ainda não existem.
2. Adiciona as colunas novas de ``social_posts`` (external_id, url, autor).
3. Remove duplicatas herdadas das coletas antigas, que não tinham proteção
   contra reprocessar o mesmo post.
4. Cria os índices únicos que impedem novas duplicatas.
"""

import logging
import shutil
from datetime import datetime

from sqlalchemy import inspect, text

from config import DATABASE_URL
from db import Base, engine

# Importar os modelos registra as tabelas no metadata do SQLAlchemy.
from models import MarketPoint, SocialPost  # noqa: F401

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("setup_db")


COLUNAS_NOVAS_SOCIAL = {
    "external_id": "VARCHAR(120)",
    "url": "VARCHAR(500)",
    "autor": "VARCHAR(120)",
}


def fazer_backup() -> None:
    """Copia o arquivo do banco antes de alterá-lo."""
    if not DATABASE_URL.startswith("sqlite"):
        return

    caminho = DATABASE_URL.replace("sqlite:///", "")
    from pathlib import Path

    origem = Path(caminho)
    if not origem.exists():
        return

    destino = origem.with_name(
        f"{origem.stem}.backup_{datetime.now():%Y%m%d_%H%M%S}{origem.suffix}"
    )
    shutil.copy2(origem, destino)
    logger.info("  Backup criado: %s", destino.name)


def adicionar_colunas_faltantes(conn) -> None:
    """Acrescenta colunas novas sem perder os dados existentes."""
    inspector = inspect(conn)
    if "social_posts" not in inspector.get_table_names():
        return

    existentes = {c["name"] for c in inspector.get_columns("social_posts")}

    for coluna, tipo in COLUNAS_NOVAS_SOCIAL.items():
        if coluna in existentes:
            continue
        conn.execute(text(f"ALTER TABLE social_posts ADD COLUMN {coluna} {tipo}"))
        logger.info("  Coluna adicionada: social_posts.%s", coluna)


def remover_duplicatas_market(conn) -> int:
    """Mantém apenas um registro por (moeda, timestamp)."""
    resultado = conn.execute(
        text(
            """
            DELETE FROM market_points
            WHERE id NOT IN (
                SELECT MIN(id) FROM market_points GROUP BY moeda, timestamp
            )
            """
        )
    )
    return resultado.rowcount or 0


def remover_duplicatas_social(conn) -> int:
    """Remove posts repetidos das coletas antigas.

    Como os registros antigos não têm ``external_id``, a identidade do post é
    inferida pela combinação fonte + moeda + texto + horário de publicação.
    """
    resultado = conn.execute(
        text(
            """
            DELETE FROM social_posts
            WHERE id NOT IN (
                SELECT MIN(id) FROM social_posts
                GROUP BY fonte, moeda, texto, timestamp_post
            )
            """
        )
    )
    return resultado.rowcount or 0


def criar_indices_unicos(conn) -> None:
    """Cria os índices que impedem duplicatas daqui pra frente."""
    indices = {
        "uq_market_moeda_timestamp":
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_market_moeda_timestamp "
            "ON market_points (moeda, timestamp)",
        "uq_social_fonte_external":
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_social_fonte_external "
            "ON social_posts (fonte, external_id)",
        "ix_market_moeda_timestamp":
            "CREATE INDEX IF NOT EXISTS ix_market_moeda_timestamp "
            "ON market_points (moeda, timestamp)",
        "ix_social_consulta":
            "CREATE INDEX IF NOT EXISTS ix_social_consulta "
            "ON social_posts (moeda, fonte, timestamp_post)",
    }

    for nome, sql in indices.items():
        try:
            conn.execute(text(sql))
        except Exception as e:
            logger.warning("  Não foi possível criar %s: %s", nome, e)


def contar(conn, tabela: str) -> int:
    try:
        return conn.execute(text(f"SELECT COUNT(*) FROM {tabela}")).scalar() or 0
    except Exception:
        return 0


def main() -> None:
    logger.info("SentCrypto - preparacao do banco de dados")
    logger.info("Banco: %s\n", DATABASE_URL)

    logger.info("[1/5] Backup do banco atual...")
    fazer_backup()

    logger.info("[2/5] Criando tabelas ausentes...")
    Base.metadata.create_all(bind=engine)

    with engine.begin() as conn:
        antes_market = contar(conn, "market_points")
        antes_social = contar(conn, "social_posts")

        logger.info("[3/5] Atualizando estrutura das tabelas...")
        adicionar_colunas_faltantes(conn)

        logger.info("[4/5] Removendo duplicatas herdadas...")
        dup_market = remover_duplicatas_market(conn)
        dup_social = remover_duplicatas_social(conn)
        logger.info(
            "  market_points: %d duplicatas removidas", dup_market
        )
        logger.info(
            "  social_posts:  %d duplicatas removidas", dup_social
        )

        logger.info("[5/5] Criando índices únicos...")
        criar_indices_unicos(conn)

        depois_market = contar(conn, "market_points")
        depois_social = contar(conn, "social_posts")

    logger.info("\nBanco atualizado com sucesso.")
    logger.info(
        "  market_points: %d -> %d registros", antes_market, depois_market
    )
    logger.info(
        "  social_posts:  %d -> %d registros", antes_social, depois_social
    )


if __name__ == "__main__":
    main()
