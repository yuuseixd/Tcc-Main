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

**Padrões** — entra no rompimento de um padrão gráfico escolhido (ver
``services/padroes.py``), com o alvo e o stop do próprio padrão; padrões de
baixa viram venda a descoberto simulada. Sai no alvo, no stop ou no fim do
horizonte do padrão.

Execução (no mesmo espírito do emulador de ordens do TradingView)
------------------------------------------------------------------
* O sinal é lido no **fechamento** de um candle e a ordem é executada na
  **abertura do candle seguinte** — comprar no mesmo fechamento que gerou o
  sinal usaria uma informação que só existe depois do candle fechar.
* Meta e stop são conferidos **dentro do candle**, pela máxima e pela mínima:
  um pavio que encosta no stop vende no stop, mesmo que o candle feche acima.
  Se o candle já abre além do nível (gap), executa na abertura. Se meta e stop
  caem no mesmo candle, conta o stop (não dá para saber a ordem; leitura
  conservadora, a mesma da análise de padrões).
* Cada compra e cada venda paga ``taxa_operacao_pct`` sobre o valor operado
  (padrão 0,1%, a taxa spot da Binance) e sofre ``slippage_pct`` no preço
  (executa um pouco pior que o preço de referência). O buy & hold paga a
  mesma taxa na entrada, para a comparação ser justa.

Ao final, posições ainda abertas são marcadas a mercado (preço da última
hora) como lucro não realizado, separado do lucro realizado das vendas.
Um benchmark "buy & hold" (comprar tudo no primeiro preço, manter até o
fim) dá contexto ao resultado — no mesmo espírito de honestidade
estatística do resto do projeto (ver ``services/correlacao.py``).
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import SocialPost
from services import correlacao as svc_correlacao
from services import mercado as svc_mercado
from services import padroes as svc_padroes
from services.sentimento import NULO, POSITIVO
from utils.tempo import para_iso_utc

logger = logging.getLogger("sentcrypto.simulacao")

# Mesmo limite de profundidade usado em services/correlacao.py — a Binance
# não sustenta janelas maiores num orçamento razoável de requisições.
MAX_DIAS_SIMULACAO = 180


def _normalizar_perfis(perfis: list[str], fonte: str = "X") -> list[str]:
    """Nomes em minúsculas, sem ``@`` (X) ou ``r/`` (Reddit), para comparar com o banco."""
    vistos: list[str] = []
    for p in perfis:
        nome = (p or "").strip().lower()
        nome = nome[2:] if fonte == "Reddit" and nome.startswith("r/") else nome
        nome = nome.lstrip("@")
        if nome and nome not in vistos:
            vistos.append(nome)
    return vistos


def _prefixo_autor(fonte: str) -> str:
    """Como o autor é gravado em ``social_posts``: ``@perfil`` ou ``r/sub``."""
    return "r/" if fonte == "Reddit" else "@"


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

# Taxa spot padrão da Binance (maker/taker sem desconto), em %.
TAXA_OPERACAO_PADRAO_PCT = 0.1


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
    fonte: str = "X",
    taxa_operacao_pct: float = TAXA_OPERACAO_PADRAO_PCT,
    slippage_pct: float = 0.0,
) -> dict:
    """Roda o backtest e devolve trades, série de patrimônio e resumo.

    ``modo="fomo"`` (padrão) exige posts já coletados e só compra com
    sentimento positivo. Com ``fonte="X"`` os ``perfis`` são obrigatórios;
    com ``fonte="Reddit"`` são subreddits e, se vazios, vale todo o Reddit
    da moeda. ``modo="flat"`` ignora sentimento e ``perfis`` por completo —
    compra e vende só pela variação do preço.
    """
    moeda_u = moeda.upper()
    modo = modo if modo in MODOS_VALIDOS else "fomo"
    fonte = "Reddit" if fonte == "Reddit" else "X"
    parametros = {
        "modo": modo,
        "fonte": fonte,
        "capital_inicial": capital_inicial,
        "valor_por_compra": valor_por_compra,
        "percentual_lucro_venda": percentual_lucro_venda,
        "percentual_queda_compra": percentual_queda_compra,
        "percentual_perda_aceita": percentual_perda_aceita,
        "taxa_operacao_pct": taxa_operacao_pct,
        "slippage_pct": slippage_pct,
    }

    agrupado: dict = {}

    if modo == "fomo":
        perfis_norm = _normalizar_perfis(perfis, fonte)
        if fonte == "X" and not perfis_norm:
            return _resultado_vazio(
                moeda_u, perfis, inicio, fim, parametros,
                "Informe ao menos um perfil do X.",
            )

        consulta = db.query(SocialPost).filter(
            SocialPost.moeda == moeda_u,
            SocialPost.fonte == fonte,
            SocialPost.sentimento != NULO,
            SocialPost.timestamp_post >= inicio,
            SocialPost.timestamp_post <= fim,
        )
        if perfis_norm:
            prefixo = _prefixo_autor(fonte)
            consulta = consulta.filter(
                func.lower(SocialPost.autor).in_(
                    [f"{prefixo}{p}" for p in perfis_norm]
                )
            )
        posts = consulta.order_by(SocialPost.timestamp_post.asc()).all()

        if not posts:
            origem = "desses perfis" if fonte == "X" else (
                "desses subreddits" if perfis_norm else "do Reddit"
            )
            return _resultado_vazio(
                moeda_u, perfis, inicio, fim, parametros,
                f"Nenhum post {origem} foi encontrado no período. Colete "
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

    resultado = _rodar_backtest(
        moeda_u, perfis, candles, agrupado, capital_inicial,
        valor_por_compra, percentual_lucro_venda, percentual_queda_compra,
        percentual_perda_aceita, modo, taxa_operacao_pct, slippage_pct,
    )
    resultado["parametros"]["fonte"] = fonte
    return resultado


# ═══════════════════════════════════════════════════════════════════════════
#  Carteira: posições, execução e métricas (comum aos três modos)
# ═══════════════════════════════════════════════════════════════════════════

COMPRADO, VENDIDO = 1, -1


def _saida(candle, s: int, alvo: float, stop: float) -> tuple[str, float] | None:
    """Se a posição de lado ``s`` sai neste candle: (motivo, preço de referência).

    Com máxima e mínima, confere dentro do candle; sem elas (candles salvos
    no banco), só pelo fechamento.
    """
    if candle.maxima is None or candle.minima is None:
        fech = candle.fechamento
        if s * (fech - stop) <= 0:
            return "perda", fech
        if s * (fech - alvo) >= 0:
            return "lucro", fech
        return None
    contra = candle.minima if s == COMPRADO else candle.maxima
    a_favor = candle.maxima if s == COMPRADO else candle.minima
    if s * (candle.abertura - stop) <= 0:
        return "perda", candle.abertura          # abriu além do stop (gap)
    if s * (contra - stop) <= 0:
        return "perda", stop
    if s * (candle.abertura - alvo) >= 0:
        return "lucro", candle.abertura
    if s * (a_favor - alvo) >= 0:
        return "lucro", alvo
    return None


class _Carteira:
    """Caixa, posições abertas e registro das operações.

    Cada posição reserva ``valor`` do caixa. Comprado: vale ``qtd x preço``.
    Vendido (a descoberto, simulado): ganha o quanto o preço cai —
    vale ``qtd x (2 x entrada - preço)``. Sem custo de aluguel da moeda.
    """

    def __init__(self, capital: float, taxa_pct: float, slippage_pct: float):
        self.caixa = capital
        self.taxa = taxa_pct / 100
        self.slip = slippage_pct / 100
        self.custos = 0.0
        self.posicoes: list[dict] = []
        self.trades: list[dict] = []
        self._id = 1

    @staticmethod
    def valor(pos: dict, preco: float) -> float:
        return pos["qtd"] * (pos["preco_entrada"] + pos["lado"] * (preco - pos["preco_entrada"]))

    def abrir(self, candle, preco_ref: float, valor: float, lado: int = COMPRADO,
              **extra) -> dict | None:
        if self.caixa < valor - 1e-9:
            return None
        preco = preco_ref * (1 + lado * self.slip)
        custo = valor * self.taxa
        self.custos += custo
        self.caixa -= valor
        pos = {"id": self._id, "lado": lado, "preco_entrada": preco,
               "qtd": (valor - custo) / preco, "valor_investido": valor,
               "inicio": candle.timestamp, "timestamp": para_iso_utc(candle.timestamp),
               **extra}
        self.posicoes.append(pos)
        self.trades.append({
            "tipo": "compra" if lado == COMPRADO else "venda",
            "entrada": True,
            "lado": "comprado" if lado == COMPRADO else "vendido",
            "posicao_id": self._id, "timestamp": pos["timestamp"],
            "preco": round(preco, 2), "qtd": pos["qtd"], "valor": round(valor, 2),
            "taxa": round(custo, 4),
            **{k: v for k, v in extra.items() if k in ("sentiment_score", "padrao")},
        })
        self._id += 1
        return pos

    def fechar(self, pos: dict, candle, preco_ref: float, motivo: str) -> None:
        lado = pos["lado"]
        preco = preco_ref * (1 - lado * self.slip)
        bruto = self.valor(pos, preco)
        custo = pos["qtd"] * preco * self.taxa
        self.custos += custo
        valor_saida = bruto - custo
        lucro = valor_saida - pos["valor_investido"]
        self.caixa += valor_saida
        self.posicoes.remove(pos)
        self.trades.append({
            "tipo": "venda" if lado == COMPRADO else "compra",
            "entrada": False,
            "lado": "comprado" if lado == COMPRADO else "vendido",
            "motivo": motivo, "posicao_id": pos["id"],
            "timestamp": para_iso_utc(candle.timestamp),
            "timestamp_entrada": pos["timestamp"],
            "duracao_h": round((candle.timestamp - pos["inicio"]).total_seconds() / 3600, 1),
            "preco": round(preco, 2), "qtd": pos["qtd"], "valor": round(valor_saida, 2),
            "taxa": round(custo, 4), "preco_entrada": round(pos["preco_entrada"], 2),
            "lucro": round(lucro, 2),
            "lucro_pct": round(lucro / pos["valor_investido"] * 100, 2),
            **({"padrao": pos["padrao"]} if "padrao" in pos else {}),
        })

    def valor_posicoes(self, preco: float) -> float:
        return sum(self.valor(p, preco) for p in self.posicoes)

    def ponto(self, candle, **extra) -> dict:
        preco = candle.fechamento
        abertas = self.valor_posicoes(preco)
        return {"timestamp": para_iso_utc(candle.timestamp), "preco": round(preco, 2),
                "caixa": round(self.caixa, 2), "valor_posicoes": round(abertas, 2),
                "patrimonio": round(self.caixa + abertas, 2),
                "posicoes_abertas": len(self.posicoes), **extra}


def _queda_maxima_pct(valores: list[float]) -> float:
    """Maior queda do pico ao vale, em % (0 = nunca caiu)."""
    pico, pior = None, 0.0
    for v in valores:
        pico = v if pico is None else max(pico, v)
        if pico > 0:
            pior = max(pior, (pico - v) / pico * 100)
    return round(pior, 2)


def _sharpe(valores: list[float], horas_por_candle: float) -> float | None:
    """Sharpe anualizado (sem taxa livre de risco) dos retornos por candle."""
    rets = [b / a - 1 for a, b in zip(valores, valores[1:]) if a > 0]
    if len(rets) < 2:
        return None
    media = sum(rets) / len(rets)
    var = sum((r - media) ** 2 for r in rets) / (len(rets) - 1)
    if var <= 0:
        return None
    return round(media / var ** 0.5 * (24 * 365 / horas_por_candle) ** 0.5, 2)


def _resumo(cart: _Carteira, candles: list, serie: list[dict], capital: float,
            horas_por_candle: float, extra: dict | None = None) -> dict:
    """Métricas do relatório, no formato do Strategy Tester do TradingView."""
    preco_inicial, preco_final = candles[0].fechamento, candles[-1].fechamento
    saidas = [t for t in cart.trades if not t["entrada"]]
    entradas = [t for t in cart.trades if t["entrada"]]
    ganhos = [t["lucro"] for t in saidas if t["lucro"] > 0]
    perdas = [t["lucro"] for t in saidas if t["lucro"] <= 0]

    valor_aberto = cart.valor_posicoes(preco_final)
    investido_aberto = sum(p["valor_investido"] for p in cart.posicoes)
    patrimonio_final = cart.caixa + valor_aberto
    lucro_total = patrimonio_final - capital

    qtd_bh = capital * (1 - cart.taxa) / preco_inicial if preco_inicial else 0.0
    patrimonio_bh = qtd_bh * preco_final
    lucro_bh_pct = round((patrimonio_bh - capital) / capital * 100, 2) if capital else 0.0
    lucro_total_pct = round(lucro_total / capital * 100, 2) if capital else 0.0

    patrimonios = [p["patrimonio"] for p in serie]
    bh = [capital * (1 - cart.taxa) * c.fechamento / preco_inicial for c in candles]
    expostos = sum(1 for p in serie if p["posicoes_abertas"] > 0)

    return {
        "total_horas_simuladas": round(len(candles) * horas_por_candle),
        "total_compras": sum(1 for t in entradas if t["lado"] == "comprado"),
        "total_vendas": sum(1 for t in saidas if t["lado"] == "comprado"),
        "total_entradas": len(entradas),
        "operacoes_fechadas": len(saidas),
        "vendas_lucrativas": len(ganhos),
        "vendas_por_stop_loss": sum(1 for t in saidas if t["motivo"] == "perda"),
        "saidas_por_prazo": sum(1 for t in saidas if t["motivo"] == "prazo"),
        "taxa_acerto_pct": round(len(ganhos) / len(saidas) * 100, 1) if saidas else None,
        "posicoes_abertas_final": len(cart.posicoes),
        "caixa_final": round(cart.caixa, 2),
        "valor_posicoes_abertas": round(valor_aberto, 2),
        "patrimonio_final": round(patrimonio_final, 2),
        "lucro_realizado": round(sum(t["lucro"] for t in saidas), 2),
        "lucro_nao_realizado": round(valor_aberto - investido_aberto, 2),
        "lucro_total": round(lucro_total, 2),
        "lucro_total_pct": lucro_total_pct,
        "custos_totais": round(cart.custos, 2),
        # Métricas de risco e das operações (as do relatório do TradingView).
        "queda_maxima_pct": _queda_maxima_pct(patrimonios),
        "fator_lucro": (round(sum(ganhos) / -sum(perdas), 2)
                        if perdas and sum(perdas) < 0 else None),
        "media_por_operacao": (round(sum(t["lucro"] for t in saidas) / len(saidas), 2)
                               if saidas else None),
        "media_por_operacao_pct": (round(sum(t["lucro_pct"] for t in saidas) / len(saidas), 2)
                                   if saidas else None),
        "maior_ganho": round(max(ganhos), 2) if ganhos else None,
        "maior_perda": round(min(perdas), 2) if perdas else None,
        "duracao_media_h": (round(sum(t["duracao_h"] for t in saidas) / len(saidas), 1)
                            if saidas else None),
        "exposicao_pct": round(expostos / len(serie) * 100, 1) if serie else 0.0,
        "sharpe": _sharpe(patrimonios, horas_por_candle),
        "buy_and_hold": {
            "preco_inicial": round(preco_inicial, 2),
            "preco_final": round(preco_final, 2),
            "patrimonio_final": round(patrimonio_bh, 2),
            "lucro_pct": lucro_bh_pct,
            "queda_maxima_pct": _queda_maxima_pct(bh),
        },
        "superou_buy_and_hold": lucro_total_pct > lucro_bh_pct,
        **(extra or {}),
    }


def _posicoes_abertas(cart: _Carteira, preco_final: float) -> list[dict]:
    return [{"id": p["id"], "lado": "comprado" if p["lado"] == COMPRADO else "vendido",
             "preco_entrada": round(p["preco_entrada"], 2), "qtd": p["qtd"],
             "valor_investido": round(p["valor_investido"], 2),
             "valor_atual": round(cart.valor(p, preco_final), 2),
             "timestamp_compra": p["timestamp"],
             **({"padrao": p["padrao"]} if "padrao" in p else {})}
            for p in cart.posicoes]


# ═══════════════════════════════════════════════════════════════════════════
#  Modos FOMO e Flat (grid de compras em quedas)
# ═══════════════════════════════════════════════════════════════════════════


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
    taxa_operacao_pct: float = TAXA_OPERACAO_PADRAO_PCT,
    slippage_pct: float = 0.0,
) -> dict:
    cart = _Carteira(capital_inicial, taxa_operacao_pct, slippage_pct)
    pico: float | None = None
    pendente: dict | None = None     # sinal do candle anterior, executa na abertura
    serie: list[dict] = []

    fator_lucro = 1 + percentual_lucro_venda / 100
    fator_queda = 1 - percentual_queda_compra / 100
    # "Assumir perda": abaixo desse preço a posição é vendida mesmo sem
    # atingir a meta de lucro — limita o prejuízo em vez de deixar a
    # posição aberta esperando uma recuperação que pode não vir.
    fator_perda = 1 - percentual_perda_aceita / 100

    for candle in candles:
        # Chave naive (sem fuso) para casar com `agrupado` (ver
        # correlacao.agregar_posts_por_hora); o que sai usa UTC explícito.
        grupo = agrupado.get(candle.timestamp.isoformat())

        # 1) Executa na abertura a compra sinalizada no fechamento anterior.
        if pendente is not None:
            pos = cart.abrir(candle, candle.abertura, valor_por_compra,
                             sentiment_score=pendente["sentiment_score"])
            if pos:
                # Exige uma nova perna de queda a partir daqui antes da
                # próxima compra — sem isso, compraria em toda hora abaixo
                # do pico antigo.
                pico = pos["preco_entrada"]
            pendente = None

        # 2) Vende o que bateu a meta ou o stop dentro do candle.
        for pos in list(cart.posicoes):
            entrada = pos["preco_entrada"]
            saida = _saida(candle, COMPRADO, entrada * fator_lucro, entrada * fator_perda)
            if saida:
                cart.fechar(pos, candle, saida[1], saida[0])

        # 3) No fechamento: atualiza o pico e decide se compra no próximo.
        preco = candle.fechamento
        pico = preco if pico is None else max(pico, preco)
        sentimento_positivo = grupo is not None and grupo.direcao == POSITIVO
        queda_suficiente = preco <= pico * fator_queda
        if queda_suficiente and (sentimento_positivo if modo == "fomo" else True) \
                and cart.caixa >= valor_por_compra:
            pendente = {"sentiment_score": grupo.sentiment_score if grupo else None}

        serie.append(cart.ponto(candle, sentiment_score=grupo.sentiment_score if grupo else None))

    resumo = _resumo(cart, candles, serie, capital_inicial, 1,
                     {"total_horas_com_posts": len(agrupado)})
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
            "taxa_operacao_pct": taxa_operacao_pct,
            "slippage_pct": slippage_pct,
        },
        "vazio": False,
        "mensagem": None,
        "resumo": resumo,
        "trades": cart.trades,
        "serie_patrimonio": serie,
        "posicoes_abertas": _posicoes_abertas(cart, candles[-1].fechamento),
    }


# ═══════════════════════════════════════════════════════════════════════════
#  Modo Padrões gráficos
# ═══════════════════════════════════════════════════════════════════════════

HORAS_POR_CANDLE = {"4h": 4, "12h": 12, "1d": 24}
# Candles lidos antes do período: um padrão que rompe no primeiro dia
# começou a se formar semanas antes.
CANDLES_ANTES = 300
MAX_DIAS_PADROES = 730


def simular_padroes(
    moeda: str,
    tipo: str,
    intervalo: str,
    inicio: datetime,
    fim: datetime,
    capital_inicial: float,
    valor_por_operacao: float,
    taxa_operacao_pct: float = TAXA_OPERACAO_PADRAO_PCT,
    slippage_pct: float = 0.0,
    candles: list | None = None,
) -> dict:
    """Opera cada rompimento do padrão ``tipo`` no período.

    Entrada na abertura do candle seguinte ao rompimento; saída no alvo
    (movimento medido), no stop (invalidação do padrão) ou no fim do
    horizonte do padrão, pelo fechamento. ``candles`` permite injetar a
    série nos testes (deve incluir o histórico antes de ``inicio``).
    """
    moeda_u = moeda.upper()
    nome, s, _, _ = svc_padroes.TIPOS[tipo]
    horas = HORAS_POR_CANDLE[intervalo]
    parametros = {
        "modo": "padroes", "padrao": tipo, "padrao_nome": nome,
        "direcao": "alta" if s == COMPRADO else "baixa", "intervalo": intervalo,
        "capital_inicial": capital_inicial, "valor_por_compra": valor_por_operacao,
        "taxa_operacao_pct": taxa_operacao_pct, "slippage_pct": slippage_pct,
    }

    if candles is None:
        try:
            candles = svc_mercado.buscar_klines_intervalo(
                svc_mercado.simbolo_para(moeda_u),
                inicio - timedelta(hours=horas * CANDLES_ANTES), fim,
                intervalo=intervalo, max_requisicoes=8,
            )
        except svc_mercado.ErroBinance as e:
            logger.warning("Sem candles para simular padrões de %s: %s", moeda_u, e)
            candles = []

    i0 = next((i for i, c in enumerate(candles) if c.timestamp >= inicio), None)
    if not candles or i0 is None:
        return _resultado_vazio(moeda_u, [], inicio, fim, parametros,
                                "Não foi possível obter preços da Binance para esse período.")

    # Agenda a entrada de cada rompimento ocorrido dentro do período.
    agenda: dict[int, list[dict]] = {}
    for p in svc_padroes.detectar(candles, tipos={tipo}):
        if p["status"] != "confirmado":
            continue
        i_entrada = p["i_rompimento"] + 1
        if p["i_rompimento"] >= i0 and i_entrada < len(candles):
            agenda.setdefault(i_entrada, []).append(p)
    if not agenda:
        return _resultado_vazio(
            moeda_u, [], inicio, fim, parametros,
            f"Nenhum rompimento de {nome.lower()} em {intervalo} no período. "
            "Tente um período maior ou outro padrão.")

    cart = _Carteira(capital_inicial, taxa_operacao_pct, slippage_pct)
    serie: list[dict] = []
    nao_operados = 0
    for j in range(i0, len(candles)):
        candle = candles[j]
        for p in agenda.get(j, []):
            pos = cart.abrir(candle, candle.abertura, valor_por_operacao, s,
                             padrao=nome, alvo=p["alvo"], stop=p["invalidacao"],
                             prazo=p["i_rompimento"] + p["horizonte"])
            nao_operados += pos is None
        for pos in list(cart.posicoes):
            saida = _saida(candle, s, pos["alvo"], pos["stop"])
            if saida:
                cart.fechar(pos, candle, saida[1], saida[0])
            elif j >= pos["prazo"]:
                cart.fechar(pos, candle, candle.fechamento, "prazo")
        serie.append(cart.ponto(candle))

    no_periodo = candles[i0:]
    resumo = _resumo(cart, no_periodo, serie, capital_inicial, horas,
                     {"rompimentos": sum(len(v) for v in agenda.values()),
                      "sem_caixa": nao_operados})
    return {
        "moeda": moeda_u,
        "perfis": [],
        "data_inicio": para_iso_utc(no_periodo[0].timestamp),
        "data_fim": para_iso_utc(no_periodo[-1].timestamp),
        "parametros": parametros,
        "vazio": False,
        "mensagem": None,
        "resumo": resumo,
        "trades": cart.trades,
        "serie_patrimonio": serie,
        "posicoes_abertas": _posicoes_abertas(cart, no_periodo[-1].fechamento),
    }


