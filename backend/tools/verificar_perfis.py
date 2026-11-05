"""Confere se os perfis de ``perfis_x.txt`` existem e são públicos no X.

Usa os cookies do X (twikit), uma consulta por perfil, com pausa entre elas
para não estourar o limite da conta. Não coleta tweets nem grava no banco.

Uso (a partir da pasta backend/):

    python tools/verificar_perfis.py              # todos, 8 s entre consultas
    python tools/verificar_perfis.py --pausa 5 saylor glassnode
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collectors.cookie_auth import COOKIES_PATH, cookies_validos
from tools.coletor_continuo import carregar_perfis


async def verificar(perfis: list[str], pausa: float) -> list[tuple[str, str, str]]:
    from twikit import Client

    client = Client("en-US")
    client.load_cookies(str(COOKIES_PATH))

    resultados = []
    for i, nome in enumerate(perfis, 1):
        try:
            user = await client.get_user_by_screen_name(nome)
            if getattr(user, "protected", False):
                estado, detalhe = "PROTEGIDO", "conta privada: sem tweets públicos"
            else:
                estado = "ok"
                detalhe = (f"{user.name} · {user.followers_count:,} seguidores · "
                           f"{user.statuses_count:,} posts")
        except Exception as e:
            texto = str(e)
            if "429" in texto or "Rate limit" in texto:
                print("Limite do X atingido; aguarde 15 min e rode de novo.")
                break
            estado, detalhe = "FALHOU", texto[:90] or type(e).__name__
        resultados.append((nome, estado, detalhe))
        print(f"[{i:3}/{len(perfis)}] {estado:9} @{nome:18} {detalhe}", flush=True)
        if i < len(perfis):
            time.sleep(pausa)
    return resultados


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("perfis", nargs="*", help="padrão: todos de perfis_x.txt")
    parser.add_argument("--pausa", type=float, default=8.0,
                        help="segundos entre consultas (padrão: 8)")
    args = parser.parse_args()

    if not cookies_validos():
        sys.exit("Cookies do X ausentes. Faça o login do X pelo dashboard antes.")

    perfis = [p.lstrip("@") for p in args.perfis] or carregar_perfis()
    resultados = asyncio.run(verificar(perfis, args.pausa))

    problemas = [r for r in resultados if r[1] != "ok"]
    print(f"\n{len(resultados) - len(problemas)} ok, {len(problemas)} com problema.")
    for nome, estado, detalhe in problemas:
        print(f"  @{nome}: {estado} — {detalhe}")


if __name__ == "__main__":
    main()
