import datetime
import os
import unicodedata
import streamlit as st
import pandas as pd
import plotly.express as px

# ─── Logo da Empresa ──────────────────────────────────────────────────────────
# Coloque um arquivo "logo.png" na raiz do repositório (mesma pasta do app.py)
# para usar a logo da empresa no ícone da aba e na barra lateral.
LOGO_PATH = "logo.png"
LOGO_DISPONIVEL = os.path.exists(LOGO_PATH)

# ─── Configuração da Página ──────────────────────────────────────────────────
st.set_page_config(
    page_title="Análise de Logística e Frete",
    page_icon=LOGO_PATH if LOGO_DISPONIVEL else "🚚",
    layout="wide",
)

# ─── Mapeamento de Colunas do CSV ────────────────────────────────────────────
COLS = {
    "cliente":        "NF: Cliente Nome",
    "transportadora": "NF: Transportadora",
    "uf_destino":     "NF: Até (UF)",
    "cidade_destino": "NF: Até (Cidade)",
    "cidade_origem":  "NF: De (Cidade)",
    "vlr_pedido":     "NF: R$ Total",
    "peso":           "NF: Peso Bruto Kg",
    "vlr_frete":      "DT: R$ Entrega Cobrado",
    "tipo_frete":     "NF: CIF/FOB",
    "data":           "NF: Data Emissão",
}
C = COLS

# Colunas obrigatórias para o app funcionar (a coluna de data é opcional —
# sem ela, tudo continua funcionando, só ficam indisponíveis o histórico
# detalhado e a comparação por períodos).
COLS_OBRIGATORIAS = [
    "cliente", "transportadora", "uf_destino", "cidade_destino", "cidade_origem",
    "vlr_pedido", "peso", "vlr_frete", "tipo_frete",
]

# ─── Empresa própria (não é cliente — deve ser excluída dos dados) ──────────
def _normalizar_texto(s) -> str:
    s = str(s).upper().strip()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    return s

# Trecho distintivo do nome (sem sufixo societário) para casar variações como
# "EIRELI", "LTDA", com ou sem acento, caixa alta/baixa, espaços extras, etc.
EMPRESA_PROPRIA_CHAVE = "LGR INDUSTRIA DE COMERCIO DE PRODUTOS DE LIMPEZA"

def remover_empresa_propria(df: pd.DataFrame) -> pd.DataFrame:
    """Remove pedidos cujo cliente é a própria empresa (transferência interna,
    não é um cliente de fato e não deve entrar nos indicadores)."""
    if C["cliente"] not in df.columns:
        return df
    mask = df[C["cliente"]].apply(_normalizar_texto).str.contains(EMPRESA_PROPRIA_CHAVE, na=False)
    return df[~mask].copy()

# ─── Conexão Supabase (histórico mensal e detalhado) ─────────────────────────
SUPABASE_DISPONIVEL = True
SUPABASE_URL = None
SUPABASE_KEY = None
try:
    from supabase import create_client

    SUPABASE_URL = st.secrets.get("SUPABASE_URL", None)
    SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", None)
    if not SUPABASE_URL or not SUPABASE_KEY:
        SUPABASE_DISPONIVEL = False
except Exception:
    SUPABASE_DISPONIVEL = False

TABELA_HISTORICO = "fretes_mensais"     # resumo agregado por mês/estado
TABELA_PEDIDOS   = "pedidos_historico"  # pedidos individuais, linha a linha, com data


@st.cache_resource
def get_supabase_client():
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def salvar_historico_mensal(mes: str, agg_uf: pd.DataFrame):
    """Salva os totais agregados por estado para a competência informada."""
    client = get_supabase_client()
    registros = []
    for _, r in agg_uf.iterrows():
        registros.append({
            "mes":               mes,
            "estado":            r[C["uf_destino"]],
            "venda_total":       float(r["total_vendas"]),
            "frete_total":       float(r["total_frete"]),
            "peso_total":        float(r["total_peso"]),
            "frete_sobre_venda": float(r["pct_frete"]),
            "custo_por_kg":      float(r["rs_por_kg"]),
        })
    client.table(TABELA_HISTORICO).upsert(registros, on_conflict="mes,estado").execute()


@st.cache_data(ttl=300)
def carregar_historico_mensal() -> pd.DataFrame:
    """Busca todo o histórico agregado salvo no banco. Cache de 5 minutos."""
    if not SUPABASE_DISPONIVEL:
        return pd.DataFrame()
    client = get_supabase_client()
    resp = client.table(TABELA_HISTORICO).select("*").order("mes").execute()
    return pd.DataFrame(resp.data)


def salvar_pedidos_detalhados(mes: str, df: pd.DataFrame) -> tuple[int, int]:
    """Salva os pedidos individuais (linha a linha, com data) da competência
    informada no histórico detalhado, substituindo qualquer dado já salvo
    para esse mês. Retorna (qtd_salva, qtd_ignorada_sem_data)."""
    client = get_supabase_client()

    sub = df[df["_dt"].notna()].copy() if "_dt" in df.columns else df.iloc[0:0]
    ignorados = len(df) - len(sub)

    # Remove qualquer dado já salvo para esse mês antes de inserir de novo,
    # para evitar duplicar registros se o usuário salvar a mesma competência
    # mais de uma vez.
    client.table(TABELA_PEDIDOS).delete().eq("mes", mes).execute()

    registros = []
    for _, r in sub.iterrows():
        registros.append({
            "mes":            mes,
            "data":           r["_dt"].strftime("%Y-%m-%d"),
            "cliente":        str(r[C["cliente"]]),
            "transportadora": str(r[C["transportadora"]]),
            "uf_destino":     str(r[C["uf_destino"]]),
            "cidade_destino": str(r[C["cidade_destino"]]),
            "cidade_origem":  str(r[C["cidade_origem"]]),
            "vlr_pedido":     float(r[C["vlr_pedido"]]),
            "peso":           float(r[C["peso"]]),
            "vlr_frete":      float(r[C["vlr_frete"]]),
            "tipo_frete":     str(r[C["tipo_frete"]]),
        })

    TAMANHO_LOTE = 500
    for i in range(0, len(registros), TAMANHO_LOTE):
        lote = registros[i:i + TAMANHO_LOTE]
        if lote:
            client.table(TABELA_PEDIDOS).insert(lote).execute()

    return len(registros), ignorados


@st.cache_data(ttl=300)
def carregar_pedidos_historico() -> pd.DataFrame:
    """Busca todos os pedidos individuais salvos no histórico detalhado.
    Cache de 5 minutos."""
    if not SUPABASE_DISPONIVEL:
        return pd.DataFrame()
    client = get_supabase_client()
    resp = client.table(TABELA_PEDIDOS).select("*").execute()
    dfh = pd.DataFrame(resp.data)
    if not dfh.empty:
        dfh["_dt"] = pd.to_datetime(dfh["data"], errors="coerce")
    return dfh


# Mapa para reconstruir, a partir do histórico detalhado, um DataFrame com os
# mesmos nomes de coluna do CSV original — assim a mesma função de exibição
# (render_visao_estado) funciona para qualquer mês salvo no histórico
# detalhado, selecionado na barra lateral.
RENAME_HIST_PARA_CSV = {alias: col for alias, col in COLS.items() if alias != "data"}


# ─── Funções Auxiliares ──────────────────────────────────────────────────────
def formata_moeda(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def formata_pct(valor):
    return f"{valor:.2f}%"

def formata_kg(valor):
    return f"{valor:,.1f} kg".replace(",", "X").replace(".", ",").replace("X", ".")

def detalhe_pedidos(df_subset, titulo, key_prefix=""):
    """Exibe expander com os pedidos individuais que compõem um total."""
    with st.expander(f"🔎 Ver pedidos individuais — {titulo}"):
        cols_exibir = [
            C["cliente"], C["transportadora"],
            C["cidade_origem"], C["cidade_destino"],
            C["vlr_pedido"], C["peso"], C["vlr_frete"],
        ]
        cols_exibir = [c for c in cols_exibir if c in df_subset.columns]
        tbl = df_subset[cols_exibir].copy()

        rename = {
            C["cliente"]:        "Cliente",
            C["transportadora"]: "Transportadora",
            C["cidade_origem"]:  "Origem",
            C["cidade_destino"]: "Destino",
            C["vlr_pedido"]:     "Venda (R$)",
            C["peso"]:           "Peso (Kg)",
            C["vlr_frete"]:      "Frete (R$)",
        }
        tbl = tbl.rename(columns=rename)
        if "Venda (R$)" in tbl.columns and "Frete (R$)" in tbl.columns:
            tbl["Frete/Venda (%)"] = tbl.apply(
                lambda r: r["Frete (R$)"] / r["Venda (R$)"] * 100 if r["Venda (R$)"] > 0 else 0, axis=1
            ).apply(formata_pct)
        if "Frete (R$)" in tbl.columns and "Peso (Kg)" in tbl.columns:
            tbl["R$/Kg"] = tbl.apply(
                lambda r: r["Frete (R$)"] / r["Peso (Kg)"] if r["Peso (Kg)"] > 0 else 0, axis=1
            ).apply(lambda v: f"R$ {v:.2f}")
        if "Venda (R$)" in tbl.columns:
            tbl["Venda (R$)"] = tbl["Venda (R$)"].apply(formata_moeda)
        if "Frete (R$)" in tbl.columns:
            tbl["Frete (R$)"] = tbl["Frete (R$)"].apply(formata_moeda)
        if "Peso (Kg)" in tbl.columns:
            tbl["Peso (Kg)"] = tbl["Peso (Kg)"].apply(formata_kg)
        st.dataframe(tbl, use_container_width=True, hide_index=True)

@st.cache_data
def carregar_dados(arquivo) -> pd.DataFrame:
    df = pd.read_csv(arquivo, sep=None, engine="python", dtype=str)
    df.columns = df.columns.str.strip()

    df["_frete_faltante"] = False
    for alias, col in COLS.items():
        if col not in df.columns:
            continue
        if alias in ("vlr_pedido", "peso", "vlr_frete"):
            df[col] = (
                df[col]
                .astype(str)
                .str.replace(r"[^\d,\.]", "", regex=True)
                .str.replace(",", ".", regex=False)
            )
            df[col] = pd.to_numeric(df[col], errors="coerce")
            if alias == "vlr_frete":
                # Marca pedidos sem nenhum valor na coluna de frete (em geral,
                # romaneio/DT ainda não processado na origem) antes de zerar —
                # usado para alertar o usuário, já que isso pode subestimar o
                # frete total se for tratado como R$ 0,00 silenciosamente.
                df["_frete_faltante"] = df[col].isna()
            df[col] = df[col].fillna(0.0)
        elif alias in ("uf_destino", "cidade_destino", "cidade_origem",
                       "cliente", "transportadora", "tipo_frete"):
            df[col] = df[col].astype(str).str.strip()

    # Coluna de data (opcional) — usada para o histórico detalhado e para a
    # comparação por períodos. Aceita datas no formato brasileiro (dia/mês/ano).
    if C["data"] in df.columns:
        df["_dt"] = pd.to_datetime(df[C["data"]], dayfirst=True, errors="coerce")
    else:
        df["_dt"] = pd.NaT

    return df


def validar_colunas(df: pd.DataFrame) -> list[str]:
    return [COLS[a] for a in COLS_OBRIGATORIAS if COLS[a] not in df.columns]


MESES_PT = [
    "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]


# ─── Exibição reutilizável: Visão por Estado ─────────────────────────────────
def render_visao_estado(df_src: pd.DataFrame, key_prefix: str):
    """Renderiza a tabela consolidada, gráficos e detalhamento por estado para
    qualquer DataFrame no formato do CSV original (colunas em COLS). Usada
    tanto para o arquivo recém-carregado quanto para meses antigos recuperados
    do histórico detalhado, sem precisar de novo upload."""
    agg_uf = (
        df_src.groupby(C["uf_destino"], as_index=False)
        .agg(
            total_vendas=(C["vlr_pedido"], "sum"),
            total_frete= (C["vlr_frete"],  "sum"),
            total_peso=  (C["peso"],        "sum"),
            qtd_pedidos= (C["vlr_pedido"], "count"),
        )
    )
    agg_uf["rs_por_kg"] = agg_uf.apply(
        lambda r: r["total_frete"] / r["total_peso"] if r["total_peso"] > 0 else 0, axis=1
    )
    agg_uf["pct_frete"] = agg_uf.apply(
        lambda r: r["total_frete"] / r["total_vendas"] * 100 if r["total_vendas"] > 0 else 0, axis=1
    )
    agg_uf = agg_uf.sort_values("pct_frete", ascending=False).reset_index(drop=True)

    st.markdown("### 📋 Tabela Consolidada por Estado")
    display_uf = agg_uf.copy()
    display_uf.columns = [
        "Estado", "Total Vendas (R$)", "Total Frete (R$)",
        "Peso Total (Kg)", "Qtd Pedidos", "R$/Kg (Frete/Peso)", "Frete/Venda (%)"
    ]
    for col_m in ["Total Vendas (R$)", "Total Frete (R$)"]:
        display_uf[col_m] = display_uf[col_m].apply(formata_moeda)
    display_uf["Peso Total (Kg)"]    = display_uf["Peso Total (Kg)"].apply(formata_kg)
    display_uf["R$/Kg (Frete/Peso)"] = display_uf["R$/Kg (Frete/Peso)"].apply(lambda v: f"R$ {v:.2f}")
    display_uf["Frete/Venda (%)"]    = display_uf["Frete/Venda (%)"].apply(formata_pct)
    st.dataframe(display_uf, use_container_width=True, hide_index=True)

    # Detalhamento por estado
    ufs_disponiveis = sorted(agg_uf[C["uf_destino"]].tolist())
    uf_detalhe = st.selectbox("Ver pedidos de qual Estado?", ufs_disponiveis, key=f"uf_detalhe_{key_prefix}")
    detalhe_pedidos(df_src[df_src[C["uf_destino"]] == uf_detalhe], f"Estado {uf_detalhe}")

    # Gráficos
    col_g1, col_g2 = st.columns(2)
    with col_g1:
        fig_pct = px.bar(
            agg_uf, x=C["uf_destino"], y="pct_frete",
            title="% Frete sobre Venda por Estado",
            labels={C["uf_destino"]: "Estado", "pct_frete": "Frete/Venda (%)"},
            color="pct_frete", color_continuous_scale="RdYlGn_r", text_auto=".1f",
        )
        fig_pct.update_layout(showlegend=False, coloraxis_showscale=False)
        st.plotly_chart(fig_pct, use_container_width=True, key=f"fig_pct_{key_prefix}")

    with col_g2:
        fig_abs = px.bar(
            agg_uf.sort_values("total_frete", ascending=False),
            x=C["uf_destino"], y="total_frete",
            title="Total de Frete Absoluto por Estado (R$)",
            labels={C["uf_destino"]: "Estado", "total_frete": "Total Frete (R$)"},
            color="total_frete", color_continuous_scale="Blues", text_auto=".2s",
        )
        fig_abs.update_layout(showlegend=False, coloraxis_showscale=False)
        st.plotly_chart(fig_abs, use_container_width=True, key=f"fig_abs_{key_prefix}")

    # Clientes e Transportadoras no geral (todos os estados somados) — útil
    # para quem aparece em mais de uma UF.
    st.markdown("### 🌎 Clientes e Transportadoras (Geral — todos os estados)")
    st.caption(
        "Consolidado por cliente/transportadora somando todos os estados — "
        "mostra quem opera em mais de uma UF."
    )

    col_cli_g, col_transp_g = st.columns(2)
    with col_cli_g:
        st.markdown("**👤 Clientes (todos os estados)**")
        clientes_geral = (
            df_src.groupby(C["cliente"], as_index=False)
            .agg(
                total_vendas=(C["vlr_pedido"], "sum"),
                total_frete= (C["vlr_frete"],  "sum"),
                total_peso=  (C["peso"],       "sum"),
                qtd_pedidos= (C["vlr_pedido"], "count"),
                qtd_estados= (C["uf_destino"], "nunique"),
            )
            .sort_values("total_frete", ascending=False)
        )
        clientes_geral["pct_frete"] = clientes_geral.apply(
            lambda r: r["total_frete"] / r["total_vendas"] * 100 if r["total_vendas"] > 0 else 0, axis=1
        )
        clientes_geral["rs_por_kg"] = clientes_geral.apply(
            lambda r: r["total_frete"] / r["total_peso"] if r["total_peso"] > 0 else 0, axis=1
        )

        so_multi_cli = st.checkbox(
            "Mostrar só clientes em mais de 1 estado", key=f"so_multi_cli_{key_prefix}"
        )
        clientes_geral_show = (
            clientes_geral[clientes_geral["qtd_estados"] > 1] if so_multi_cli else clientes_geral
        )

        tbl_cli_geral = clientes_geral_show.copy()
        tbl_cli_geral.columns = [
            "Cliente", "Total Vendas (R$)", "Total Frete (R$)", "Peso Total (Kg)", "Qtd Pedidos",
            "Qtd Estados", "Frete/Venda (%)", "R$/Kg",
        ]
        tbl_cli_geral["Total Vendas (R$)"] = tbl_cli_geral["Total Vendas (R$)"].apply(formata_moeda)
        tbl_cli_geral["Total Frete (R$)"]  = tbl_cli_geral["Total Frete (R$)"].apply(formata_moeda)
        tbl_cli_geral["Peso Total (Kg)"]   = tbl_cli_geral["Peso Total (Kg)"].apply(formata_kg)
        tbl_cli_geral["Frete/Venda (%)"]   = tbl_cli_geral["Frete/Venda (%)"].apply(formata_pct)
        tbl_cli_geral["R$/Kg"]             = tbl_cli_geral["R$/Kg"].apply(lambda v: f"R$ {v:.2f}")
        st.dataframe(tbl_cli_geral, use_container_width=True, hide_index=True)

        clientes_geral_lista = clientes_geral_show[C["cliente"]].tolist()
        if clientes_geral_lista:
            cli_geral_sel = st.selectbox(
                "Ver pedidos do cliente (todos os estados):", clientes_geral_lista,
                key=f"cli_geral_sel_{key_prefix}",
            )
            detalhe_pedidos(
                df_src[df_src[C["cliente"]] == cli_geral_sel],
                f"{cli_geral_sel} (todos os estados)",
            )

    with col_transp_g:
        st.markdown("**🚛 Transportadoras (todos os estados)**")
        transp_geral = (
            df_src.groupby(C["transportadora"], as_index=False)
            .agg(
                total_vendas=(C["vlr_pedido"], "sum"),
                total_frete= (C["vlr_frete"], "sum"),
                total_peso=  (C["peso"],      "sum"),
                qtd_pedidos= (C["vlr_frete"], "count"),
                qtd_estados= (C["uf_destino"], "nunique"),
            )
            .sort_values("total_frete", ascending=False)
        )
        transp_geral["rs_por_kg"] = transp_geral.apply(
            lambda r: r["total_frete"] / r["total_peso"] if r["total_peso"] > 0 else 0, axis=1
        )
        transp_geral["pct_frete"] = transp_geral.apply(
            lambda r: r["total_frete"] / r["total_vendas"] * 100 if r["total_vendas"] > 0 else 0, axis=1
        )

        so_multi_transp = st.checkbox(
            "Mostrar só transportadoras em mais de 1 estado", key=f"so_multi_transp_{key_prefix}"
        )
        transp_geral_show = (
            transp_geral[transp_geral["qtd_estados"] > 1] if so_multi_transp else transp_geral
        )

        tbl_transp_geral = transp_geral_show.copy()
        tbl_transp_geral.columns = [
            "Transportadora", "Total Vendas (R$)", "Total Frete (R$)", "Peso Total (Kg)", "Qtd Pedidos",
            "Qtd Estados", "R$/Kg", "Frete/Venda (%)",
        ]
        tbl_transp_geral["Total Vendas (R$)"] = tbl_transp_geral["Total Vendas (R$)"].apply(formata_moeda)
        tbl_transp_geral["Total Frete (R$)"] = tbl_transp_geral["Total Frete (R$)"].apply(formata_moeda)
        tbl_transp_geral["Peso Total (Kg)"]  = tbl_transp_geral["Peso Total (Kg)"].apply(formata_kg)
        tbl_transp_geral["R$/Kg"]            = tbl_transp_geral["R$/Kg"].apply(lambda v: f"R$ {v:.2f}")
        tbl_transp_geral["Frete/Venda (%)"]  = tbl_transp_geral["Frete/Venda (%)"].apply(formata_pct)
        st.dataframe(tbl_transp_geral, use_container_width=True, hide_index=True)

        transp_geral_lista = transp_geral_show[C["transportadora"]].tolist()
        if transp_geral_lista:
            transp_geral_sel = st.selectbox(
                "Ver pedidos da transportadora (todos os estados):", transp_geral_lista,
                key=f"transp_geral_sel_{key_prefix}",
            )
            detalhe_pedidos(
                df_src[df_src[C["transportadora"]] == transp_geral_sel],
                f"{transp_geral_sel} (todos os estados)",
            )

    st.markdown("---")

    # Clientes e Transportadoras por UF
    st.markdown("### 🔍 Clientes e Transportadoras por Estado")
    uf_selecionada = st.selectbox("Selecione um Estado:", ufs_disponiveis, key=f"uf_{key_prefix}")
    df_uf = df_src[df_src[C["uf_destino"]] == uf_selecionada]

    col_cli, col_transp = st.columns(2)
    with col_cli:
        st.markdown(f"**👤 Clientes em {uf_selecionada}**")
        clientes_uf = (
            df_uf.groupby(C["cliente"], as_index=False)
            .agg(
                total_vendas=(C["vlr_pedido"], "sum"),
                total_frete= (C["vlr_frete"],  "sum"),
                total_peso=  (C["peso"],       "sum"),
                qtd_pedidos= (C["vlr_pedido"], "count"),
            )
            .sort_values("total_frete", ascending=False)
        )
        clientes_uf["pct_frete"] = clientes_uf.apply(
            lambda r: r["total_frete"] / r["total_vendas"] * 100 if r["total_vendas"] > 0 else 0, axis=1
        )
        clientes_uf["rs_por_kg"] = clientes_uf.apply(
            lambda r: r["total_frete"] / r["total_peso"] if r["total_peso"] > 0 else 0, axis=1
        )
        tbl_cli_uf = clientes_uf.copy()
        tbl_cli_uf.columns = [
            "Cliente", "Total Vendas (R$)", "Total Frete (R$)", "Peso Total (Kg)",
            "Qtd Pedidos", "Frete/Venda (%)", "R$/Kg",
        ]
        tbl_cli_uf["Total Vendas (R$)"] = tbl_cli_uf["Total Vendas (R$)"].apply(formata_moeda)
        tbl_cli_uf["Total Frete (R$)"]  = tbl_cli_uf["Total Frete (R$)"].apply(formata_moeda)
        tbl_cli_uf["Peso Total (Kg)"]   = tbl_cli_uf["Peso Total (Kg)"].apply(formata_kg)
        tbl_cli_uf["Frete/Venda (%)"]   = tbl_cli_uf["Frete/Venda (%)"].apply(formata_pct)
        tbl_cli_uf["R$/Kg"]             = tbl_cli_uf["R$/Kg"].apply(lambda v: f"R$ {v:.2f}")
        st.dataframe(tbl_cli_uf, use_container_width=True, hide_index=True)

        clientes_lista = clientes_uf[C["cliente"]].tolist()
        cli_sel = st.selectbox("Ver pedidos do cliente:", clientes_lista, key=f"cli_sel_{key_prefix}")
        detalhe_pedidos(df_uf[df_uf[C["cliente"]] == cli_sel], f"{cli_sel} em {uf_selecionada}")

    with col_transp:
        st.markdown(f"**🚛 Transportadoras em {uf_selecionada}**")
        transp_uf = (
            df_uf.groupby(C["transportadora"], as_index=False)
            .agg(
                total_vendas=(C["vlr_pedido"], "sum"),
                total_frete= (C["vlr_frete"], "sum"),
                total_peso=  (C["peso"],      "sum"),
                qtd_pedidos= (C["vlr_frete"], "count"),
            )
            .sort_values("total_frete", ascending=False)
        )
        transp_uf["rs_por_kg"] = transp_uf.apply(
            lambda r: r["total_frete"] / r["total_peso"] if r["total_peso"] > 0 else 0, axis=1
        )
        transp_uf["pct_frete"] = transp_uf.apply(
            lambda r: r["total_frete"] / r["total_vendas"] * 100 if r["total_vendas"] > 0 else 0, axis=1
        )
        tbl_transp_uf = transp_uf.copy()
        tbl_transp_uf.columns = [
            "Transportadora", "Total Vendas (R$)", "Total Frete (R$)", "Peso Total (Kg)",
            "Qtd Pedidos", "R$/Kg", "Frete/Venda (%)",
        ]
        tbl_transp_uf["Total Vendas (R$)"] = tbl_transp_uf["Total Vendas (R$)"].apply(formata_moeda)
        tbl_transp_uf["Total Frete (R$)"] = tbl_transp_uf["Total Frete (R$)"].apply(formata_moeda)
        tbl_transp_uf["Peso Total (Kg)"]  = tbl_transp_uf["Peso Total (Kg)"].apply(formata_kg)
        tbl_transp_uf["R$/Kg"]            = tbl_transp_uf["R$/Kg"].apply(lambda v: f"R$ {v:.2f}")
        tbl_transp_uf["Frete/Venda (%)"]  = tbl_transp_uf["Frete/Venda (%)"].apply(formata_pct)
        st.dataframe(tbl_transp_uf, use_container_width=True, hide_index=True)

        transp_lista = transp_uf[C["transportadora"]].tolist()
        transp_sel = st.selectbox("Ver pedidos da transportadora:", transp_lista, key=f"transp_sel_{key_prefix}")
        detalhe_pedidos(df_uf[df_uf[C["transportadora"]] == transp_sel], f"{transp_sel} em {uf_selecionada}")

    return agg_uf


# ─── Sidebar ─────────────────────────────────────────────────────────────────
with st.sidebar:
    if LOGO_DISPONIVEL:
        st.image(LOGO_PATH, width=160)
    else:
        st.image("https://cdn-icons-png.flaticon.com/512/2590/2590584.png", width=64)
    st.title("🚚 Frete Analytics")

    pedidos_hist_sidebar = carregar_pedidos_historico() if SUPABASE_DISPONIVEL else pd.DataFrame()
    meses_disponiveis_sidebar = (
        sorted(pedidos_hist_sidebar["mes"].unique().tolist(), reverse=True)
        if not pedidos_hist_sidebar.empty else []
    )
    if meses_disponiveis_sidebar:
        mes_visao_sel = st.selectbox(
            "📅 Mês para visualizar", meses_disponiveis_sidebar, key="mes_visao_sidebar"
        )
    else:
        mes_visao_sel = None
        st.caption("Nenhum mês salvo ainda — envie um CSV na aba **Upload Mensal**.")

    st.markdown("---")
    st.caption("📤 Use a aba **Upload Mensal** para enviar o CSV todo mês.")
    st.markdown("---")
    if SUPABASE_DISPONIVEL:
        st.success("🟢 Histórico conectado")
    else:
        st.warning("🟡 Histórico desconectado\n\nConfigure SUPABASE_URL e SUPABASE_KEY em Secrets para habilitar.")

# Dados do mês selecionado na barra lateral — usados nas abas "Visão por Estado"
# e "Análise de Deficiência", que agora sempre mostram o histórico salvo no
# banco (e não o arquivo recém-enviado na aba de Upload).
df_mes_sel = None
if mes_visao_sel and not pedidos_hist_sidebar.empty:
    df_mes_sel = pedidos_hist_sidebar[pedidos_hist_sidebar["mes"] == mes_visao_sel].copy()
    df_mes_sel = df_mes_sel.rename(columns=RENAME_HIST_PARA_CSV)

st.markdown("## 🚚 Análise de Logística & Eficiência de Fretes")

tab_upload, tab1, tab2, tab3 = st.tabs([
    "📤 Upload Mensal",
    "📍 Visão por Estado",
    "🔬 Análise de Deficiência",
    "📊 Comparação Mensal",
])

# ═══════════════════════════════════════════════════════════════════════════════
# ABA 0 — UPLOAD MENSAL
# ═══════════════════════════════════════════════════════════════════════════════
df = None
faltando = []
mes_competencia = None
arquivo = None

with tab_upload:
    st.markdown("### 📂 Envie o CSV do mês")
    arquivo = st.file_uploader("Upload do CSV mensal", type=["csv"])

    st.markdown("### 📅 Competência do arquivo")
    hoje = datetime.date.today()
    col_mes, col_ano = st.columns(2)
    mes_nome = col_mes.selectbox("Mês", MESES_PT, index=hoje.month - 1)
    ano_sel = col_ano.number_input("Ano", min_value=2020, max_value=2100, value=hoje.year, step=1)
    mes_competencia = f"{ano_sel}-{MESES_PT.index(mes_nome) + 1:02d}"

    if arquivo is not None:
        df = carregar_dados(arquivo)
        faltando = validar_colunas(df)
        if faltando:
            st.error(f"⚠️ Colunas não encontradas no CSV:\n\n{faltando}\n\nVerifique se o arquivo está correto.")
            df = None
        else:
            qtd_antes = len(df)
            df = remover_empresa_propria(df)
            qtd_removida = qtd_antes - len(df)
            if qtd_removida > 0:
                st.caption(
                    f"ℹ️ {qtd_removida} registro(s) da própria empresa (LGR Indústria de Comércio "
                    f"de Produtos de Limpeza) foram excluídos da análise — não são clientes."
                )
            if C["data"] not in df.columns or df["_dt"].notna().sum() == 0:
                st.warning(
                    "🟡 Não foi encontrada a coluna de data (\"NF: Data Emissão\") com datas válidas "
                    "neste CSV. O histórico detalhado e a comparação por períodos não estarão "
                    "disponíveis para esta competência."
                )

            if "_frete_faltante" in df.columns and df["_frete_faltante"].sum() > 0:
                qtd_faltante = int(df["_frete_faltante"].sum())
                pct_faltante = qtd_faltante / len(df) * 100
                venda_faltante = df.loc[df["_frete_faltante"], C["vlr_pedido"]].sum()
                ufs_faltantes = (
                    df.loc[df["_frete_faltante"], C["uf_destino"]]
                    .value_counts()
                    .head(5)
                )
                ufs_txt = ", ".join(f"{uf} ({n})" for uf, n in ufs_faltantes.items())
                st.warning(
                    f"⚠️ **{qtd_faltante} pedido(s) ({pct_faltante:.0f}%) estão sem valor na coluna "
                    f"de frete** (\"DT: R$ Entrega Cobrado\") — provavelmente o romaneio/DT ainda não "
                    f"foi processado na origem quando este CSV foi exportado. Esses pedidos estão sendo "
                    f"somados como **R$ 0,00 de frete**, o que pode subestimar o total e o %Frete/Venda.\n\n"
                    f"Vendas envolvidas: {formata_moeda(venda_faltante)}. "
                    f"Estados mais afetados: {ufs_txt}.\n\n"
                    f"Recomendado: aguardar o romaneio fechar e reexportar antes de salvar esta "
                    f"competência no histórico."
                )

    if df is not None:
        st.markdown("---")
        total_pedidos = df[C["vlr_pedido"]].sum()
        total_frete   = df[C["vlr_frete"]].sum()
        total_peso    = df[C["peso"]].sum()
        pct_global    = (total_frete / total_pedidos * 100) if total_pedidos > 0 else 0

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("💰 Total de Vendas", formata_moeda(total_pedidos))
        col2.metric("🚛 Total de Fretes", formata_moeda(total_frete))
        col3.metric("⚖️ Peso Total",      formata_kg(total_peso))
        col4.metric("📊 Frete / Venda",   formata_pct(pct_global))

        st.markdown("---")
        st.markdown("### 💾 Salvar no Histórico")
        tem_data = C["data"] in df.columns and df["_dt"].notna().sum() > 0
        st.caption(
            f"Salva os pedidos da competência **{mes_competencia}** no histórico detalhado — "
            f"alimenta as abas **Visão por Estado** e **Análise de Deficiência** (via seleção "
            f"de mês na barra lateral) e a **Comparação por Períodos**. Se já existir um "
            f"registro para este mês, ele será substituído."
        )
        if not tem_data:
            st.caption(
                "⚠️ Sem coluna de data válida neste CSV — não é possível salvar no histórico detalhado."
            )

        if st.button(
            "💾 Salvar dados detalhados",
            type="primary",
            disabled=not SUPABASE_DISPONIVEL or not tem_data,
        ):
            try:
                qtd_salva, qtd_ignorada = salvar_pedidos_detalhados(mes_competencia, df)
                carregar_pedidos_historico.clear()
                msg = f"✅ {qtd_salva} pedido(s) de {mes_competencia} salvos no histórico detalhado!"
                if qtd_ignorada > 0:
                    msg += f" ({qtd_ignorada} ignorado(s) por falta de data válida.)"
                st.success(msg)
            except Exception as e:
                st.error(f"Erro ao salvar dados detalhados: {e}")

        if not SUPABASE_DISPONIVEL:
            st.caption("⚠️ Conecte o Supabase (Secrets) para habilitar o salvamento.")
    else:
        st.info("📂 Envie um CSV acima para começar a análise.")

st.markdown("---")

# ═══════════════════════════════════════════════════════════════════════════════
# ABA 1 — VISÃO POR ESTADO (mês selecionado na barra lateral)
# ═══════════════════════════════════════════════════════════════════════════════
with tab1:
    if df_mes_sel is None:
        st.info(
            "📂 Nenhum mês com dados detalhados salvos ainda. Envie um CSV na aba "
            "**Upload Mensal**, clique em **Salvar dados detalhados** e depois "
            "selecione o mês na barra lateral."
        )
    else:
        render_visao_estado(df_mes_sel, f"p1_{mes_visao_sel}")

# ═══════════════════════════════════════════════════════════════════════════════
# ABA 2 — ANÁLISE DE DEFICIÊNCIA
# ═══════════════════════════════════════════════════════════════════════════════
with tab2:
    if df_mes_sel is None:
        st.info(
            "📂 Nenhum mês com dados detalhados salvos ainda. Envie um CSV na aba "
            "**Upload Mensal**, clique em **Salvar dados detalhados** e depois "
            "selecione o mês na barra lateral."
        )
    else:
        ufs_disponiveis_p2 = sorted(df_mes_sel[C["uf_destino"]].unique().tolist())
        uf_deep = st.selectbox(
            "Selecione o Estado para investigar:", ufs_disponiveis_p2, index=0,
            key=f"uf_deep_{mes_visao_sel}",
        )
        df_deep = df_mes_sel[df_mes_sel[C["uf_destino"]] == uf_deep].copy()

        if df_deep.empty:
            st.warning("Nenhum dado para o estado selecionado.")
        else:
            st.markdown(f"### 👤 Maiores Gastos por Cliente em **{uf_deep}**")
            cliente_agg = (
                df_deep.groupby(C["cliente"], as_index=False)
                .agg(
                    total_venda=(C["vlr_pedido"], "sum"),
                    total_frete=(C["vlr_frete"],  "sum"),
                    total_peso= (C["peso"],       "sum"),
                    qtd_pedidos=(C["vlr_pedido"], "count"),
                )
            )
            cliente_agg["pct_frete"] = cliente_agg.apply(
                lambda r: r["total_frete"] / r["total_venda"] * 100 if r["total_venda"] > 0 else 0, axis=1
            )
            cliente_agg["rs_por_kg"] = cliente_agg.apply(
                lambda r: r["total_frete"] / r["total_peso"] if r["total_peso"] > 0 else 0, axis=1
            )
            cliente_agg = cliente_agg.sort_values("total_frete", ascending=False)

            col_c1, col_c2 = st.columns(2)
            with col_c1:
                fig_cli_abs = px.bar(
                    cliente_agg.head(15), x="total_frete", y=C["cliente"], orientation="h",
                    title=f"Top 15 Clientes — Frete Absoluto (R$) em {uf_deep}",
                    labels={"total_frete": "Total Frete (R$)", C["cliente"]: "Cliente"},
                    color="total_frete", color_continuous_scale="Reds",
                )
                fig_cli_abs.update_layout(yaxis={"categoryorder": "total ascending"}, coloraxis_showscale=False)
                st.plotly_chart(fig_cli_abs, use_container_width=True)

            with col_c2:
                fig_cli_pct = px.bar(
                    cliente_agg.sort_values("pct_frete", ascending=False).head(15),
                    x="pct_frete", y=C["cliente"], orientation="h",
                    title=f"Top 15 Clientes — % Frete/Venda em {uf_deep}",
                    labels={"pct_frete": "Frete/Venda (%)", C["cliente"]: "Cliente"},
                    color="pct_frete", color_continuous_scale="OrRd",
                )
                fig_cli_pct.update_layout(yaxis={"categoryorder": "total ascending"}, coloraxis_showscale=False)
                st.plotly_chart(fig_cli_pct, use_container_width=True)

            with st.expander("📄 Ver tabela completa por cliente"):
                tbl_cli = cliente_agg.copy()
                tbl_cli.columns = [
                    "Cliente", "Total Venda (R$)", "Total Frete (R$)", "Peso Total (Kg)",
                    "Qtd Pedidos", "Frete/Venda (%)", "R$/Kg",
                ]
                tbl_cli["Total Venda (R$)"] = tbl_cli["Total Venda (R$)"].apply(formata_moeda)
                tbl_cli["Total Frete (R$)"] = tbl_cli["Total Frete (R$)"].apply(formata_moeda)
                tbl_cli["Peso Total (Kg)"]  = tbl_cli["Peso Total (Kg)"].apply(formata_kg)
                tbl_cli["Frete/Venda (%)"]  = tbl_cli["Frete/Venda (%)"].apply(formata_pct)
                tbl_cli["R$/Kg"]            = tbl_cli["R$/Kg"].apply(lambda v: f"R$ {v:.2f}")
                st.dataframe(tbl_cli, use_container_width=True, hide_index=True)

            clientes_deep = cliente_agg[C["cliente"]].tolist()
            cli_deep_sel = st.selectbox(
                "Ver pedidos individuais do cliente:", clientes_deep, key=f"cli_deep_sel_{mes_visao_sel}"
            )
            detalhe_pedidos(df_deep[df_deep[C["cliente"]] == cli_deep_sel], f"{cli_deep_sel} em {uf_deep}")

            st.markdown(f"### 🔀 Comparativo de Transportadoras por Rota em **{uf_deep}**")
            st.caption("Isole uma rota idêntica (mesma origem → destino) e compare o R$/Kg de cada transportadora.")

            df_deep["_rota"] = df_deep[C["cidade_origem"]] + " → " + df_deep[C["cidade_destino"]]
            rotas = sorted(df_deep["_rota"].unique().tolist())

            if not rotas:
                st.info("Nenhuma rota disponível para o estado selecionado.")
            else:
                rota_sel = st.selectbox("Selecione a Rota:", rotas, key=f"rota_sel_{mes_visao_sel}")
                df_rota = df_deep[df_deep["_rota"] == rota_sel].copy()
                df_rota = df_rota[df_rota[C["peso"]] > 0]

                if df_rota.empty:
                    st.warning("Nenhum registro com peso válido para esta rota.")
                else:
                    rota_agg = (
                        df_rota.groupby(C["transportadora"], as_index=False)
                        .agg(
                            total_venda=        (C["vlr_pedido"], "sum"),
                            total_frete=        (C["vlr_frete"], "sum"),
                            total_peso=         (C["peso"],       "sum"),
                            qtd_embarques=      (C["vlr_frete"], "count"),
                            ticket_medio_frete= (C["vlr_frete"], "mean"),
                        )
                    )
                    rota_agg["rs_por_kg"] = rota_agg.apply(
                        lambda r: r["total_frete"] / r["total_peso"] if r["total_peso"] > 0 else 0, axis=1
                    )
                    rota_agg["pct_frete"] = rota_agg.apply(
                        lambda r: r["total_frete"] / r["total_venda"] * 100 if r["total_venda"] > 0 else 0, axis=1
                    )
                    rota_agg = rota_agg.sort_values("rs_por_kg")

                    melhor    = rota_agg.iloc[0][C["transportadora"]]
                    mais_caro = rota_agg.iloc[-1][C["transportadora"]]
                    delta_pct = (
                        (rota_agg.iloc[-1]["rs_por_kg"] - rota_agg.iloc[0]["rs_por_kg"])
                        / rota_agg.iloc[0]["rs_por_kg"] * 100
                        if rota_agg.iloc[0]["rs_por_kg"] > 0 else 0
                    )

                    col_m1, col_m2, col_m3 = st.columns(3)
                    col_m1.metric("✅ Mais Barato", melhor)
                    col_m2.metric("⚠️ Mais Caro",  mais_caro)
                    col_m3.metric("📈 Diferença de Custo", formata_pct(delta_pct))

                    fig_rota = px.bar(
                        rota_agg, x=C["transportadora"], y="rs_por_kg",
                        title=f"R$/Kg por Transportadora — Rota: {rota_sel}",
                        labels={C["transportadora"]: "Transportadora", "rs_por_kg": "R$/Kg"},
                        color="rs_por_kg", color_continuous_scale="RdYlGn_r", text_auto=".3f",
                    )
                    fig_rota.update_layout(coloraxis_showscale=False)
                    st.plotly_chart(fig_rota, use_container_width=True)

                    tbl_rota = rota_agg.copy()
                    tbl_rota.columns = [
                        "Transportadora", "Total Venda (R$)", "Total Frete (R$)", "Peso Total (Kg)",
                        "Qtd Embarques", "Ticket Médio (R$)", "R$/Kg", "Frete/Venda (%)"
                    ]
                    tbl_rota["Total Venda (R$)"]  = tbl_rota["Total Venda (R$)"].apply(formata_moeda)
                    tbl_rota["Total Frete (R$)"]  = tbl_rota["Total Frete (R$)"].apply(formata_moeda)
                    tbl_rota["Peso Total (Kg)"]   = tbl_rota["Peso Total (Kg)"].apply(formata_kg)
                    tbl_rota["Ticket Médio (R$)"] = tbl_rota["Ticket Médio (R$)"].apply(formata_moeda)
                    tbl_rota["R$/Kg"]             = tbl_rota["R$/Kg"].apply(lambda v: f"R$ {v:.4f}")
                    tbl_rota["Frete/Venda (%)"]   = tbl_rota["Frete/Venda (%)"].apply(formata_pct)
                    st.dataframe(tbl_rota, use_container_width=True, hide_index=True)

                    transp_rota_lista = rota_agg[C["transportadora"]].tolist()
                    transp_rota_sel = st.selectbox(
                        "Ver embarques individuais da transportadora:", transp_rota_lista,
                        key=f"transp_rota_sel_{mes_visao_sel}",
                    )
                    detalhe_pedidos(
                        df_rota[df_rota[C["transportadora"]] == transp_rota_sel],
                        f"{transp_rota_sel} — {rota_sel}"
                    )

                    with st.expander("📊 Ver dispersão de todos os embarques nesta rota"):
                        df_rota["rs_kg_ind"] = df_rota[C["vlr_frete"]] / df_rota[C["peso"]]
                        fig_scatter = px.strip(
                            df_rota, x=C["transportadora"], y="rs_kg_ind", color=C["transportadora"],
                            title="Distribuição de R$/Kg por Embarque e Transportadora",
                            labels={C["transportadora"]: "Transportadora", "rs_kg_ind": "R$/Kg por Embarque"},
                            hover_data=[C["cliente"], C["vlr_frete"], C["peso"]],
                        )
                        st.plotly_chart(fig_scatter, use_container_width=True)

# ═══════════════════════════════════════════════════════════════════════════════
# ABA 3 — COMPARAÇÃO MENSAL (comparar períodos com datas customizadas)
# ═══════════════════════════════════════════════════════════════════════════════
with tab3:
    st.markdown("## 📊 Comparação Mensal")

    if not SUPABASE_DISPONIVEL:
        st.warning(
            "🟡 Conexão com o banco de histórico (Supabase) não configurada.\n\n"
            "Configure `SUPABASE_URL` e `SUPABASE_KEY` em **Settings → Secrets** "
            "no Streamlit Cloud para habilitar esta aba."
        )
    else:
        # ── Comparar Períodos (datas customizadas, quantos quiser) ──────────
        st.markdown("### 🔄 Comparar Períodos")
        st.caption(
            "Compare intervalos de datas específicos — por exemplo, a mesma semana em "
            "anos diferentes (13/07 a 18/07 de 2022 x 13/07 a 18/07 de 2023). Use quantos "
            "períodos quiser. Os dados vêm do histórico detalhado salvo na aba "
            "**📤 Upload Mensal** (botão **Salvar dados detalhados**)."
        )

        pedidos_hist = carregar_pedidos_historico()

        if pedidos_hist.empty:
            st.info(
                "Nenhum dado detalhado salvo ainda. Vá para a aba **📤 Upload Mensal**, "
                "envie um CSV e clique em **Salvar dados detalhados** para habilitar "
                "a comparação por períodos."
            )
        else:
            dt_min = pedidos_hist["_dt"].min().date()
            dt_max = pedidos_hist["_dt"].max().date()

            if "periodos_comp" not in st.session_state:
                fim_default = dt_max
                ini_default = max(dt_min, fim_default - datetime.timedelta(days=6))
                st.session_state.periodos_comp = [
                    {"id": 1, "label": "Período 1", "inicio": ini_default, "fim": fim_default},
                    {"id": 2, "label": "Período 2", "inicio": ini_default, "fim": fim_default},
                ]
                st.session_state.next_periodo_id = 3

            st.markdown("#### Períodos selecionados")
            periodos = st.session_state.periodos_comp

            for p in periodos:
                c_label, c_ini, c_fim, c_rm = st.columns([2, 2, 2, 1])
                p["label"]  = c_label.text_input("Nome", value=p["label"], key=f"periodo_label_{p['id']}")
                p["inicio"] = c_ini.date_input("Início", value=p["inicio"], min_value=dt_min, max_value=dt_max, key=f"periodo_ini_{p['id']}")
                p["fim"]    = c_fim.date_input("Fim", value=p["fim"], min_value=dt_min, max_value=dt_max, key=f"periodo_fim_{p['id']}")
                c_rm.markdown("&nbsp;")
                if c_rm.button("🗑️", key=f"periodo_rm_{p['id']}") and len(periodos) > 1:
                    st.session_state.periodos_comp = [x for x in periodos if x["id"] != p["id"]]
                    st.rerun()

            if st.button("➕ Adicionar período"):
                novo_id = st.session_state.next_periodo_id
                st.session_state.next_periodo_id += 1
                ultimo = st.session_state.periodos_comp[-1]
                st.session_state.periodos_comp.append({
                    "id": novo_id, "label": f"Período {len(st.session_state.periodos_comp) + 1}",
                    "inicio": ultimo["inicio"], "fim": ultimo["fim"],
                })
                st.rerun()

            linhas = []
            detalhes_por_periodo = {}
            for p in st.session_state.periodos_comp:
                ini, fim = p["inicio"], p["fim"]
                if ini > fim:
                    st.warning(f"⚠️ Período '{p['label']}': a data de início é depois da data de fim — ignorado.")
                    continue
                sub = pedidos_hist[(pedidos_hist["_dt"].dt.date >= ini) & (pedidos_hist["_dt"].dt.date <= fim)]
                venda = sub["vlr_pedido"].sum()
                frete = sub["vlr_frete"].sum()
                peso  = sub["peso"].sum()
                linhas.append({
                    "Período":         p["label"],
                    "Intervalo":       f"{ini.strftime('%d/%m/%Y')} a {fim.strftime('%d/%m/%Y')}",
                    "Venda Total":     venda,
                    "Frete Total":     frete,
                    "Peso Total":      peso,
                    "Frete/Venda (%)": (frete / venda * 100) if venda > 0 else 0,
                    "R$/Kg":           (frete / peso) if peso > 0 else 0,
                    "Qtd Pedidos":     len(sub),
                })
                detalhes_por_periodo[p["label"]] = sub

            if not linhas:
                st.info("Configure ao menos um período válido para ver a comparação.")
            else:
                comp_periodos = pd.DataFrame(linhas)
                primeiro = comp_periodos.iloc[0]
                comp_periodos["Δ Frete vs 1º (%)"] = comp_periodos["Frete Total"].apply(
                    lambda v: (v - primeiro["Frete Total"]) / primeiro["Frete Total"] * 100
                    if primeiro["Frete Total"] > 0 else 0
                )

                tbl_periodos = comp_periodos.copy()
                tbl_periodos["Venda Total"]       = tbl_periodos["Venda Total"].apply(formata_moeda)
                tbl_periodos["Frete Total"]       = tbl_periodos["Frete Total"].apply(formata_moeda)
                tbl_periodos["Peso Total"]        = tbl_periodos["Peso Total"].apply(formata_kg)
                tbl_periodos["Frete/Venda (%)"]   = tbl_periodos["Frete/Venda (%)"].apply(formata_pct)
                tbl_periodos["R$/Kg"]             = tbl_periodos["R$/Kg"].apply(lambda v: f"R$ {v:.2f}")
                tbl_periodos["Δ Frete vs 1º (%)"] = tbl_periodos["Δ Frete vs 1º (%)"].apply(lambda v: f"{v:+.1f}%")
                st.dataframe(tbl_periodos, use_container_width=True, hide_index=True)

                col_p1, col_p2 = st.columns(2)
                with col_p1:
                    comp_melt = comp_periodos.melt(
                        id_vars=["Período"], value_vars=["Venda Total", "Frete Total"],
                        var_name="Indicador", value_name="Valor",
                    )
                    fig_periodos_valores = px.bar(
                        comp_melt, x="Período", y="Valor", color="Indicador", barmode="group",
                        title="Venda Total x Frete Total por Período",
                        labels={"Valor": "R$"},
                    )
                    st.plotly_chart(fig_periodos_valores, use_container_width=True)

                with col_p2:
                    fig_periodos_pct = px.bar(
                        comp_periodos, x="Período", y="Frete/Venda (%)",
                        title="% Frete/Venda por Período",
                        color="Frete/Venda (%)", color_continuous_scale="RdYlGn_r", text_auto=".1f",
                    )
                    fig_periodos_pct.update_layout(coloraxis_showscale=False)
                    st.plotly_chart(fig_periodos_pct, use_container_width=True)

                fig_periodos_kg = px.bar(
                    comp_periodos, x="Período", y="R$/Kg",
                    title="R$/Kg por Período",
                    color="R$/Kg", color_continuous_scale="Blues", text_auto=".2f",
                )
                fig_periodos_kg.update_layout(coloraxis_showscale=False)
                st.plotly_chart(fig_periodos_kg, use_container_width=True)

                with st.expander("📍 Ver comparativo por Estado dentro de cada período"):
                    linhas_uf = []
                    for label, sub in detalhes_por_periodo.items():
                        if sub.empty:
                            continue
                        agg = sub.groupby("uf_destino", as_index=False).agg(
                            total_frete=("vlr_frete", "sum"),
                            total_venda=("vlr_pedido", "sum"),
                        )
                        agg["Período"] = label
                        linhas_uf.append(agg)

                    if linhas_uf:
                        uf_comp = pd.concat(linhas_uf, ignore_index=True)
                        fig_uf_periodos = px.bar(
                            uf_comp, x="uf_destino", y="total_frete", color="Período", barmode="group",
                            title="Total de Frete por Estado, comparado entre Períodos",
                            labels={"uf_destino": "Estado", "total_frete": "Total Frete (R$)"},
                        )
                        st.plotly_chart(fig_uf_periodos, use_container_width=True)
                    else:
                        st.caption("Sem dados suficientes para detalhar por estado nos períodos selecionados.")

                    periodo_detalhe_sel = st.selectbox(
                        "Ver pedidos individuais de um período:",
                        list(detalhes_por_periodo.keys()), key="periodo_detalhe_sel"
                    )
                    sub_sel = detalhes_por_periodo[periodo_detalhe_sel]
                    if not sub_sel.empty:
                        tbl_sel = sub_sel[[
                            "cliente", "transportadora", "cidade_origem", "cidade_destino",
                            "vlr_pedido", "peso", "vlr_frete"
                        ]].copy()
                        tbl_sel.columns = ["Cliente", "Transportadora", "Origem", "Destino",
                                            "Venda (R$)", "Peso (Kg)", "Frete (R$)"]
                        tbl_sel["Frete/Venda (%)"] = tbl_sel.apply(
                            lambda r: r["Frete (R$)"] / r["Venda (R$)"] * 100 if r["Venda (R$)"] > 0 else 0, axis=1
                        ).apply(formata_pct)
                        tbl_sel["R$/Kg"] = tbl_sel.apply(
                            lambda r: r["Frete (R$)"] / r["Peso (Kg)"] if r["Peso (Kg)"] > 0 else 0, axis=1
                        ).apply(lambda v: f"R$ {v:.2f}")
                        tbl_sel["Venda (R$)"] = tbl_sel["Venda (R$)"].apply(formata_moeda)
                        tbl_sel["Frete (R$)"] = tbl_sel["Frete (R$)"].apply(formata_moeda)
                        tbl_sel["Peso (Kg)"]  = tbl_sel["Peso (Kg)"].apply(formata_kg)
                        st.dataframe(tbl_sel, use_container_width=True, hide_index=True)
                    else:
                        st.caption("Nenhum pedido neste período.")

# ─── Footer ───────────────────────────────────────────────────────────────────
st.markdown("---")
if arquivo is not None:
    st.caption("🚚 Frete Analytics • Arquivo carregado: " + arquivo.name)
else:
    st.caption("🚚 Frete Analytics")
