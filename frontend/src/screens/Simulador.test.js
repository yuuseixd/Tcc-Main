import { operacoes, rankingSimples } from "./Simulador";

const st = (taxa, base, avaliados = 40) => ({
  taxa_acerto_pct: taxa, baseline_pct: base, vantagem_pp: taxa - base, avaliados,
});

const RANKING = {
  catalogo: [
    { tipo: "fundo_duplo", nome: "Fundo duplo", direcao: "alta" },
    { tipo: "oco", nome: "OCO", direcao: "baixa" },
    { tipo: "flamula_alta", nome: "Flâmula de alta", direcao: "alta" },
  ],
  tipos: {
    fundo_duplo: { geral: st(50, 40), moeda: st(60, 40, 12) },
    oco: { geral: st(70, 40), moeda: st(30, 40, 12) },
    // Vantagem enorme, mas com 3 casos: não pode ser o "melhor".
    flamula_alta: { geral: st(100, 40, 3), moeda: null },
  },
};

describe("rankingSimples", () => {
  test("ordena pelo backtest geral e ignora amostra pequena", () => {
    const { lista, melhor } = rankingSimples(RANKING);
    expect(lista.map((p) => p.tipo)).toEqual(["flamula_alta", "oco", "fundo_duplo"]);
    // A moeda isolada diria fundo duplo; o geral (mais casos) diz OCO.
    expect(melhor.tipo).toBe("oco");
  });

  test("sem ranking", () => {
    expect(rankingSimples(null)).toEqual({ lista: [], melhor: null });
  });
});

describe("operacoes", () => {
  test("junta entrada e saída e acumula o resultado", () => {
    const r = {
      trades: [
        { entrada: true, posicao_id: 1, tipo: "compra" },
        { entrada: true, posicao_id: 2, tipo: "compra" },
        { entrada: false, posicao_id: 1, lucro: 10 },
        { entrada: true, posicao_id: 3, tipo: "compra" },
        { entrada: false, posicao_id: 3, lucro: -4 },
      ],
      posicoes_abertas: [{ id: 2, valor_atual: 105, valor_investido: 100 }],
    };
    const linhas = operacoes(r);
    expect(linhas.map((l) => l.acumulado)).toEqual([10, null, 6]);
    expect(linhas[1].aberta.id).toBe(2);
  });
});
