export const MOEDAS = [
  { sigla: "BTC", nome: "Bitcoin" },
  { sigla: "ETH", nome: "Ethereum" },
  { sigla: "SOL", nome: "Solana" },
  { sigla: "DOGE", nome: "Dogecoin" },
  { sigla: "XRP", nome: "XRP" },
  { sigla: "ADA", nome: "Cardano" },
  { sigla: "AVAX", nome: "Avalanche" },
  { sigla: "LINK", nome: "Chainlink" },
];

export const nomeDaMoeda = (sigla) =>
  MOEDAS.find((m) => m.sigla === sigla)?.nome || sigla;

export const SUBREDDITS_DEFAULT = {
  BTC: ["Bitcoin", "CryptoCurrency", "BitcoinMarkets"],
  ETH: ["ethereum", "ethtrader", "CryptoCurrency"],
  SOL: ["solana", "CryptoCurrency"],
  DOGE: ["dogecoin", "CryptoCurrency"],
  XRP: ["XRP", "Ripple", "CryptoCurrency"],
  ADA: ["cardano", "CryptoCurrency"],
  AVAX: ["Avax", "CryptoCurrency"],
  LINK: ["Chainlink", "CryptoCurrency"],
};

export const INTERVALOS_COLETA = [
  { valor: 0, rotulo: "Desativada" },
  { valor: 3, rotulo: "A cada 3 min" },
  { valor: 5, rotulo: "A cada 5 min" },
  { valor: 10, rotulo: "A cada 10 min" },
  { valor: 15, rotulo: "A cada 15 min" },
  { valor: 30, rotulo: "A cada 30 min" },
];

/** Valores padrão de compra/venda do simulador, por moeda marcada. */
export const SIM_DEFAULTS = {
  capital_inicial: 1000,
  valor_por_compra: 100,
  percentual_lucro_venda: 5,
  percentual_queda_compra: 3,
  percentual_perda_aceita: 10,
};

/** Perfis do investidor: pré-configuram meta, queda e stop-loss do simulador. */
export const PERFIS_INVESTIDOR = {
  Conservador: {
    percentual_lucro_venda: 3,
    percentual_queda_compra: 5,
    percentual_perda_aceita: 5,
    nota: "Compra só com queda forte, vende cedo e corta a perda rápido.",
  },
  Moderado: {
    percentual_lucro_venda: 5,
    percentual_queda_compra: 3,
    percentual_perda_aceita: 10,
    nota: "Equilíbrio entre risco e retorno.",
  },
  Arrojado: {
    percentual_lucro_venda: 10,
    percentual_queda_compra: 2,
    percentual_perda_aceita: 20,
    nota: "Compra com quedas pequenas e espera ganhos maiores.",
  },
};

export const SIM_CAMPOS = [
  { chave: "capital_inicial", rotulo: "Capital (US$)", min: "1", step: "1" },
  { chave: "valor_por_compra", rotulo: "Por compra (US$)", min: "1", step: "1" },
  { chave: "percentual_lucro_venda", rotulo: "Meta de lucro %", min: "0.1", step: "0.1" },
  { chave: "percentual_queda_compra", rotulo: "Queda p/ comprar %", min: "0.1", step: "0.1" },
  { chave: "percentual_perda_aceita", rotulo: "Stop-loss %", min: "0.1", step: "0.1" },
];

export const listaDePerfis = (texto) =>
  (texto || "")
    .split(",")
    .map((p) => p.trim().replace(/^@/, "").replace(/^r\//i, ""))
    .filter(Boolean);

/** Ignora o erro disparado quando cancelamos uma requisição de propósito. */
export const foiCancelada = (e) => e?.name === "AbortError";

export const FONTES_SOCIAIS = [
  { valor: "X", rotulo: "X" },
  { valor: "Reddit", rotulo: "Reddit" },
];

const ORDEM_RISCO = { baixo: 0, médio: 1, alto: 2 };
/** Maior risco que cada perfil encara sem receber um aviso. */
const RISCO_TOLERADO = { Conservador: "baixo", Moderado: "médio", Arrojado: "alto" };

/**
 * `riscos` é a resposta de /risco-moedas: volatilidade e queda máxima de cada
 * moeda, medidas nos preços dos últimos 365 dias. Sem ela não há aviso.
 */
export function moedasAcimaDoPerfil(perfil, moedas, riscos) {
  const limite = ORDEM_RISCO[RISCO_TOLERADO[perfil]];
  if (limite == null || !riscos?.moedas) return [];
  return moedas.filter((m) => {
    const ordem = ORDEM_RISCO[riscos.moedas[m]?.risco];
    return ordem != null && ordem > limite;
  });
}

export function textoDoAviso(perfil, moedas, riscos) {
  const lista = moedasAcimaDoPerfil(perfil, moedas, riscos);
  if (lista.length === 0) return null;
  const detalhe = lista
    .map((m) => {
      const r = riscos.moedas[m];
      return `${m} (risco ${r.risco}: volatilidade de ${Math.round(r.volatilidade_anual_pct)}% ao ano e queda máxima de ${Math.abs(Math.round(r.queda_maxima_pct))}%)`;
    })
    .join("; ");
  return (
    `${detalhe} ${lista.length > 1 ? "têm" : "tem"} risco acima do habitual para o perfil ${perfil.toLowerCase()}. ` +
    `Medido nos preços dos últimos ${riscos.metodologia?.janela_dias ?? 365} dias e relativo entre criptomoedas; ` +
    "não é recomendação de investimento."
  );
}

/** Leitura adaptada ao perfil, mostrada na Visão geral. */
export const LEITURA_DO_PERFIL = {
  Conservador:
    "Para o seu perfil conservador: desconfie de sinais com poucas horas de dados " +
    "e priorize proteger o capital, sem decidir só pelo sentimento.",
  Moderado:
    "Para o seu perfil moderado: use o sentimento como um sinal entre vários e " +
    "confirme com a direção do preço antes de agir.",
  Arrojado:
    "Para o seu perfil arrojado: sentimento forte pode indicar oportunidade, mas " +
    "perdas temporárias grandes continuam possíveis.",
};
