"""Configuração comum dos testes."""

import os
import sys
from pathlib import Path

os.environ["CARREGAR_BERT"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from services import correlacao as svc_correlacao


@pytest.fixture(autouse=True)
def _cache_de_precos_limpo():
    """Cada teste simula a Binance do seu jeito; o cache não pode vazar."""
    svc_correlacao.limpar_cache()
    yield
    svc_correlacao.limpar_cache()
