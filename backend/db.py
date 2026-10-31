"""Conexão com o banco de dados (SQLAlchemy + SQLite)."""

from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from config import CONTAS_DATABASE_URL, DATABASE_URL


def _criar_engine(url: str):
    e_sqlite = url.startswith("sqlite")
    novo = create_engine(
        url,
        # check_same_thread=False é necessário porque o FastAPI atende
        # requisições em threads diferentes do pool.
        connect_args={"check_same_thread": False} if e_sqlite else {},
        pool_pre_ping=True,
    )
    if e_sqlite:
        event.listen(novo, "connect", _configurar_sqlite)
    return novo


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


engine = _criar_engine(DATABASE_URL)
contas_engine = _criar_engine(CONTAS_DATABASE_URL)

Base = declarative_base()

# Tabelas que vivem no banco de contas (ver CONTAS_DATABASE_URL).
TABELAS_CONTAS = ("usuarios", "sessoes")


def _binds() -> dict:
    """Mapeia as tabelas de contas para o banco próprio; o resto usa ``engine``."""
    return {
        tabela: contas_engine
        for nome, tabela in Base.metadata.tables.items()
        if nome in TABELAS_CONTAS
    }


class _SessaoComContas(sessionmaker):
    """sessionmaker que resolve os binds na hora de abrir a sessão.

    Os modelos são importados depois deste módulo, então as tabelas ainda não
    existem em ``Base.metadata`` quando o sessionmaker é criado.
    """

    def __call__(self, **kw):
        kw.setdefault("binds", _binds())
        return super().__call__(**kw)


SessionLocal = _SessaoComContas(autocommit=False, autoflush=False, bind=engine)


def criar_tabelas() -> None:
    """Cria as tabelas ausentes, cada grupo no seu banco."""
    contas = [t for n, t in Base.metadata.tables.items() if n in TABELAS_CONTAS]
    dados = [t for n, t in Base.metadata.tables.items() if n not in TABELAS_CONTAS]
    Base.metadata.create_all(bind=engine, tables=dados)
    Base.metadata.create_all(bind=contas_engine, tables=contas)


def get_db():
    """Dependência do FastAPI: abre uma sessão por requisição e fecha no fim."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
