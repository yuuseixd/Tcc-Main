"""Normalização de datas e horas.

Motivo de existir
-----------------
O SQLite não guarda fuso horário: ao salvar um ``datetime`` com ``tzinfo``,
essa informação é silenciosamente descartada. Se depois compararmos a coluna
com um ``datetime`` *aware*, a comparação fica incorreta (ou lança erro em
outros bancos).

A regra do projeto é: **tudo é armazenado em UTC sem tzinfo (naive)**.
Toda data que entra no banco passa por :func:`para_utc_naive`, e toda data
que sai para a API volta a ser marcada como UTC por :func:`para_iso_utc`.
"""

import re
from datetime import datetime, timedelta, timezone

UTC = timezone.utc


def agora_utc() -> datetime:
    """Instante atual em UTC, *naive* (pronto para salvar no banco)."""
    return datetime.now(UTC).replace(tzinfo=None)


def para_utc_naive(dt: datetime | None) -> datetime | None:
    """Converte qualquer datetime para UTC sem tzinfo.

    - Se já for *naive*, assume que já está em UTC e devolve como está.
    - Se for *aware*, converte para UTC e remove o tzinfo.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(UTC).replace(tzinfo=None)


def de_timestamp_ms(ms: float) -> datetime:
    """Converte timestamp em milissegundos (padrão Binance) para UTC naive."""
    return datetime.fromtimestamp(ms / 1000, tz=UTC).replace(tzinfo=None)


def para_timestamp_ms(dt: datetime) -> int:
    """Converte um datetime (naive = UTC) para timestamp em milissegundos."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def para_iso_utc(dt: datetime | None) -> str | None:
    """Serializa para ISO 8601 marcando explicitamente o fuso UTC.

    Sem isso o frontend interpreta a data como horário local e os gráficos
    ficam deslocados pelo offset do fuso do usuário.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def parse_data_inicio(texto: str | None) -> datetime | None:
    """Interpreta uma data ISO de início de intervalo (00:00:00 UTC).

    Aceita tanto ``2026-03-01`` quanto ``2026-03-01T14:30:00Z``.
    Devolve ``None`` se o texto for vazio ou inválido.
    """
    dt = _parse_iso(texto)
    if dt is None:
        return None
    # Se veio apenas a data (sem hora), o início do dia já é 00:00.
    return dt


def parse_data_fim(texto: str | None) -> datetime | None:
    """Interpreta uma data ISO de fim de intervalo.

    Quando só a data é informada (``2026-03-03``), o intervalo se estende até
    o último instante do dia, senão o filtro excluiria o dia inteiro.
    """
    dt = _parse_iso(texto)
    if dt is None:
        return None
    if _e_somente_data(texto):
        return dt + timedelta(days=1) - timedelta(microseconds=1)
    return dt


def truncar_hora(dt: datetime) -> datetime:
    """Zera minutos/segundos — usado para agrupar registros por hora cheia."""
    return dt.replace(minute=0, second=0, microsecond=0)


def _e_somente_data(texto: str | None) -> bool:
    return bool(texto) and "T" not in texto and " " not in texto.strip()


# Offset de fuso que chegou como espaço em vez de "+" (ex.: "...T14:00:00 00:00").
_OFFSET_COM_ESPACO = re.compile(r"(\d{2}:\d{2}:\d{2}(?:\.\d+)?) (\d{2}:\d{2})$")


def _parse_iso(texto: str | None) -> datetime | None:
    if not texto:
        return None

    texto = texto.strip().replace("Z", "+00:00")
    # Num query string, "+" significa espaço. Um timestamp ISO com fuso
    # (2026-03-03T14:00:00+00:00) chega aqui como "...14:00:00 00:00" se o
    # cliente não usar encodeURIComponent, e seria rejeitado como inválido.
    texto = _OFFSET_COM_ESPACO.sub(r"\1+\2", texto)

    try:
        dt = datetime.fromisoformat(texto)
    except ValueError:
        return None
    return para_utc_naive(dt)
