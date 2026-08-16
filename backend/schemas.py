"""Schemas de entrada da API (Pydantic).

A validação aqui garante que valores inválidos sejam recusados com HTTP 422 e
uma mensagem clara, em vez de virarem erro 500 lá dentro do coletor.
"""

from pydantic import BaseModel, Field, field_validator


class ColetaRedditRequest(BaseModel):
    moeda: str = Field("BTC", max_length=20)
    subreddits: list[str] = Field(
        default=["CryptoCurrency", "Bitcoin", "ethtrader"],
        min_length=1,
        max_length=20,
    )
    limite_por_sub: int = Field(25, ge=1, le=100)
    ordenacao: str = Field("new", pattern="^(new|hot|top|rising|controversial)$")

    @field_validator("moeda")
    @classmethod
    def _moeda_upper(cls, v: str) -> str:
        return v.strip().upper()


class ColetaXRequest(BaseModel):
    moeda: str = Field("BTC", max_length=20)
    perfis: list[str] = Field(
        default=["whale_alert", "cabortopcripto"], min_length=1, max_length=20
    )
    limite_por_perfil: int = Field(20, ge=1, le=100)

    @field_validator("moeda")
    @classmethod
    def _moeda_upper(cls, v: str) -> str:
        return v.strip().upper()


class FeedXRequest(BaseModel):
    perfis: list[str] = Field(
        default=["whale_alert", "cabortopcripto"], min_length=1, max_length=20
    )
    limite_por_perfil: int = Field(30, ge=1, le=100)


class TextoParaAnalise(BaseModel):
    # O modelo trunca em 512 tokens; o limite evita payloads abusivos.
    texto: str = Field(..., min_length=1, max_length=10_000)
    moeda: str = Field("BTC", max_length=20)


class LoginXRequest(BaseModel):
    auth_token: str = Field(..., min_length=10, max_length=500)
    ct0: str = Field(..., min_length=10, max_length=500)
