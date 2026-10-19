import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import App from "./App";
import { definirToken } from "./api";
import { moedasAcimaDoPerfil, textoDoAviso } from "./constants";

// O recharts mede o contêiner; no jsdom isso não existe, então o trocamos por
// um bloco simples só para a árvore montar.
jest.mock("recharts", () => {
  const Vazio = ({ children }) => <div>{children}</div>;
  return new Proxy({}, { get: () => Vazio });
});

const USUARIO = {
  id: 1, nome: "Ana Souza", email: "ana@exemplo.com",
  perfil: "Moderado", perfil_sugerido: "Moderado", pontuacao: 20,
};
const RISCOS = {
  moedas: {
    BTC: { risco: "baixo", volatilidade_anual_pct: 44.0, queda_maxima_pct: -48.8, dias: 365 },
    SOL: { risco: "médio", volatilidade_anual_pct: 63.9, queda_maxima_pct: -68.9, dias: 365 },
    DOGE: { risco: "médio", volatilidade_anual_pct: 66.3, queda_maxima_pct: -66.4, dias: 365 },
    ADA: { risco: "alto", volatilidade_anual_pct: 75.3, queda_maxima_pct: -78.9, dias: 365 },
  },
  metodologia: {
    janela_dias: 365,
    cortes_volatilidade_pct: { baixo_ate: 50, medio_ate: 70 },
    observacao: "Classificação relativa entre criptomoedas.",
  },
};
const QUESTIONARIO = {
  fontes: [{ titulo: "Resolução CVM nº 30", url: "https://conteudo.cvm.gov.br/legislacao/resolucoes/resol030.html" }],
  perguntas: [
    { id: "a", texto: "Primeira pergunta?", base: "Res. CVM 30, art. 3º, § 1º, I", opcoes: ["A0", "A1", "A2", "A3"] },
    { id: "b", texto: "Segunda pergunta?", base: "Res. CVM 30, art. 3º, § 2º, II", opcoes: ["B0", "B1", "B2", "B3"] },
  ],
  perfis: [
    { nome: "Conservador", descricao: "Protege o capital." },
    { nome: "Moderado", descricao: "Equilibra risco e retorno." },
    { nome: "Arrojado", descricao: "Busca crescimento." },
  ],
};

/** Troca o fetch por um roteador simples. Respostas com `__status` viram erro. */
function instalarApi(respostas = {}) {
  const chamadas = [];
  const tabela = {
    "/": { status: "ok", twitter_cookies: true, reddit_oauth: false },
    "/perfil/questionario": QUESTIONARIO,
    "/risco-moedas": RISCOS,
    "/auth/logout": { sucesso: true },
    ...respostas,
  };
  global.fetch = jest.fn((url, opcoes = {}) => {
    const caminho = url.replace(/^https?:\/\/[^/]+/, "").split("?")[0];
    chamadas.push({ caminho, opcoes });
    const corpo = tabela[caminho] ?? { pontos: [], resumo: null };
    const status = corpo.__status || 200;
    return Promise.resolve({
      ok: status < 400,
      status,
      json: () => Promise.resolve(corpo),
    });
  });
  return chamadas;
}

beforeEach(() => {
  localStorage.clear();
  definirToken(null);
  delete document.documentElement.dataset.theme;
});

function entrarNaConta() {
  fireEvent.change(screen.getByLabelText("E-mail"), { target: { value: "ana@exemplo.com" } });
  fireEvent.change(screen.getByLabelText("Senha"), { target: { value: "senha-bem-longa-123" } });
  const form = screen.getByRole("form");
  fireEvent.click(within(form).getByRole("button", { name: /^(Entrar|Criar conta)$/ }));
}

describe("acesso", () => {
  test("mostra a marca, e a Visão geral abre sem conta", () => {
    instalarApi();
    render(<App />);
    expect(screen.getByText("SentyCrypto")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Visão geral" })).toBeInTheDocument();
    expect(screen.getByText("Quer ir além da Visão geral?")).toBeInTheDocument();
  });

  test.each(["X e Reddit", "Simulador", "Analisador", "Meu perfil", "Configurações"])(
    "%s pede conta a quem não entrou",
    async (nome) => {
      instalarApi();
      render(<App />);
      fireEvent.click(screen.getByRole("tab", { name: new RegExp(nome) }));
      expect(await screen.findByText(`Entre na sua conta para usar ${nome}.`)).toBeInTheDocument();
      expect(screen.getByRole("form", { name: "Formulário de login" })).toBeInTheDocument();
    },
  );

  test("entra com e-mail e senha e libera as áreas", async () => {
    const chamadas = instalarApi({ "/auth/login": { token: "tok-1", usuario: USUARIO } });
    render(<App />);
    fireEvent.click(screen.getByRole("tab", { name: /Simulador/ }));
    await screen.findByText("Entre na sua conta para usar Simulador.");

    entrarNaConta();

    expect(await screen.findByText("Simulador de investimento")).toBeInTheDocument();
    expect(screen.getByText("Ana Souza")).toBeInTheDocument();
    expect(localStorage.getItem("senty_token")).toBe("tok-1");
    const login = chamadas.find((c) => c.caminho === "/auth/login");
    expect(JSON.parse(login.opcoes.body)).toEqual({
      email: "ana@exemplo.com", senha: "senha-bem-longa-123",
    });
  });

  test("senha errada mostra a mensagem e não entra", async () => {
    instalarApi({ "/auth/login": { __status: 401, detail: "E-mail ou senha incorretos." } });
    render(<App />);
    fireEvent.click(screen.getAllByRole("button", { name: "Entrar" })[0]);
    entrarNaConta();
    expect(await screen.findByRole("alert")).toHaveTextContent("E-mail ou senha incorretos.");
    expect(localStorage.getItem("senty_token")).toBeNull();
  });

  test("o token da sessão é enviado nas chamadas seguintes", async () => {
    const chamadas = instalarApi({ "/auth/login": { token: "tok-2", usuario: USUARIO } });
    render(<App />);
    fireEvent.click(screen.getByRole("tab", { name: /Analisador/ }));
    await screen.findByText("Entre na sua conta para usar Analisador.");
    entrarNaConta();
    await screen.findByText("Análise de texto");

    fireEvent.change(screen.getByLabelText("Texto"), { target: { value: "bitcoin sobe" } });
    fireEvent.click(screen.getByRole("button", { name: "Analisar texto" }));
    await waitFor(() => expect(chamadas.some((c) => c.caminho === "/analisar-texto")).toBe(true));
    const analise = chamadas.find((c) => c.caminho === "/analisar-texto");
    expect(analise.opcoes.headers.Authorization).toBe("Bearer tok-2");
  });

  test("sair apaga a sessão e volta à Visão geral", async () => {
    localStorage.setItem("senty_token", "tok-3");
    definirToken("tok-3");
    instalarApi({ "/auth/eu": USUARIO });
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Sair" }));
    expect(await screen.findByText("Quer ir além da Visão geral?")).toBeInTheDocument();
    expect(localStorage.getItem("senty_token")).toBeNull();
  });

  test("um login guardado que o servidor recusa é descartado", async () => {
    definirToken("tok-velho");
    instalarApi({ "/auth/eu": { __status: 401, detail: "Entre na sua conta." } });
    render(<App />);
    expect((await screen.findAllByRole("button", { name: "Criar conta" })).length).toBeGreaterThan(0);
    await waitFor(() => expect(localStorage.getItem("senty_token")).toBeNull());
  });
});

describe("cadastro e perfil", () => {
  test("quem cria a conta cai direto no questionário", async () => {
    instalarApi({
      "/auth/cadastro": { token: "tok-4", usuario: { ...USUARIO, perfil: null, perfil_sugerido: null } },
    });
    render(<App />);
    fireEvent.click(screen.getAllByRole("button", { name: "Criar conta" })[0]);
    fireEvent.change(await screen.findByLabelText("Nome"), { target: { value: "Ana Souza" } });
    fireEvent.change(screen.getByLabelText("E-mail"), { target: { value: "ana@exemplo.com" } });
    fireEvent.change(screen.getByLabelText("Senha"), { target: { value: "senha-bem-longa-123" } });
    const form = screen.getByRole("form", { name: "Formulário de cadastro" });
    fireEvent.click(within(form).getByRole("button", { name: "Criar conta" }));

    expect(await screen.findByText("Vamos definir seu perfil de investimento")).toBeInTheDocument();
    expect(screen.getByText("Perfil pendente")).toBeInTheDocument();
  });

  test("o questionário sugere o perfil e a pessoa confirma outro", async () => {
    localStorage.setItem("senty_tela", "perfil");
    definirToken("tok-5");
    const chamadas = instalarApi({
      "/auth/eu": { ...USUARIO, perfil: null, perfil_sugerido: null },
      "/perfil/questionario": QUESTIONARIO,
      "/perfil/confirmar": { ...USUARIO, perfil: "Conservador" },
    });
    // O mesmo caminho serve ao GET (perguntas) e ao POST (resultado).
    const base = global.fetch;
    global.fetch = jest.fn((url, opcoes = {}) =>
      opcoes.method === "POST" && url.includes("/perfil/questionario")
        ? Promise.resolve({
            ok: true, status: 200,
            json: () => Promise.resolve({
              perfil: "Arrojado", pontuacao: 28, pontuacao_maxima: 32,
              travas: ["Limitado por falta de reserva."], descricao: "Busca crescimento.",
            }),
          })
        : base(url, opcoes),
    );

    render(<App />);
    expect(await screen.findByText(/Primeira pergunta\?/)).toBeInTheDocument();
    expect(screen.getByText(/Base: Res\. CVM 30, art\. 3º, § 1º, I/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Resolução CVM nº 30" })).toHaveAttribute(
      "href", "https://conteudo.cvm.gov.br/legislacao/resolucoes/resol030.html");
    const enviar = screen.getByRole("button", { name: "Ver meu perfil sugerido" });
    expect(enviar).toBeDisabled();

    fireEvent.click(screen.getByLabelText("A3"));
    expect(enviar).toBeDisabled();
    fireEvent.click(screen.getByLabelText("B3"));
    expect(enviar).toBeEnabled();
    fireEvent.click(enviar);

    expect(await screen.findByText("Seu perfil sugerido")).toBeInTheDocument();
    expect(screen.getByText("Limitado por falta de reserva.")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Arrojado/ })).toHaveAttribute("aria-checked", "true");

    fireEvent.click(screen.getByRole("radio", { name: /Conservador/ }));
    fireEvent.click(screen.getByRole("button", { name: "Confirmar perfil Conservador" }));

    await waitFor(() => expect(chamadas.some((c) => c.caminho === "/perfil/confirmar")).toBe(true));
    const confirmar = chamadas.find((c) => c.caminho === "/perfil/confirmar");
    expect(JSON.parse(confirmar.opcoes.body)).toEqual({ perfil: "Conservador" });
    expect((await screen.findAllByText("Conservador")).length).toBeGreaterThan(0);
  });

  test("o simulador abre com os parâmetros do perfil da pessoa", async () => {
    localStorage.setItem("senty_tela", "sim");
    definirToken("tok-6");
    instalarApi({ "/auth/eu": { ...USUARIO, perfil: "Arrojado" } });
    render(<App />);
    await screen.findByText("Simulador de investimento");
    expect(screen.getByLabelText("Meta de lucro % BTC")).toHaveValue(10);
    expect(screen.getByLabelText("Stop-loss % BTC")).toHaveValue(20);
    expect(screen.getByText(/Seu perfil\./)).toBeInTheDocument();
  });

  test("moeda de risco acima do perfil gera aviso para quem é conservador", async () => {
    localStorage.setItem("senty_tela", "sim");
    definirToken("tok-7");
    instalarApi({ "/auth/eu": { ...USUARIO, perfil: "Conservador" } });
    render(<App />);
    await screen.findByText("Simulador de investimento");
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "DOGE" }));
    const aviso = await screen.findByRole("note");
    expect(aviso).toHaveTextContent(/DOGE \(risco médio: volatilidade de 66% ao ano e queda máxima de 66%\)/);
    expect(aviso).toHaveTextContent(/perfil conservador/);
  });

  test("quem é moderado só recebe aviso para moeda de risco alto", async () => {
    localStorage.setItem("senty_tela", "sim");
    definirToken("tok-9");
    instalarApi({ "/auth/eu": { ...USUARIO, perfil: "Moderado" } });
    render(<App />);
    await screen.findByText("Simulador de investimento");
    fireEvent.click(screen.getByRole("button", { name: "DOGE" }));
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "ADA" }));
    expect(await screen.findByRole("note")).toHaveTextContent(/ADA \(risco alto/);
  });

  test("se o risco medido não carregar, não inventa aviso", async () => {
    localStorage.setItem("senty_tela", "sim");
    definirToken("tok-10");
    instalarApi({ "/auth/eu": { ...USUARIO, perfil: "Conservador" }, "/risco-moedas": { __status: 502, detail: "Binance fora do ar" } });
    render(<App />);
    await screen.findByText("Simulador de investimento");
    fireEvent.click(screen.getByRole("button", { name: "DOGE" }));
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
    expect(screen.queryByText("Risco medido de cada moeda")).not.toBeInTheDocument();
  });

  test("a tabela mostra a volatilidade e a queda máxima medidas", async () => {
    localStorage.setItem("senty_tela", "sim");
    definirToken("tok-11");
    instalarApi({ "/auth/eu": USUARIO });
    render(<App />);
    expect(await screen.findByText("Risco medido de cada moeda")).toBeInTheDocument();
    expect(screen.getByText("75,3%")).toBeInTheDocument();
    expect(screen.getByText("-78,9%")).toBeInTheDocument();
  });
});

describe("regras de risco por perfil", () => {
  test("cada perfil tolera um nível de risco", () => {
    const moedas = ["BTC", "SOL", "ADA"];
    expect(moedasAcimaDoPerfil("Conservador", moedas, RISCOS)).toEqual(["SOL", "ADA"]);
    expect(moedasAcimaDoPerfil("Moderado", moedas, RISCOS)).toEqual(["ADA"]);
    expect(moedasAcimaDoPerfil("Arrojado", moedas, RISCOS)).toEqual([]);
  });

  test("sem moeda acima do perfil, sem perfil conhecido ou sem dados de risco não há aviso", () => {
    expect(textoDoAviso("Moderado", ["BTC", "SOL"], RISCOS)).toBeNull();
    expect(textoDoAviso("Perfil desconhecido", ["ADA"], RISCOS)).toBeNull();
    expect(textoDoAviso("Conservador", ["ADA"], null)).toBeNull();
    expect(textoDoAviso("Conservador", ["MOEDA_SEM_DADO"], RISCOS)).toBeNull();
  });

  test("o aviso cita os números medidos e diz que não é recomendação", () => {
    const t = textoDoAviso("Conservador", ["ADA"], RISCOS);
    expect(t).toMatch(/volatilidade de 75% ao ano/);
    expect(t).toMatch(/queda máxima de 79%/);
    expect(t).toMatch(/últimos 365 dias/);
    expect(t).toMatch(/não é recomendação de investimento/);
  });
});

describe("visão geral personalizada", () => {
  test("mostra a leitura do perfil a quem entrou", async () => {
    definirToken("tok-8");
    instalarApi({ "/auth/eu": { ...USUARIO, perfil: "Conservador" } });
    render(<App />);
    expect(await screen.findByText(/Para o seu perfil conservador/)).toBeInTheDocument();
    expect(screen.queryByText("Quer ir além da Visão geral?")).not.toBeInTheDocument();
  });
});

test("o tema pode ser trocado pela barra lateral, mesmo sem conta", () => {
  instalarApi();
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: /Tema: Sistema/ }));
  expect(document.documentElement.dataset.theme).toBe("light");
  fireEvent.click(screen.getByRole("button", { name: /Tema: Claro/ }));
  expect(document.documentElement.dataset.theme).toBe("dark");
  fireEvent.click(screen.getByRole("button", { name: /Tema: Escuro/ }));
  expect(document.documentElement.dataset.theme).toBeUndefined();
});
