from db import Base, engine 

print("🔧 Criando/atualizando tabelas no SQLite...")

# importa os modelos para registrar as tabelas
from models import MarketPoint, SocialPost

Base.metadata.create_all(bind=engine)

print("✅ Banco de dados atualizado com sucesso!")
