"""Simulador de investimento — backtest de compra/venda com dinheiro fictício.

Dois modos, escolhidos pelo usuário:

**FOMO** — cruza o sentimento dos posts já coletados (de perfis do X
escolhidos pelo usuário) com os candles horários da Binance: só compra
quando há hype positivo *e* o preço caiu. Não roda coleta ao vivo: os
coletores do X só trazem os tweets mais recentes (não há busca histórica
por data sem API paga), então usar dados já salvos em ``social_posts`` é o
único jeito de simular um período passado de verdade — por isso este modo
exige que os posts já tenham sido coletados antes.

**Flat** — ignora completamente o sentimento; compra e vende só pela
variação percentual do preço. Não depende de posts nem de perfis do X, e
por isso funciona em qualquer período coberto pela Binance (até
``MAX_DIAS_SIMULACAO`` dias), mesmo sem nenhuma coleta prévia.

Regra de decisão (documentada aqui porque é o coração da feature)
-------------------------------------------------------------------
Estado: ``caixa`` (começa em ``capital_inicial``), ``pico`` (maior preço
observado desde a última compra) e uma lista de posições abertas — o
modelo é "grid": cada sinal de compra abre uma posição independente, com
seu próprio alvo de venda, em vez de um saldo único all-in/all-out.

Para cada hora, em ordem cronológica:
  1. Vende qualquer posição que atingiu a meta de lucro **ou** o limite de
     perda aceita (stop-loss) — o que vier primeiro.
  2. Atualiza o pico (maior preço visto desde a última compra).
  3. Compra uma posição nova se o preço caiu o suficiente a partir do pico
     **e** há caixa disponível — no modo FOMO, exige também que o
     sentimento da hora seja positivo. Reseta o pico para o preço da
     compra, o que exige uma nova perna de queda antes da próxima compra
     (sem isso, compraria em toda hora abaixo do pico antigo).

Ao final, posições ainda abertas são marcadas a mercado (preço da última
hora) como lucro não realizado, separado do lucro realizado das vendas.
Um benchmark "buy & hold" (comprar tudo no primeiro preço, manter até o
fim) dá contexto ao resultado — no mesmo espírito de honestidade
estatística do resto do projeto (ver ``services/correlacao.py``).
"""

import logging
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import SocialPost
from services import correlacao as svc_correlacao
from services import mercado as svc_mercado
from services.sentimento import NULO, POSITIVO
from utils.tempo import para_iso_utc

logger = logging.getLogger("sentcrypto.simulacao")

# Mesmo limite de profundidade usado em services/correlacao.py — a Binance
# não sustenta janelas maiores num orçamento razoável de requisições.
MAX_DIAS_SIMULACAO = 180


def _normalizar_perfis(perfis: list[str]) -> list[str]:
    """``@perfil`` em minúsculas, do jeito que dá pra comparar com o banco."""
    vistos: list[str] = []
    for p in perfis:
        nome = (p or "").strip().lstrip("@").lower()
        if nome and nome not in vistos:
            vistos.append(nome)
    return vistos


def _resultado_vazio(
    moeda: str, perfis: list[str], inicio: datetime, fim: datetime,
    parametros: dict, mensagem: str,
) -> dict:
    return {
        "moeda": moeda,
        "perfis": perfis,
        "data_inicio": para_iso_utc(inicio),
        "data_fim": para_iso_utc(fim),
        "parametros": parametros,
        "vazio": True,
        "mensagem": mensagem,
        "resumo": None,
        "trades": [],
        "serie_patrimonio": [],
        "posicoes_abertas": [],
    }


MODOS_VALIDOS = ("fomo", "flat")


def simular_investimento(
    db: Session,
    moeda: str,
    perfis: list[str],
    inicio: datetime,
    fim: datetime,
    capital_inicial: float,
    valor_por_compra: float,
    percentual_lucro_venda: float,
    percentual_queda_compra: float,
    percentual_perda_aceita: float,
    modo: str = "fomo",
) -> dict:
    """Roda o backtest e devolve trades, série de patrimônio e resumo.

    ``modo="fomo"`` (padrão) exige posts já coletados dos ``perfis`` e só
    compra com sentimento positivo. ``modo="flat"`` ignora sentimento e
    ``perfis`` por completo — compra e vende só pela variação do preço.
    """
    moeda_u = moeda.upper()
    modo = modo if modo in MODOS_VALIDOS else "fomo"
    parametros = {
        "modo": modo,
        "capital_inicial": capital_inicial,
        "valor_por_compra": valor_por_compra,
        "percentual_lucro_venda": percentual_lucro_venda,
        "percentual_queda_compra": percentual_queda_compra,
        "percentual_perda_aceita": percentual_perda_aceita,
    }

    agrupado: dict = {}

    if modo == "fomo":
        perfis_norm = _normalizar_perfis(perfis)
        if not perfis_norm:
            return _resultado_vazio(
                moeda_u, perfis, inicio, fim, parametros,
                "Informe ao menos um perfil do X.",
            )

        posts = (
            db.query(SocialPost)
            .filter(
                SocialPost.moeda == moeda_u,
                SocialPost.fonte == "X",
                SocialPost.sentimento != NULO,
                func.lower(SocialPost.autor).in_([f"@{p}" for p in perfis_norm]),
                SocialPost.timestamp_post >= inicio,
                SocialPost.timestamp_post <= fim,
            )
            .order_by(SocialPost.timestamp_post.asc())
            .all()
        )

        if not posts:
            return _resultado_vazio(
                moeda_u, perfis, inicio, fim, parametros,
                "Nenhum post desses perfis foi encontrado no período. Colete "
                "com 'Analisar e salvar' antes de simular.",
            )

        agrupado = svc_correlacao.agregar_posts_por_hora(posts)

    try:
        candles = svc_mercado.buscar_klines_intervalo(
            svc_mercado.simbolo_para(moeda_u), inicio=inicio, fim=fim,
            intervalo="1h",
        )
    except svc_mercado.ErroBinance as e:
        # Mesmo padrão defensivo de correlacao.precos_para_horas: uma falha
        # de rede vira resultado vazio, não um 502 que derruba a tela.
        logger.warning("Sem candles para simular %s: %s", moeda_u, e)
        candles = []

    if not candles:
        return _resultado_vazio(
            moeda_u, perfis, inicio, fim, parametros,
            "Não foi possível obter preços da Binance para esse período.",
        )

    return _rodar_backtest(
        moeda_u, perfis, candles, agrupado, capital_inicial,
        valor_por_compra, percentual_lucro_venda, percentual_queda_compra,
        percentual_perda_aceita, modo,
    )


def _rodar_backtest(
    moeda: str,
    perfis: list[str],
    candles: list,
    agrupado: dict,
    capital_inicial: float,
    valor_por_compra: float,
    percentual_lucro_venda: float,
    percentual_queda_compra: float,
    percentual_perda_aceita: float,
    modo: str = "fomo",
) -> dict:
    caixa = capital_inicial
    pico: float | None = None
    posicoes: list[dict] = []
    trades: list[dict] = []
    serie_patrimonio: list[dict] = []
    proximo_id = 1

    fator_lucro = 1 + percentual_lucro_venda / 100
    fator_queda = 1 - percentual_queda_compra / 100
    # "Assumir perda": abaixo desse preço a posição é vendida mesmo sem
    # atingir a meta de lucro — limita o prejuízo em vez de deixar a
    # posição aberta esperando uma recuperação que pode não vir.
    fator_perda = 1 - percentual_perda_aceita / 100

    for candle in candles:
        preco = candle.fechamento
        # Chave naive (sem fuso) para casar com `agrupado`, que usa a mesma
        # convenção interna (ver correlacao.agregar_posts_por_hora). Tudo
        # que sai para o frontend usa `hora_out`, com UTC explícito — sem
        # isso o navegador interpreta a hora como fuso local (ver
        # utils/tempo.py::para_iso_utc).
        hora_iso = candle.timestamp.isoformat()
        hora_out = para_iso_utc(candle.timestamp)

        # 1) Vende posições que bateram a meta de lucro OU o limite de
        #    perda aceita (stop-loss) — o que vier primeiro.
        ainda_abertas = []
        for pos in posicoes:
            alvo_lucro = pos["preco_entrada"] * fator_lucro
            alvo_perda = pos["preco_entrada"] * fator_perda
            motivo = None
            if preco >= alvo_lucro:
                motivo = "lucro"
            elif preco <= alvo_perda:
                motivo = "perda"

            if motivo:
                valor_venda = pos["qtd"] * preco
                lucro = valor_venda - pos["valor_investido"]
                caixa += valor_venda
                trades.append(
                    {
                        "tipo": "venda",
                        "motivo": motivo,
                        "posicao_id": pos["id"],
                        "timestamp": hora_out,
                        "preco": round(preco, 2),
                        "qtd": pos["qtd"],
                        "valor": round(valor_venda, 2),
                        "preco_entrada": round(pos["preco_entrada"], 2),
                        "lucro": round(lucro, 2),
                        "lucro_pct": round(
                            lucro / pos["valor_investido"] * 100, 2
                        ),
                    }
                )
            else:
                ainda_abertas.append(pos)
        posicoes = ainda_abertas

        # 2) Atualiza o pico de referência para a próxima queda.
        pico = preco if pico is None else max(pico, preco)

        # 3) Compra se a queda foi suficiente + caixa disponível — no modo
        #    FOMO, exige também sentimento positivo na hora; no modo Flat,
        #    a queda de preço sozinha já é o sinal de compra.
        grupo = agrupado.get(hora_iso)
        sentimento_positivo = grupo is not None and grupo.direcao == POSITIVO
        queda_suficiente = pico is not None and preco <= pico * fator_queda
        sinal_compra = queda_suficiente and (
            sentimento_positivo if modo == "fomo" else True
        )

        if sinal_compra and caixa >= valor_por_compra:
            qtd = valor_por_compra / preco
            caixa -= valor_por_compra
            posicoes.append(
                {
                    "id": proximo_id,
                    "preco_entrada": preco,
                    "qtd": qtd,
                    "valor_investido": valor_por_compra,
                    "timestamp": hora_out,
                }
            )
            trades.append(
                {
                    "tipo": "compra",
                    "posicao_id": proximo_id,
                    "timestamp": hora_out,
                    "preco": round(preco, 2),
                    "qtd": qtd,
                    "valor": round(valor_por_compra, 2),
                    "sentiment_score": grupo.sentiment_score if grupo else None,
                }
            )
            proximo_id += 1
            # Exige uma nova perna de queda a partir daqui antes da próxima
            # compra — sem isso, compraria em toda hora abaixo do pico antigo.
            pico = preco

        valor_posicoes = sum(p["qtd"] * preco for p in posicoes)
        serie_patrimonio.append(
            {
                "timestamp": hora_out,
                "preco": round(preco, 2),
                "caixa": round(caixa, 2),
                "valor_posicoes": round(valor_posicoes, 2),
                "patrimonio": round(caixa + valor_posicoes, 2),
                "posicoes_abertas": len(posicoes),
                "sentiment_score": grupo.sentiment_score if grupo else None,
            }
        )

    preco_final = candles[-1].fechamento
    preco_inicial = candles[0].fechamento

    valor_investido_aberto = sum(p["valor_investido"] for p in posicoes)
    valor_atual_aberto = sum(p["qtd"] * preco_final for p in posicoes)
    lucro_nao_realizado = valor_atual_aberto - valor_investido_aberto

    vendas = [t for t in trades if t["tipo"] == "venda"]
    compras = [t for t in trades if t["tipo"] == "compra"]
    lucro_realizado = sum(t["lucro"] for t in vendas)

    # Taxa de acerto = fração das vendas que fecharam com lucro. Só faz
    # sentido com pelo menos uma venda — posições ainda abertas não contam
    # (o resultado delas não está decidido).
    vendas_lucrativas = sum(1 for t in vendas if t["lucro"] > 0)
    taxa_acerto_pct = (
        round(vendas_lucrativas / len(vendas) * 100, 1) if vendas else None
    )
    vendas_por_stop_loss = sum(1 for t in vendas if t["motivo"] == "perda")

    patrimonio_final = caixa + valor_atual_aberto
    lucro_total = patrimonio_final - capital_inicial
    lucro_total_pct = (
        round(lucro_total / capital_inicial * 100, 2) if capital_inicial else 0.0
    )

    qtd_bh = capital_inicial / preco_inicial if preco_inicial else 0.0
    patrimonio_bh = qtd_bh * preco_final
    lucro_bh_pct = (
        round((patrimonio_bh - capital_inicial) / capital_inicial * 100, 2)
        if capital_inicial
        else 0.0
    )

    resumo = {
        "total_horas_simuladas": len(candles),
        "total_horas_com_posts": len(agrupado),
        "total_compras": len(compras),
        "total_vendas": len(vendas),
        "vendas_lucrativas": vendas_lucrativas,
        "vendas_por_stop_loss": vendas_por_stop_loss,
        "taxa_acerto_pct": taxa_acerto_pct,
        "posicoes_abertas_final": len(posicoes),
        "caixa_final": round(caixa, 2),
        "valor_posicoes_abertas": round(valor_atual_aberto, 2),
        "patrimonio_final": round(patrimonio_final, 2),
        "lucro_realizado": round(lucro_realizado, 2),
        "lucro_nao_realizado": round(lucro_nao_realizado, 2),
        "lucro_total": round(lucro_total, 2),
        "lucro_total_pct": lucro_total_pct,
        "buy_and_hold": {
            "preco_inicial": round(preco_inicial, 2),
            "preco_final": round(preco_final, 2),
            "patrimonio_final": round(patrimonio_bh, 2),
            "lucro_pct": lucro_bh_pct,
        },
        "superou_buy_and_hold": lucro_total_pct > lucro_bh_pct,
    }

    posicoes_abertas = [
        {
            "id": p["id"],
            "preco_entrada": round(p["preco_entrada"], 2),
            "qtd": p["qtd"],
            "valor_investido": round(p["valor_investido"], 2),
            "valor_atual": round(p["qtd"] * preco_final, 2),
            "timestamp_compra": p["timestamp"],
        }
        for p in posicoes
    ]

    return {
        "moeda": moeda,
        "perfis": perfis,
        "data_inicio": para_iso_utc(candles[0].timestamp),
        "data_fim": para_iso_utc(candles[-1].timestamp),
        "parametros": {
            "modo": modo,
            "capital_inicial": capital_inicial,
            "valor_por_compra": valor_por_compra,
            "percentual_lucro_venda": percentual_lucro_venda,
            "percentual_queda_compra": percentual_queda_compra,
            "percentual_perda_aceita": percentual_perda_aceita,
        },
        "vazio": False,
        "mensagem": None,
        "resumo": resumo,
        "trades": trades,
        "serie_patrimonio": serie_patrimonio,
        "posicoes_abertas": posicoes_abertas,
    }
