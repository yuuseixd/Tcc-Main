/**
 * Camada de acesso à API do SentCrypto.
 *
 * A URL vem de REACT_APP_API_URL (arquivo .env do frontend). Antes ela estava
 * fixa em 127.0.0.1:8000, o que impedia rodar o dashboard em qualquer lugar
 * que não fosse a própria máquina do backend.
 */

export const API_URL = (
  process.env.REACT_APP_API_URL || "http://127.0.0.1:8000"
).replace(/\/$/, "");

/** Erro de API que carrega o status HTTP, para a UI reagir de acordo. */
export class ApiError extends Error {
  constructor(mensagem, status) {
    super(mensagem);
    this.name = "ApiError";
    this.status = status;
  }
}

const CHAVE_TOKEN = "senty_token";
let tokenAtual = null;
let aoPerderSessao = null;

try {
  tokenAtual = localStorage.getItem(CHAVE_TOKEN);
} catch {
  /* navegador sem armazenamento: o login vale só nesta aba */
}

/** Guarda (ou apaga, com null) o token da sessão e passa a enviá-lo nas chamadas. */
export function definirToken(token) {
  tokenAtual = token || null;
  try {
    if (tokenAtual) localStorage.setItem(CHAVE_TOKEN, tokenAtual);
    else localStorage.removeItem(CHAVE_TOKEN);
  } catch {
    /* ignora */
  }
}

export const temToken = () => Boolean(tokenAtual);

/** Registra o que fazer quando o servidor recusa o token (sessão expirada). */
export function aoPerderSessaoFazer(callback) {
  aoPerderSessao = callback;
}

async function request(caminho, { method = "GET", body, signal } = {}) {
  const cabecalhos = {};
  if (body) cabecalhos["Content-Type"] = "application/json";
  if (tokenAtual) cabecalhos.Authorization = `Bearer ${tokenAtual}`;

  const resposta = await fetch(`${API_URL}${caminho}`, {
    method,
    signal,
    headers: Object.keys(cabecalhos).length ? cabecalhos : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });

  // 401 com token enviado = sessão expirada ou revogada. No login, 401 é só
  // "senha errada" e não deve derrubar nada.
  if (resposta.status === 401 && tokenAtual && !caminho.startsWith("/auth/login")) {
    aoPerderSessao?.();
  }

  // Respostas de erro do FastAPI trazem "detail", que pode ser texto ou a
  // lista de erros de validação do Pydantic.
  if (!resposta.ok) {
    let detalhe = `Erro ${resposta.status}`;
    try {
      const dados = await resposta.json();
      if (typeof dados.detail === "string") {
        detalhe = dados.detail;
      } else if (Array.isArray(dados.detail)) {
        detalhe = dados.detail.map((d) => d.msg).join("; ");
      }
    } catch {
      /* resposta sem corpo JSON — mantém a mensagem padrão */
    }
    throw new ApiError(detalhe, resposta.status);
  }

  return resposta.json();
}

const qs = (params) => {
  const busca = new URLSearchParams();
  Object.entries(params).forEach(([chave, valor]) => {
    if (valor !== undefined && valor !== null && valor !== "") {
      busca.append(chave, valor);
    }
  });
  const texto = busca.toString();
  return texto ? `?${texto}` : "";
};

export const api = {
  saude: (signal) => request("/", { signal }),

  cadastrar: (nome, email, senha) =>
    request("/auth/cadastro", { method: "POST", body: { nome, email, senha } }),
  entrar: (email, senha) =>
    request("/auth/login", { method: "POST", body: { email, senha } }),
  sair: () => request("/auth/logout", { method: "POST" }),
  eu: (signal) => request("/auth/eu", { signal }),
  questionario: (signal) => request("/perfil/questionario", { signal }),
  enviarQuestionario: (respostas) =>
    request("/perfil/questionario", { method: "POST", body: { respostas } }),
  confirmarPerfil: (perfil) =>
    request("/perfil/confirmar", { method: "POST", body: { perfil } }),

  riscoMoedas: (signal) => request("/risco-moedas", { signal }),

  sentimento: (moeda, signal) =>
    request(`/sentimento${qs({ moeda })}`, { signal }),

  historicoBinance: (moeda, dataInicio, dataFim, signal) =>
    request(
      `/historico-sentimento${qs({ moeda, data_inicio: dataInicio, data_fim: dataFim })}`,
      { signal },
    ),

  historicoDb: (moeda, dataInicio, dataFim, signal) =>
    request(
      `/historico-db${qs({ moeda, data_inicio: dataInicio, data_fim: dataFim })}`,
      { signal },
    ),

  historicoSocial: (moeda, fonte, dataInicio, dataFim, signal) =>
    request(
      `/historico-social${qs({ moeda, fonte, data_inicio: dataInicio, data_fim: dataFim })}`,
      { signal },
    ),

  correlacao: (moeda, fonte, signal) =>
    request(`/correlacao${qs({ moeda, fonte })}`, { signal }),

  comparacaoFontes: (moeda, signal) =>
    request(`/comparacao-fontes${qs({ moeda })}`, { signal }),

  syncBinance: (moeda, dias = 7) =>
    request(`/sync-binance${qs({ moeda, dias })}`, { method: "POST" }),

  coletarReddit: (corpo) =>
    request("/coletar/reddit", { method: "POST", body: corpo }),

  coletarX: (corpo) => request("/coletar/x", { method: "POST", body: corpo }),

  feedX: (corpo, signal) =>
    request("/feed/x", { method: "POST", body: corpo, signal }),

  simularInvestimento: (corpo, signal) =>
    request("/simulacao/investimento", { method: "POST", body: corpo, signal }),

  analisarTexto: (texto, moeda) =>
    request("/analisar-texto", { method: "POST", body: { texto, moeda } }),

  loginX: (authToken, ct0) =>
    request("/login/x", {
      method: "POST",
      body: { auth_token: authToken, ct0 },
    }),

  gerarRelatorio: (moeda, fonte, hora, indice) =>
    request(`/gerar-relatorio${qs({ moeda, fonte, hora, indice })}`, {
      method: "POST",
    }),

  gerarRelatorioCorrelacao: (moeda, fonte) =>
    request(`/gerar-relatorio-correlacao${qs({ moeda, fonte })}`, {
      method: "POST",
    }),
};
