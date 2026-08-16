"""Conexão com o banco de dados (SQLAlchemy + SQLite)."""

from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from config import DATABASE_URL

_e_sqlite = DATABASE_URL.startswith("sqlite")

engine = create_engine(
    DATABASE_URL,
    # check_same_thread=False é necessário porque o FastAPI atende requisições
    # em threads diferentes do pool.
    connect_args={"check_same_thread": False} if _e_sqlite else {},
    pool_pre_ping=True,
)


if _e_sqlite:

    @event.listens_for(engine, "connect")
    def _configurar_sqlite(dbapi_connection, _connection_record):
        """Ajustes de robustez do SQLite.

        - ``foreign_keys``: o SQLite ignora FKs por padrão.
        - ``journal_mode=WAL``: permite leitura concorrente enquanto a coleta
          escreve, evitando erros de "database is locked" no dashboard.
        - ``busy_timeout``: espera em vez de falhar quando há escrita simultânea.
        """
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Dependência do FastAPI: abre uma sessão por requisição e fecha no fim."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
