"""Reclassifica os posts já salvos com o modelo configurado.

Use depois de trocar o modelo (``BERT_MODEL_NAME`` no ``.env`` ou ``--modelo``):
os posts guardam o sentimento do modelo antigo e, sem reclassificar, o banco
mistura dois modelos nas mesmas métricas.

Por padrão só SIMULA e mostra o antes e o depois. Com ``--aplicar`` faz uma
cópia de segurança do banco SQLite e grava os novos rótulos.

Uso
---
    python tools/reclassificar.py                       # simula
    python tools/reclassificar.py --aplicar             # copia o banco e grava
    python tools/reclassificar.py --modelo ElKulako/cryptobert --aplicar
"""

import argparse
import os
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
parser.add_argument("--modelo", help="substitui BERT_MODEL_NAME nesta execução")
parser.add_argument("--aplicar", action="store_true", help="grava no banco")
args = parser.parse_args()
if args.modelo:
    os.environ["BERT_MODEL_NAME"] = args.modelo  # antes de importar a config

from config import BERT_MODEL_NAME, DATABASE_URL  # noqa: E402
from db import SessionLocal  # noqa: E402
from models import SocialPost  # noqa: E402
from services import sentimento as svc_sentimento  # noqa: E402


def distribuicao(rotulos: list[str]) -> str:
    c, n = Counter(rotulos), max(len(rotulos), 1)
    return "  ".join(f"{k} {c[k]} ({c[k] / n:.0%})"
                     for k in ("positivo", "neutro", "negativo", "nulo"))


def main() -> None:
    if not svc_sentimento.carregar_modelo():
        sys.exit("Não foi possível carregar o modelo.")

    db = SessionLocal()
    try:
        posts = db.query(SocialPost).all()
        antes = [p.sentimento for p in posts]
        novos = []
        for p in posts:
            r = svc_sentimento.classificar(p.texto or "")
            novos.append((r["sentimento"], r["score"]))

        depois = [s for s, _ in novos]
        mudaram = sum(a != d for a, d in zip(antes, depois))
        print(f"Modelo: {BERT_MODEL_NAME}")
        print(f"{len(posts)} posts, {mudaram} mudariam de rótulo")
        print(f"  antes:  {distribuicao(antes)}")
        print(f"  depois: {distribuicao(depois)}")

        if not args.aplicar:
            print("\nSimulação. Use --aplicar para gravar (com cópia do banco).")
            return

        if DATABASE_URL.startswith("sqlite:///"):
            origem = Path(DATABASE_URL.replace("sqlite:///", "", 1))
            if not origem.is_absolute():
                origem = Path(__file__).resolve().parent.parent / origem
            copia = origem.with_name(
                f"{origem.stem}.backup_{datetime.now():%Y%m%d_%H%M%S}{origem.suffix}"
            )
            shutil.copy2(origem, copia)
            print(f"Cópia de segurança: {copia}")

        for p, (sentimento, score) in zip(posts, novos):
            p.sentimento, p.score = sentimento, score
        db.commit()
        print("Banco atualizado.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
