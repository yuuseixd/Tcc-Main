from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Arquivo SQLite ficará na própria pasta backend
DATABASE_URL = "sqlite:///./dados.db"

# check_same_thread=False é necessário para usar SQLite com FastAPI
engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()
