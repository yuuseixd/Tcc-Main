import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import Analisador from "./Analisador";
import AnalisadorGraficos from "./AnalisadorGraficos";
import RankingPadroes, { evidencia } from "./RankingPadroes";

// Mesmo motivo do App.test.js: o recharts mede o contêiner, que o jsdom não tem.
jest.mock("recharts", () => {
  const Vazio = ({ children }) => <div>{children}</div>;
  return new Proxy({}, { get: () => Vazio });
});

const CATALOGO = [
  { tipo: "ocoi", nome: "OCOI (ombro-cabeça-ombro invertido)", direcao: "alta", categoria: "reversão", descricao: "Três fundos." },
  { tipo: "fundo_duplo", nome: "Fundo duplo", direcao: "alta", categoria: "reversão", descricao: "Dois fundos." },
  { tipo: "topo_duplo", nome: "Topo duplo", direcao: "baixa", categoria: "reversão", descricao: "Dois topos." },
];

const candles = Array.from({ length: 40 }, (_, i) => ({
  t: new Date(Date.UTC(2026, 0, 1, 4 * i)).toISOString(), c: 100 + i, h: 101 + i, l: 99 + i,
}));
const pv = (nome, rotulo, tipo, i, preco) => ({ nome, rotulo, tipo, i, preco, timestamp: candles[i].t });
const OCOI_EM_FORMACAO = {
  tipo: "ocoi", direcao: "alta",
  pivos: [pv("ombro_esquerdo", "OE", "fundo", 10, 100), pv("topo_esquerdo", "", "topo", 15, 120),
    pv("cabeca", "Cabeça", "fundo", 20, 90), pv("topo_direito", "", "topo", 25, 120),
    pv("ombro_direito", "OD", "fundo", 30, 100)],
  linhas: [{ nome: "Linha de pescoço", i1: 15, p1: 120, i2: 25, p2: 120 }],
  i_ultimo_pivo: 30, timestamp_formacao: candles[30].t,
  status: "em_formacao", i_status: 39, gatilho: 120, alvo: 150, invalidacao: 100,
};
const OCOI_ANTIGO = {
  ...OCOI_EM_FORMACAO, i_ultimo_pivo: 12, timestamp_formacao: candles[12].t,
  status: "confirmado", i_status: 35, i_rompimento: 35, entrada: 125, alvo_pct: 20, stop_pct: 20,
  horizonte: 40, desfecho: "alvo", i_desfecho: 38, retorno_pct: 12.5,
};
const TOPO_DUPLO = {
  tipo: "topo_duplo", direcao: "baixa",
  pivos: [pv("t1", "T1", "topo", 5, 140), pv("reacao1", "", "fundo", 8, 120), pv("t2", "T2", "topo", 11, 139)],
  linhas: [{ nome: "Suporte", i1: 5, p1: 120, i2: 11, p2: 120 }],
  i_ultimo_pivo: 11, timestamp_formacao: candles[11].t,
  status: "confirmado", i_status: 14, i_rompimento: 14, entrada: 118, gatilho: 120, alvo: 100,
  invalidacao: 140, alvo_pct: 15.3, stop_pct: 18.6, horizonte: 12, desfecho: "em_andamento",
  i_desfecho: 39, retorno_pct: -10,
};
const RESUMO = (taxa, base, extra = {}) => ({
  avaliados: 189, taxa_acerto_pct: taxa, ic95_pct: [taxa - 6, taxa + 6], baseline_pct: base,
  vantagem_pp: taxa - base, p_valor: 0.5, significativo: false, amostra_suficiente: true,
  candles_medios_ate_alvo: 22.4, ...extra,
});
const BACKTEST = {
  gerado_em: "2026-11-04T16:36:00Z", moedas: ["BTC", "ETH"], periodo_moeda: "2024-02-01 a 2026-11-04",
  tipos: {
    ocoi: { geral: RESUMO(33.9, 34.4), moeda: RESUMO(44.4, 40, { avaliados: 18, amostra_suficiente: false }) },
    fundo_duplo: { geral: RESUMO(55.5, 41.3, { p_valor: 0.0001, significativo: true }), moeda: null },
    topo_duplo: { geral: RESUMO(47, 38.7, { p_valor: 0.007, significativo: true }), moeda: null },
  },
};
const PADROES = {
  moeda: "BTC", intervalo: "4h", candles, catalogo: CATALOGO,
  padroes: [TOPO_DUPLO, OCOI_ANTIGO, OCOI_EM_FORMACAO], ativos: [0, 2], backtest: BACKTEST,
};
const RANKING = { moeda: "BTC", intervalo: "4h", catalogo: CATALOGO, ...BACKTEST };

function instalarApi({ padroes = PADROES, ranking = RANKING } = {}) {
  global.fetch = jest.fn((url) => {
    const corpo = url.includes("/padroes/ranking") ? ranking : padroes;
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(corpo) });
  });
}

const notify = { erro: jest.fn(), ok: jest.fn(), aviso: jest.fn() };

function Graficos(props) {
  return (
    <AnalisadorGraficos moeda="BTC" notify={notify} tipo="ocoi" setTipo={() => {}}
      intervalo="4h" setIntervalo={() => {}} {...props} />
  );
}

describe("analisador de gráficos", () => {
  test("mostra o padrão em formação com gatilho, alvo e invalidação", async () => {
    instalarApi();
    render(<Graficos />);

    expect(await screen.findByText("Gatilho (linha de rompimento)")).toBeInTheDocument();
    // No card e na legenda do gráfico.
    expect(screen.getAllByText("Alvo projetado")).toHaveLength(2);
    // Preço atual 139: alvo 150 fica +7,9% acima.
    expect(screen.getByText("+7,9% do preço atual")).toBeInTheDocument();
    expect(screen.getByText(/se um candle de 4h fechar acima de US\$ 120,00/)).toBeInTheDocument();
    expect(screen.getByText(/sem evidência de que o padrão seja melhor que o acaso/)).toBeInTheDocument();
    expect(global.fetch.mock.calls[0][0]).toMatch(/\/padroes\?moeda=BTC&intervalo=4h/);
  });

  test("lista os ativos de todos os tipos", async () => {
    instalarApi();
    render(<Graficos />);

    expect(await screen.findByText("Ativos agora em BTC 4h (2):")).toBeInTheDocument();
    // Um de alta (OCOI) e um de baixa (topo duplo).
    expect(screen.getByText(/Sinais conflitantes/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /↓ Topo duplo · confirmado · em andamento/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /↑ OCOI .* · em formação/ })).toBeInTheDocument();
  });

  test("clicar num ativo de outro tipo troca o padrão", async () => {
    instalarApi();
    const setTipo = jest.fn();
    render(<Graficos setTipo={setTipo} />);

    fireEvent.click(await screen.findByRole("button", { name: /Topo duplo/ }));
    expect(setTipo).toHaveBeenCalledWith("topo_duplo");
  });

  test("padrão de baixa: textos invertidos", async () => {
    instalarApi();
    render(<Graficos tipo="topo_duplo" />);

    // Na etiqueta do card e na tabela do período.
    expect(await screen.findAllByText("Confirmado · em andamento")).toHaveLength(2);
    expect(screen.getByText(/o padrão rompeu em US\$ 118,00; o alvo é US\$ 100,00 \(-15,3% da entrada\)/))
      .toBeInTheDocument();
    expect(screen.getByText(/o padrão foi melhor que o acaso \(p = 0,007\)/)).toBeInTheDocument();
    // Na tabela, alvo e stop como variação de preço: o alvo de baixa é negativo.
    const linha = screen.getByRole("button", { name: /Confirmado · em andamento/ });
    expect(linha).toHaveTextContent(/-15,3%.*\+18,6%/);
  });

  test("lista os padrões do tipo e troca o destaque ao clicar", async () => {
    instalarApi();
    render(<Graficos />);

    const linha = await screen.findByRole("button", { name: /Atingiu o alvo/ });
    expect(linha).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(linha);
    expect(linha).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(/\(atingiu o alvo\)/)).toBeInTheDocument();
  });

  test("tabela do backtest traz o geral e a moeda", async () => {
    instalarApi();
    render(<Graficos />);

    expect(await screen.findByText("Todas (2 moedas)")).toBeInTheDocument();
    expect(screen.getByText(/Confiabilidade medida · OCOI/)).toBeInTheDocument();
    expect(screen.getAllByText("33,9%").length).toBeGreaterThan(0);
  });

  test("sem padrões e sem backtest", async () => {
    instalarApi({ padroes: { ...PADROES, padroes: [], ativos: [], backtest: null } });
    render(<Graficos />);

    expect(await screen.findByText(/Nenhum padrão ativo agora em BTC 4h/)).toBeInTheDocument();
    expect(screen.getByText(/Nenhum OCOI .* nos últimos 40 candles de 4h/)).toBeInTheDocument();
    expect(screen.getByText(/Backtest ainda não gerado/)).toBeInTheDocument();
  });
});

describe("ranking dos padrões", () => {
  const linhasDoRanking = () =>
    within(screen.getByRole("table")).getAllByRole("button").map((tr) => tr.textContent);

  test("ordena pela vantagem sobre o acaso e destaca o melhor", async () => {
    instalarApi();
    render(<RankingPadroes moeda="BTC" onEscolher={() => {}} />);

    await screen.findByText(/Melhor em 4h:/);
    expect(screen.getByText(/Fundo duplo — acertou 55,5%/)).toBeInTheDocument();
    const linhas = linhasDoRanking();
    expect(linhas[0]).toMatch(/Fundo duplo.*\+14,2 pp/);
    expect(linhas[1]).toMatch(/Topo duplo/);
    expect(linhas[2]).toMatch(/OCOI.*-0,5 pp/);
  });

  test("ordenar pela taxa de acerto", async () => {
    instalarApi();
    render(<RankingPadroes moeda="BTC" onEscolher={() => {}} />);
    await screen.findByText(/Melhor em 4h:/);

    fireEvent.click(screen.getByRole("button", { name: "Taxa de acerto" }));
    expect(linhasDoRanking()[0]).toMatch(/Fundo duplo.*55,5%/);
  });

  test("seletor de moeda lista só as moedas do projeto", async () => {
    instalarApi();
    render(<RankingPadroes moeda="BTC" onEscolher={() => {}} />);
    const seletor = await screen.findByRole("combobox", { name: "Moeda do ranking" });

    const opcoes = within(seletor).getAllByRole("option").map((o) => o.value);
    expect(opcoes).toEqual(["todas", "BTC", "ETH", "SOL", "DOGE", "XRP", "ADA", "AVAX", "LINK"]);
    expect(seletor).toHaveValue("todas");
  });

  test("escolher uma moeda consulta e ordena só por ela", async () => {
    instalarApi();
    render(<RankingPadroes moeda="BTC" onEscolher={() => {}} />);
    await screen.findByText(/Melhor em 4h:/);

    fireEvent.change(screen.getByRole("combobox", { name: "Moeda do ranking" }), { target: { value: "ETH" } });
    await waitFor(() => expect(global.fetch.mock.calls.at(-1)[0]).toMatch(/ranking\?moeda=ETH&intervalo=4h/));
    // A resposta simulada traz só o OCOI por moeda: amostra pequena e sem dados vão para o fim.
    await waitFor(() => expect(linhasDoRanking()[0]).toMatch(/OCOI.*Amostra pequena/));
    expect(linhasDoRanking()[2]).toMatch(/Sem dados/);
  });

  test("trocar o intervalo refaz a consulta", async () => {
    instalarApi();
    render(<RankingPadroes moeda="BTC" onEscolher={() => {}} />);
    await screen.findByText(/Melhor em 4h:/);

    fireEvent.click(screen.getByRole("button", { name: "24h" }));
    await waitFor(() => expect(global.fetch.mock.calls.at(-1)[0]).toMatch(/ranking\?moeda=BTC&intervalo=1d/));
    fireEvent.click(screen.getByRole("button", { name: "Semana" }));
    await waitFor(() => expect(global.fetch.mock.calls.at(-1)[0]).toMatch(/ranking\?moeda=BTC&intervalo=1w/));
  });

  test("sem padrão com amostra suficiente, explica em vez de eleger um melhor", async () => {
    const pequenos = Object.fromEntries(Object.entries(BACKTEST.tipos).map(([t, r]) => [
      t, { ...r, geral: { ...r.geral, avaliados: 8, amostra_suficiente: false } },
    ]));
    instalarApi({ ranking: { ...RANKING, intervalo: "1w", tipos: pequenos } });
    render(<RankingPadroes moeda="BTC" onEscolher={() => {}} />);

    expect(await screen.findByText(/Sem conclusão em 4h/)).toBeInTheDocument();
    expect(screen.queryByText(/Melhor em/)).not.toBeInTheDocument();
  });

  test("intervalos disponíveis: 4h no mínimo, até a semana", async () => {
    instalarApi();
    render(<RankingPadroes moeda="BTC" onEscolher={() => {}} />);
    await screen.findByText(/Melhor em 4h:/);

    const grupo = screen.getByRole("group", { name: "Intervalo do ranking" });
    expect(within(grupo).getAllByRole("button").map((b) => b.textContent))
      .toEqual(["4h", "12h", "24h", "Semana"]);
  });

  test("clicar num padrão avisa qual tipo e intervalo abrir", async () => {
    instalarApi();
    const onEscolher = jest.fn();
    render(<RankingPadroes moeda="BTC" onEscolher={onEscolher} />);
    await screen.findByText(/Melhor em 4h:/);

    fireEvent.click(screen.getByRole("button", { name: /Ver Topo duplo no gráfico de 4h/ }));
    expect(onEscolher).toHaveBeenCalledWith("topo_duplo", "4h", "BTC");
  });

  test("grau de evidência com a correção de Bonferroni", () => {
    expect(evidencia(RESUMO(55, 41, { p_valor: 0.0001, significativo: true }), 36).texto).toBe("Forte");
    expect(evidencia(RESUMO(47, 39, { p_valor: 0.007, significativo: true }), 36).texto).toBe("Significativo");
    expect(evidencia(RESUMO(30, 35), 36).texto).toBe("Pior que o acaso");
    expect(evidencia(RESUMO(40, 35, { amostra_suficiente: false }), 36).texto).toBe("Amostra pequena");
    expect(evidencia(null, 36).texto).toBe("Sem dados");
  });

  test("sem backtest gerado", async () => {
    global.fetch = jest.fn(() => Promise.resolve({
      ok: false, status: 404, json: () => Promise.resolve({ detail: "Backtest ainda não gerado." }),
    }));
    render(<RankingPadroes moeda="BTC" onEscolher={() => {}} />);
    expect(await screen.findByText("Ranking indisponível")).toBeInTheDocument();
  });
});

test("clicar no ranking abre o padrão no analisador", async () => {
  instalarApi();
  render(<Analisador moeda="BTC" setMoeda={() => {}} notify={notify} />);
  await screen.findByText(/Melhor em 4h:/);
  const seletor = await screen.findByRole("combobox", { name: "Padrão gráfico" });
  await waitFor(() => expect(seletor).toHaveValue("ocoi"));

  fireEvent.click(screen.getByRole("button", { name: /Ver Topo duplo no gráfico de 4h/ }));
  expect(seletor).toHaveValue("topo_duplo");
});

test("com uma moeda filtrada no ranking, o clique abre o gráfico dela", async () => {
  instalarApi();
  const setMoeda = jest.fn();
  render(<Analisador moeda="BTC" setMoeda={setMoeda} notify={notify} />);
  await screen.findByText(/Melhor em 4h:/);

  fireEvent.change(screen.getByRole("combobox", { name: "Moeda do ranking" }), { target: { value: "SOL" } });
  await waitFor(() => expect(global.fetch.mock.calls.some(([u]) => /ranking\?moeda=SOL/.test(u))).toBe(true));
  fireEvent.click(await screen.findByRole("button", { name: /Ver Topo duplo no gráfico de 4h/ }));
  expect(setMoeda).toHaveBeenCalledWith("SOL");
});
