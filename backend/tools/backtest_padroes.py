"""Backtest dos padrões gráficos no histórico da Binance.

Para cada intervalo, detecta todos os padrões de cada moeda, acompanha o que
aconteceu depois do rompimento e compara a taxa de acerto (alvo antes do
stop) com uma entrada qualquer que tivesse o mesmo alvo e o mesmo stop.
O resultado vai para ``backtest_padroes.json``, que o Analisador exibe no
ranking e na "confiabilidade medida" de cada padrão.

Uso (a partir da pasta backend/):

    python tools/backtest_padroes.py                       # 8 moedas, 4h/12h/1d/1w
    python tools/backtest_padroes.py --moedas BTC ETH --intervalos 4h
"""

import argparse
import json
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import BASE_DIR, MOEDAS_SUPORTADAS
from services import mercado as svc_mercado
from services import padroes as svc_padroes
from utils.tempo import agora_utc

ARQUIVO = BASE_DIR / "backtest_padroes.json"

HORAS_POR_CANDLE = {"4h": 4, "12h": 12, "1d": 24, "1w": 168}
# Histórico por intervalo: ~2,7 anos em 4h, ~5,5 em 12h, ~8 no diário e todo
# o histórico da Binance no semanal (BTC desde 2017: ~480 semanas).
CANDLES_POR_INTERVALO = {"4h": 6000, "12h": 4000, "1d": 3000, "1w": 1000}


def _json(resumo: dict) -> dict:
    return {k: list(v) if isinstance(v, tuple) else v for k, v in resumo.items()}


def rodar(moedas: list[str], intervalos: list[str]) -> dict:
    p = svc_padroes
    resultado = {
        "gerado_em": agora_utc().isoformat() + "Z",
        "moedas": moedas,
        "parametros": {
            "fatores_zigzag": list(p.FATORES_ZIGZAG), "tol_nivel": p.TOL_NIVEL,
            "cabeca_destaque": p.CABECA_DESTAQUE, "simetria_ombros": p.SIMETRIA_OMBROS,
            "simetria_tempo": p.SIMETRIA_TEMPO, "inclinacao_triangulo": p.INCLINACAO_TRIANGULO,
            "retracao_flamula": p.RETRACAO_FLAMULA, "duracao_flamula": p.DURACAO_FLAMULA,
            "janela_rompimento": p.JANELA_ROMPIMENTO, "janela_desfecho": p.JANELA_DESFECHO,
            "amostras_baseline": p.AMOSTRAS_BASELINE,
        },
        "intervalos": {},
    }

    for intervalo in intervalos:
        horas = HORAS_POR_CANDLE[intervalo] * CANDLES_POR_INTERVALO[intervalo]
        por_tipo: dict[str, list[dict]] = {t: [] for t in p.TIPOS}
        por_moeda_tipo: dict[str, dict[str, list[dict]]] = {}
        periodo: dict[str, str] = {}

        for moeda in moedas:
            t0 = time.time()
            fim = agora_utc()
            candles = svc_mercado.buscar_klines_intervalo(
                svc_mercado.simbolo_para(moeda), fim - timedelta(hours=horas), fim,
                intervalo=intervalo, max_requisicoes=10,
            )
            if not candles:
                print(f"  {moeda} {intervalo}: sem candles")
                continue
            padroes = p.detectar(candles)
            p.anotar_baseline(candles, padroes)
            por_moeda_tipo[moeda] = {t: [] for t in p.TIPOS}
            for padrao in padroes:
                por_tipo[padrao["tipo"]].append(padrao)
                por_moeda_tipo[moeda][padrao["tipo"]].append(padrao)
            periodo[moeda] = f"{candles[0].timestamp:%Y-%m-%d} a {candles[-1].timestamp:%Y-%m-%d}"
            print(f"  {moeda} {intervalo}: {len(candles)} candles, "
                  f"{len(padroes)} padrões ({time.time() - t0:.0f}s)", flush=True)

        resultado["intervalos"][intervalo] = {
            "tipos": {
                tipo: {
                    "geral": _json(p.resumir(lista)),
                    "por_moeda": {m: _json(p.resumir(por_moeda_tipo[m][tipo]))
                                  for m in por_moeda_tipo},
                }
                for tipo, lista in por_tipo.items()
            },
            "periodo": periodo,
        }
    return resultado


def _fmt(v, casas=1, sufixo="%"):
    return "—" if v is None else f"{v:.{casas}f}{sufixo}"


def tabela_markdown(resultado: dict) -> str:
    """Ranking por vantagem sobre a entrada qualquer, um bloco por intervalo."""
    blocos = []
    for intervalo, dados in resultado["intervalos"].items():
        linhas = [
            f"**{intervalo}**", "",
            "| Padrão | Avaliados | Acerto | IC 95% | Entrada qualquer | Vantagem | p-valor |",
            "|---|---:|---:|---|---:|---:|---:|",
        ]
        tipos = sorted(
            dados["tipos"].items(),
            key=lambda kv: (kv[1]["geral"]["vantagem_pp"] is None,
                            -(kv[1]["geral"]["vantagem_pp"] or 0)),
        )
        for tipo, r in tipos:
            g = r["geral"]
            ic = g["ic95_pct"]
            linhas.append(
                f"| {svc_padroes.TIPOS[tipo][0]} | {g['avaliados']} | "
                f"{_fmt(g['taxa_acerto_pct'])} | "
                f"{f'{ic[0]:.0f}–{ic[1]:.0f}%' if ic else '—'} | "
                f"{_fmt(g['baseline_pct'])} | {_fmt(g['vantagem_pp'], 1, ' pp')} | "
                f"{_fmt(g['p_valor'], 3, '')}{' *' if g['significativo'] else ''} |"
            )
        blocos.append("\n".join(linhas))
    return "\n\n".join(blocos)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--moedas", nargs="+", default=list(MOEDAS_SUPORTADAS))
    parser.add_argument("--intervalos", nargs="+", default=list(HORAS_POR_CANDLE),
                        choices=list(HORAS_POR_CANDLE))
    parser.add_argument("--saida", type=Path, default=ARQUIVO)
    args = parser.parse_args()

    moedas = [m.upper() for m in args.moedas]
    resultado = rodar(moedas, args.intervalos)
    args.saida.write_text(json.dumps(resultado, indent=1, ensure_ascii=False),
                          encoding="utf-8")
    print()
    print(tabela_markdown(resultado))
    print("\n* = significativo (p < 0,05 e acima da entrada qualquer)")
    print(f"Salvo em {args.saida}")


if __name__ == "__main__":
    main()
