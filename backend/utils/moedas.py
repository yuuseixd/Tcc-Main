"""Reconhecimento de menções a uma moeda dentro de um texto.

Antes, o coletor do Reddit exigia o ticker literal (``BTC``) enquanto o do X
também aceitava o nome por extenso (``BITCOIN``). Na prática o Reddit
descartava posts relevantes que o X aproveitava, e as duas fontes ficavam
incomparáveis — justamente o que o trabalho se propõe a comparar.
Agora as duas usam esta função.
"""

import re

# Nome por extenso e apelidos de cada ticker.
MAPA_NOMES: dict[str, list[str]] = {
    "BTC": ["BITCOIN"],
    "ETH": ["ETHEREUM", "ETHER"],
    "SOL": ["SOLANA"],
    "DOGE": ["DOGECOIN"],
    "XRP": ["RIPPLE"],
    "ADA": ["CARDANO"],
    "MATIC": ["POLYGON"],
    "DOT": ["POLKADOT"],
    "AVAX": ["AVALANCHE"],
    "LINK": ["CHAINLINK"],
    "BNB": ["BINANCE COIN", "BNB"],
    "LTC": ["LITECOIN"],
    "SHIB": ["SHIBA INU", "SHIBA"],
    "TRX": ["TRON"],
}

_PALAVRAS = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def termos_da_moeda(moeda: str) -> list[str]:
    """Todos os termos que identificam a moeda (ticker + nomes por extenso)."""
    moeda_u = moeda.upper().strip()
    return [moeda_u, *MAPA_NOMES.get(moeda_u, [])]


def texto_menciona_moeda(texto: str, moeda: str) -> bool:
    """Diz se o texto cita a moeda, por ticker (``BTC``/``$BTC``) ou nome.

    A comparação é por palavra inteira: buscar a substring ``"ADA"`` casaria
    dentro de ``"CANADA"`` e ``"SOL"`` dentro de ``"SOLUTION"``, enchendo a
    base de posts irrelevantes atribuídos à moeda errada.
    """
    if not texto:
        return False

    texto_upper = texto.upper()
    palavras = set(_PALAVRAS.findall(texto_upper))

    for termo in termos_da_moeda(moeda):
        if " " in termo:
            # Expressões compostas ("SHIBA INU") precisam de busca literal.
            if termo in texto_upper:
                return True
        elif termo in palavras:
            return True

    return False
