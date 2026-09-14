"""Schemas de entrada da API (Pydantic).

A validação aqui garante que valores inválidos sejam recusados com HTTP 422 e
uma mensagem clara, em vez de virarem erro 500 lá dentro do coletor.
"""

from pydantic import BaseModel, Field, field_validator, model_validator


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
        default=["whale_alert", "cabortopcripto"], min_length=1, max_length=50
    )
    # Padrão no máximo permitido (100): os coletores de fallback (sem
    # twikit) costumam ter lacunas grandes de data, então pedir mais tweets
    # por perfil aumenta a chance de achar algo dentro do período desejado.
    limite_por_perfil: int = Field(100, ge=1, le=100)

    @field_validator("moeda")
    @classmethod
    def _moeda_upper(cls, v: str) -> str:
        return v.strip().upper()


class FeedXRequest(BaseModel):
    perfis: list[str] = Field(
        default=["whale_alert", "cabortopcripto"], min_length=1, max_length=50
    )
    limite_por_perfil: int = Field(100, ge=1, le=100)
    # Os coletores só trazem os tweets mais recentes (não há busca histórica
    # por data sem API paga do X); estas datas filtram o que já foi
    # coletado, não ampliam o alcance da coleta.
    data_inicio: str | None = Field(None, description="Data ISO (ex: 2026-03-01)")
    data_fim: str | None = Field(None, description="Data ISO (ex: 2026-03-03)")


class SimulacaoInvestimentoRequest(BaseModel):
    """Parâmetros do backtest de compra/venda simulada.

    Dois modos (``modo``): "fomo" cruza o sentimento dos posts já
    coletados e salvos no banco com o preço (exige ``perfis``, não dispara
    coleta ao vivo); "flat" ignora sentimento e ``perfis`` por completo,
    compra e vende só pela variação percentual do preço. Ver
    ``services/simulacao.py``.
    """

    moeda: str = Field("BTC", max_length=20)
    modo: str = Field("fomo", pattern="^(fomo|flat)$")
    perfis: list[str] | None = Field(None, min_length=1, max_length=50)
    data_inicio: str = Field(..., description="Data ISO de início (ex: 2026-03-01)")
    data_fim: str = Field(..., description="Data ISO de fim (ex: 2026-03-08)")
    capital_inicial: float = Field(1000.0, gt=0, le=10_000_000)
    valor_por_compra: float = Field(100.0, gt=0, le=10_000_000)
    percentual_lucro_venda: float = Field(5.0, gt=0, le=1000)
    percentual_queda_compra: float = Field(3.0, gt=0, le=100)
    # "Assumir perda": stop-loss. Abaixo desse percentual em relação ao
    # preço de compra, a posição é vendida mesmo sem atingir a meta de
    # lucro — limita o prejuízo em vez de deixar a posição aberta esperando
    # uma recuperação que pode não vir.
    percentual_perda_aceita: float = Field(10.0, gt=0, le=100)

    @field_validator("moeda")
    @classmethod
    def _moeda_upper(cls, v: str) -> str:
        return v.strip().upper()

    @model_validator(mode="after")
    def _perfis_obrigatorios_no_fomo(self) -> "SimulacaoInvestimentoRequest":
        if self.modo == "fomo" and not self.perfis:
            raise ValueError(
                "O modo FOMO exige ao menos um perfil do X (o modo Flat não)."
            )
        return self


class TextoParaAnalise(BaseModel):
    # O modelo trunca em 512 tokens; o limite evita payloads abusivos.
    texto: str = Field(..., min_length=1, max_length=10_000)
    moeda: str = Field("BTC", max_length=20)


class LoginXRequest(BaseModel):
    auth_token: str = Field(..., min_length=10, max_length=500)
    ct0: str = Field(..., min_length=10, max_length=500)
