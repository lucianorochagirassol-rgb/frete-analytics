"""Regras de negócio do Frete Analytics.

Este módulo não depende do Streamlit nem do banco: recebe o CSV exportado do
sistema ("Análise de Frete por NF") e devolve uma tabela com UMA LINHA POR NOTA
FISCAL, já identificada e classificada, mais um relatório de qualidade.

Regras (ver o relatório de revisão de 30/09/2026):
1. A nota é identificada por CNPJ do emitente + número + série. Se a mesma nota
   vier repetida na exportação, fica uma cópia só (a que tem frete).
2. Só entram notas emitidas pelos CNPJs do grupo (EMPRESAS_DO_GRUPO).
3. Transferência interna = cliente com raiz de CNPJ do grupo.
4. O % de frete considera só notas CIF (frete pago pela empresa).
5. Frete real = entrega + complementar + reentrega cobrados no conhecimento (DT).
   Frete de devolução fica guardado à parte.
6. Frete ausente nunca vira zero em silêncio: cada nota recebe um status
   ("cobrado", "estimado" pelo aprovisionamento, ou "sem_info").
7. O mês de cada nota vem da data de emissão.
"""

from __future__ import annotations

import io
import re

import pandas as pd

# ─── Configuração do grupo ──────────────────────────────────────────────────
# Raiz do CNPJ (8 primeiros dígitos) → nome da empresa.
EMPRESAS_DO_GRUPO = {
    "08706183": "LGR",
    "05968082": "Metal Mecânica Cruzeiro",
}

# CNPJ completo → nome curto do estabelecimento (para os filtros e tabelas).
ESTABELECIMENTOS = {
    "08706183000177": "LGR RS (matriz)",
    "08706183000258": "LGR SP (filial)",
    "05968082000186": "Metal Mecânica",
}

STATUS_COBRADO = "cobrado"
STATUS_ESTIMADO = "estimado"
STATUS_SEM_INFO = "sem_info"

# ─── Colunas do CSV ─────────────────────────────────────────────────────────
COL = {
    "numero": "Nº NF",
    "serie": "NF: Série",
    "data": "NF: Data Emissão",
    "natureza": "NF: Natureza",
    "emitente_cnpj": "NF: Emitente CNPJ",
    "emitente_nome": "NF: Emitente Nome",
    "cliente_cnpj": "NF: Cliente CNPJ",
    "cliente_nome": "NF: Cliente Nome",
    "cif_fob": "NF: CIF/FOB",
    "cfop": "NF: CFOP Predom.",
    "transportadora": "NF: Transportadora",
    "cidade_origem": "NF: De (Cidade)",
    "uf_origem": "NF: De (UF)",
    "cidade_destino": "NF: Até (Cidade)",
    "uf_destino": "NF: Até (UF)",
    "vlr_nf": "NF: R$ Total",
    "peso": "NF: Peso Bruto Kg",
    "frete_aprovisionamento": "NF: R$ Aprovisionamento",
    "dt_entrega": "DT: Entrega",
    "frete_entrega": "DT: R$ Entrega Cobrado",
    "frete_complementar": "DT: R$ Complementar Cobrado",
    "frete_reentrega": "DT: R$ Reentrega Cobrado",
    "frete_devolucao": "DT: R$ Devolução Cobrado",
}

# Sem estas o app não consegue funcionar.
OBRIGATORIAS = [
    "numero", "serie", "data", "emitente_cnpj", "cliente_cnpj", "cliente_nome",
    "cif_fob", "transportadora", "uf_destino", "cidade_destino", "vlr_nf",
    "peso", "frete_entrega",
]

NUMERICAS = [
    "vlr_nf", "peso", "frete_aprovisionamento", "frete_entrega",
    "frete_complementar", "frete_reentrega", "frete_devolucao",
]


# ─── Leitura ────────────────────────────────────────────────────────────────
def ler_csv(arquivo) -> pd.DataFrame:
    """Lê o CSV exportado (separador detectado automaticamente, tudo como texto)."""
    if hasattr(arquivo, "read"):
        conteudo = arquivo.read()
        if isinstance(conteudo, bytes):
            for enc in ("utf-8-sig", "latin-1"):
                try:
                    conteudo = conteudo.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
        arquivo = io.StringIO(conteudo)
    df = pd.read_csv(arquivo, sep=None, engine="python", dtype=str, keep_default_na=False)
    df.columns = df.columns.str.strip()
    return df


def colunas_faltando(df: pd.DataFrame) -> list[str]:
    return [COL[c] for c in OBRIGATORIAS if COL[c] not in df.columns]


def numero_br(serie: pd.Series) -> pd.Series:
    """Converte texto em número aceitando '1.234,56', '1234,56' e '1234.56'.
    Célula vazia vira NaN (e não zero): quem decide o que fazer com a falta de
    valor é a regra de negócio, não a leitura."""
    s = serie.astype(str).str.strip()
    s = s.str.replace(r"[R$\s]", "", regex=True)
    tem_virgula = s.str.contains(",", regex=False)
    s = s.where(~tem_virgula, s.str.replace(".", "", regex=False).str.replace(",", ".", regex=False))
    s = s.replace({"": None, "-": None, "nan": None, "None": None})
    return pd.to_numeric(s, errors="coerce")


def so_digitos(serie: pd.Series, tamanho: int = 14) -> pd.Series:
    s = serie.astype(str).map(lambda v: re.sub(r"\D", "", v))
    return s.where(s == "", s.str.zfill(tamanho))


def _texto(serie: pd.Series) -> pd.Series:
    return serie.astype(str).str.strip().str.replace(r"\s+", " ", regex=True)


# ─── Preparação ─────────────────────────────────────────────────────────────
def preparar(df_bruto: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Recebe o CSV lido e devolve (notas, relatorio).

    `notas` tem uma linha por nota fiscal do grupo, com as colunas usadas pelo
    app e pelo banco. `relatorio` resume o que foi descartado ou sinalizado.
    """
    rel: dict = {"linhas_lidas": len(df_bruto)}

    d = pd.DataFrame(index=df_bruto.index)
    for chave, coluna in COL.items():
        d[chave] = df_bruto[coluna] if coluna in df_bruto.columns else ""

    for c in ("numero", "serie", "natureza", "emitente_nome", "cliente_nome",
              "transportadora", "cidade_origem", "uf_origem", "cidade_destino",
              "uf_destino", "cfop", "dt_entrega"):
        d[c] = _texto(d[c])
    d["cif_fob"] = _texto(d["cif_fob"]).str.upper().str[:1]
    d["emitente_cnpj"] = so_digitos(d["emitente_cnpj"])
    d["cliente_cnpj"] = so_digitos(d["cliente_cnpj"])
    for c in NUMERICAS:
        d[c] = numero_br(d[c])
    d["data_emissao"] = pd.to_datetime(d["data"], dayfirst=True, errors="coerce")
    d = d.drop(columns=["data"])

    # 1) Escopo: só notas emitidas pelo grupo.
    raiz_emit = d["emitente_cnpj"].str[:8]
    no_grupo = raiz_emit.isin(EMPRESAS_DO_GRUPO.keys())
    fora = d[~no_grupo]
    rel["fora_do_escopo"] = int(len(fora))
    rel["fora_do_escopo_emitentes"] = (
        fora["emitente_nome"].value_counts().head(10).to_dict() if len(fora) else {}
    )
    d = d[no_grupo].copy()

    # 2) Sem data de emissão válida não dá para saber o mês.
    sem_data = d["data_emissao"].isna()
    rel["sem_data"] = int(sem_data.sum())
    d = d[~sem_data].copy()

    # 3) Uma linha por nota: CNPJ emitente + número + série. Se houver cópias,
    #    fica a que tem frete de entrega lançado.
    d["chave"] = d["emitente_cnpj"] + "|" + d["numero"] + "|" + d["serie"]
    d["_tem_frete"] = d["frete_entrega"].notna()
    d = d.sort_values(["chave", "_tem_frete"], ascending=[True, False])
    repetidas = d.duplicated("chave", keep="first")
    rel["duplicadas"] = int(repetidas.sum())
    rel["duplicadas_valor"] = float(d.loc[repetidas, "vlr_nf"].sum())
    rel["duplicadas_emitentes"] = (
        d.loc[repetidas, "emitente_nome"].value_counts().to_dict() if repetidas.any() else {}
    )
    d = d[~repetidas].drop(columns="_tem_frete")

    # 4) Classificação.
    d["mes"] = d["data_emissao"].dt.strftime("%Y-%m")
    d["estabelecimento"] = d["emitente_cnpj"].map(ESTABELECIMENTOS).fillna(
        d["emitente_cnpj"].str[:8].map(EMPRESAS_DO_GRUPO)
    )
    d["eh_interna"] = d["cliente_cnpj"].str[:8].isin(EMPRESAS_DO_GRUPO.keys())
    d["eh_cif"] = d["cif_fob"].eq("C")

    extras = d["frete_complementar"].fillna(0) + d["frete_reentrega"].fillna(0)
    tem_entrega = d["frete_entrega"].notna()
    tem_aprov = d["frete_aprovisionamento"].fillna(0) > 0
    d["status_frete"] = STATUS_SEM_INFO
    d.loc[tem_aprov, "status_frete"] = STATUS_ESTIMADO
    d.loc[tem_entrega, "status_frete"] = STATUS_COBRADO
    d["frete_real"] = d["frete_entrega"].fillna(0) + extras
    d["frete_estimado"] = d["frete_aprovisionamento"].where(
        d["status_frete"].eq(STATUS_ESTIMADO), 0
    ).fillna(0)
    d["data_emissao"] = d["data_emissao"].dt.date

    rel["notas"] = int(len(d))
    rel["internas"] = int(d["eh_interna"].sum())
    ext = d[~d["eh_interna"]]
    rel["fob"] = int((~ext["eh_cif"]).sum())
    rel["fob_valor"] = float(ext.loc[~ext["eh_cif"], "vlr_nf"].sum())
    base = ext[ext["eh_cif"]]
    rel["base_notas"] = int(len(base))
    rel["status_frete"] = base["status_frete"].value_counts().to_dict()
    rel["meses"] = sorted(d["mes"].unique().tolist())

    colunas = [
        "chave", "mes", "data_emissao", "numero", "serie", "emitente_cnpj",
        "emitente_nome", "estabelecimento", "cliente_cnpj", "cliente_nome",
        "natureza", "cfop", "cif_fob", "eh_cif", "eh_interna", "transportadora",
        "uf_origem", "cidade_origem", "uf_destino", "cidade_destino", "vlr_nf",
        "peso", "dt_entrega", "frete_entrega", "frete_complementar",
        "frete_reentrega", "frete_devolucao", "frete_aprovisionamento",
        "status_frete", "frete_real", "frete_estimado",
    ]
    return d[colunas].reset_index(drop=True), rel


# ─── Recortes e indicadores ─────────────────────────────────────────────────
def base_clientes(notas: pd.DataFrame) -> pd.DataFrame:
    """Notas que entram nos indicadores de frete: para clientes (não internas) e CIF."""
    if notas.empty:
        return notas
    return notas[~notas["eh_interna"].astype(bool) & notas["eh_cif"].astype(bool)]


def frete_considerado(df: pd.DataFrame, incluir_estimado: bool) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=float)
    return df["frete_real"] + (df["frete_estimado"] if incluir_estimado else 0)


def resumir(df: pd.DataFrame, por, incluir_estimado: bool) -> pd.DataFrame:
    """Totais por grupo: vendas, frete, peso, notas, % frete, R$/kg e % sem frete."""
    if df.empty:
        return pd.DataFrame()
    t = df.assign(
        _frete=frete_considerado(df, incluir_estimado),
        _sem=~df["status_frete"].eq(STATUS_COBRADO),
    )
    g = t.groupby(por, as_index=False, dropna=False).agg(
        vendas=("vlr_nf", "sum"),
        frete=("_frete", "sum"),
        peso=("peso", "sum"),
        notas=("chave", "count"),
        sem_frete=("_sem", "sum"),
    )
    g["pct_frete"] = (g["frete"] / g["vendas"].where(g["vendas"] > 0)) * 100
    g["rs_kg"] = g["frete"] / g["peso"].where(g["peso"] > 0)
    g["pct_sem_frete"] = g["sem_frete"] / g["notas"] * 100
    return g.fillna({"pct_frete": 0, "rs_kg": 0})


def totais(df: pd.DataFrame, incluir_estimado: bool) -> dict:
    if df.empty:
        return {"vendas": 0.0, "frete": 0.0, "peso": 0.0, "notas": 0,
                "pct_frete": 0.0, "rs_kg": 0.0, "pct_sem_frete": 0.0}
    vendas = float(df["vlr_nf"].sum())
    frete = float(frete_considerado(df, incluir_estimado).sum())
    peso = float(df["peso"].sum())
    return {
        "vendas": vendas,
        "frete": frete,
        "peso": peso,
        "notas": int(len(df)),
        "pct_frete": frete / vendas * 100 if vendas else 0.0,
        "rs_kg": frete / peso if peso else 0.0,
        "pct_sem_frete": float((~df["status_frete"].eq(STATUS_COBRADO)).mean() * 100),
    }


def sem_frete_por_transportadora(df: pd.DataFrame) -> pd.DataFrame:
    """Para o relatório de qualidade: onde falta frete cobrado."""
    if df.empty:
        return pd.DataFrame()
    t = df.assign(_sem=~df["status_frete"].eq(STATUS_COBRADO))
    g = t.groupby(["estabelecimento", "transportadora"], as_index=False).agg(
        notas=("chave", "count"), sem_frete=("_sem", "sum"), vendas=("vlr_nf", "sum"),
        estimado=("frete_estimado", "sum"),
    )
    g["pct_sem_frete"] = g["sem_frete"] / g["notas"] * 100
    return g[g["sem_frete"] > 0].sort_values("sem_frete", ascending=False)
