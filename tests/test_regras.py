"""Testes das regras de negócio com dados fictícios.

Rodar com:  python -m pytest tests   (ou  python tests/test_regras.py)
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import regras as R  # noqa: E402

CABECALHO = (
    "Nº NF;NF: Série;NF: Data Emissão;NF: Natureza;NF: Emitente CNPJ;NF: Emitente Nome;"
    "NF: Cliente CNPJ;NF: Cliente Nome;NF: CIF/FOB;NF: Transportadora;NF: De (UF);"
    "NF: Até (Cidade);NF: Até (UF);NF: R$ Total;NF: Peso Bruto Kg;NF: R$ Aprovisionamento;"
    "DT: Entrega;DT: R$ Entrega Cobrado;DT: R$ Complementar Cobrado;DT: R$ Reentrega Cobrado"
)
LINHAS = [
    # 1) venda normal, frete cobrado + complementar
    "100;0;05/01/2026;OUTBOUND;08706183000177;LGR RS;11111111000100;CLIENTE A;C;TRANSP X;RS;PORTO ALEGRE;RS;1.000,00;100;90;DT1;80,00;5,00;",
    # 2) mesma nota da Metal repetida: uma cópia sem frete, outra com
    "200;0;06/01/2026;OUTBOUND;05968082000186;METAL;22222222000100;CLIENTE B;C;TRANSP Y;RS;CANOAS;RS;500,00;50;;;;;",
    "200;0;06/01/2026;OUTBOUND;05968082000186;METAL;22222222000100;CLIENTE B;C;TRANSP Y;RS;CANOAS;RS;500,00;50;;DT2;40,00;;",
    # 3) entrega local SP sem conhecimento: frete estimado pelo aprovisionamento
    "300;0;10/02/2026;OUTBOUND;08706183000258;LGR SP;33333333000100;CLIENTE C;C;RF;SP;SAO PAULO;SP;2000;200;120;;;;",
    # 4) transferência interna (cliente é a própria LGR)
    "400;0;11/02/2026;TRANSFERENCIA;08706183000177;LGR RS;08706183000258;LGR SP;C;TRANSP X;RS;SAO PAULO;SP;9000;900;;DT3;300;;",
    # 5) venda FOB (frete do cliente)
    "500;0;12/02/2026;OUTBOUND;08706183000177;LGR RS;44444444000100;CLIENTE D;F;TRANSP Z;RS;CURITIBA;PR;700;70;;;;;",
    # 6) nota de fornecedor (fora do escopo)
    "600;0;12/02/2026;INBOUND;99999999000100;FORNECEDOR;08706183000177;LGR RS;C;TRANSP Z;SC;BLUMENAU;SC;999;9;;;;;",
    # 7) duas notas diferentes com o mesmo frete no mesmo conhecimento: nenhuma pode ser zerada
    "700;0;13/02/2026;OUTBOUND;08706183000177;LGR RS;55555555000100;CLIENTE E;C;TRANSP X;RS;PELOTAS;RS;100;10;;DT9;25,00;;",
    "701;0;13/02/2026;OUTBOUND;08706183000177;LGR RS;55555555000100;CLIENTE E;C;TRANSP X;RS;PELOTAS;RS;100;10;;DT9;25,00;;",
]


def _preparar():
    csv = CABECALHO + "\n" + "\n".join(LINHAS) + "\n"
    return R.preparar(R.ler_csv(io.StringIO(csv)))


def test_escopo_e_duplicadas():
    notas, rel = _preparar()
    assert rel["fora_do_escopo"] == 1
    assert rel["duplicadas"] == 1
    assert rel["duplicadas_valor"] == 500.0
    assert len(notas) == 7
    metal = notas[notas["numero"] == "200"].iloc[0]
    assert metal["status_frete"] == R.STATUS_COBRADO and metal["frete_real"] == 40.0


def test_classificacao():
    notas, _ = _preparar()
    por_nf = notas.set_index("numero")
    assert bool(por_nf.loc["400", "eh_interna"]) is True
    assert bool(por_nf.loc["500", "eh_cif"]) is False
    assert por_nf.loc["100", "frete_real"] == 85.0          # entrega + complementar
    assert por_nf.loc["300", "status_frete"] == R.STATUS_ESTIMADO
    assert por_nf.loc["300", "frete_estimado"] == 120.0
    assert por_nf.loc["300", "frete_real"] == 0.0
    assert por_nf.loc["100", "mes"] == "2026-01" and por_nf.loc["300", "mes"] == "2026-02"
    assert por_nf.loc["100", "estabelecimento"] == "LGR RS (matriz)"
    # mesmo frete no mesmo conhecimento não zera ninguém
    assert por_nf.loc["700", "frete_real"] == 25.0 and por_nf.loc["701", "frete_real"] == 25.0


def test_indicadores():
    notas, _ = _preparar()
    base = R.base_clientes(notas)
    assert set(base["numero"]) == {"100", "200", "300", "700", "701"}
    t_real = R.totais(base, incluir_estimado=False)
    t_est = R.totais(base, incluir_estimado=True)
    assert t_real["vendas"] == 3700.0
    assert t_real["frete"] == 175.0            # 85 + 40 + 0 + 25 + 25
    assert t_est["frete"] == 295.0             # + 120 estimado
    assert round(t_real["pct_sem_frete"], 1) == 20.0


def test_numero_br():
    import pandas as pd
    s = R.numero_br(pd.Series(["1.234,56", "1234,56", "1234.56", "", "R$ 10,00"]))
    assert list(s[:3]) == [1234.56, 1234.56, 1234.56]
    assert pd.isna(s[3]) and s[4] == 10.0


if __name__ == "__main__":
    for nome, f in list(globals().items()):
        if nome.startswith("test_"):
            f()
            print("ok", nome)
