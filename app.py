import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ─── Configuração da Página ──────────────────────────────────────────────────
st.set_page_config(
    page_title="Análise de Logística e Frete",
    page_icon="🚚",
    layout="wide",
)

# ─── Mapeamento de Colunas ───────────────────────────────────────────────────
COLS = {
    "cliente":        "NF: Cliente Nome",
    "transportadora": "NF: Transportadora",
    "uf_destino":     "NF: Até (UF)",
    "cidade_destino": "NF: Até (Cidade)",
    "cidade_origem":  "NF: De (Cidade)",
    "vlr_pedido":     "NF: R$ Total",
    "peso":           "NF: Peso Bruto Kg",
    "vlr_frete":      "DT: R$ Total Cobrado",
    "tipo_frete":     "NF: CIF/FOB",
}

# ─── Funções Auxiliares ──────────────────────────────────────────────────────
def formata_moeda(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def formata_pct(valor):
    return f"{valor:.2f}%"

def formata_kg(valor):
    return f"{valor:,.1f} kg".replace(",", "X").replace(".", ",").replace("X", ".")

def detalhe_pedidos(df_subset, titulo):
    """Exibe expander com os pedidos individuais que compõem um total."""
    with st.expander(f"🔎 Ver pedidos individuais — {titulo}"):
        cols_exibir = [
            C["cliente"], C["transportadora"],
            C["cidade_origem"], C["cidade_destino"],
            C["vlr_pedido"], C["peso"], C["vlr_frete"], C["tipo_frete"],
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
            C["tipo_frete"]:     "Tipo",
        }
        tbl = tbl.rename(columns=rename)
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
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        elif alias in ("uf_destino", "cidade_destino", "cidade_origem",
                       "cliente", "transportadora", "tipo_frete"):
            df[col] = df[col].astype(str).str.strip()

    return df


def validar_colunas(df: pd.DataFrame) -> list[str]:
    return [c for c in COLS.values() if c not in df.columns]


# ─── Sidebar ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/2590/2590584.png", width=64)
    st.title("🚚 Frete Analytics")
    st.markdown("---")
    arquivo = st.file_uploader("📂 Upload do CSV mensal", type=["csv"])
    st.markdown("---")
    st.caption("Envie o arquivo CSV exportado do seu sistema. O app processa tudo automaticamente.")

# ─── Estado sem arquivo ───────────────────────────────────────────────────────
if arquivo is None:
    st.markdown(
        """
        <div style='text-align:center; padding: 80px 0;'>
            <h1>📦 Análise de Logística & Eficiência de Fretes</h1>
            <p style='font-size:18px; color:#888;'>
                Faça o upload do seu arquivo CSV mensal na barra lateral para começar.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.stop()

# ─── Carrega e valida ─────────────────────────────────────────────────────────
df = carregar_dados(arquivo)
faltando = validar_colunas(df)
if faltando:
    st.error(f"⚠️ Colunas não encontradas no CSV:\n\n{faltando}\n\nVerifique se o arquivo está correto.")
    st.stop()

C = COLS

# ─── Header / KPIs globais ────────────────────────────────────────────────────
total_pedidos = df[C["vlr_pedido"]].sum()
total_frete   = df[C["vlr_frete"]].sum()
total_peso    = df[C["peso"]].sum()
pct_global    = (total_frete / total_pedidos * 100) if total_pedidos > 0 else 0

st.markdown("## 🚚 Análise de Logística & Eficiência de Fretes")
col1, col2, col3, col4 = st.columns(4)
col1.metric("💰 Total de Vendas",  formata_moeda(total_pedidos))
col2.metric("🚛 Total de Fretes",  formata_moeda(total_frete))
col3.metric("⚖️ Peso Total",       formata_kg(total_peso))
col4.metric("📊 Frete / Venda",    formata_pct(pct_global))

st.markdown("---")

# ═══════════════════════════════════════════════════════════════════════════════
# PARTE 1 — VISÃO POR ESTADO
# ═══════════════════════════════════════════════════════════════════════════════
st.markdown("## 📍 Parte 1 — Visão por Estado")

agg_uf = (
    df.groupby(C["uf_destino"], as_index=False)
    .agg(
        total_vendas=(C["vlr_pedido"], "sum"),
        total_frete= (C["vlr_frete"],  "sum"),
        total_peso=  (C["peso"],        "sum"),
        qtd_pedidos= (C["vlr_pedido"], "count"),
    )
)

# ✅ CORRIGIDO: R$/Kg = Total Frete / Peso Total
agg_uf["rs_por_kg"] = agg_uf.apply(
    lambda r: r["total_frete"] / r["total_peso"] if r["total_peso"] > 0 else 0, axis=1
)
agg_uf["pct_frete"] = agg_uf.apply(
    lambda r: r["total_frete"] / r["total_vendas"] * 100 if r["total_vendas"] > 0 else 0, axis=1
)

agg_uf = agg_uf.sort_values("pct_frete", ascending=False).reset_index(drop=True)

# ── Tabela por UF ─────────────────────────────────────────────────────────────
st.markdown("### 📋 Tabela Consolidada por Estado")

display_uf = agg_uf.copy()
display_uf.columns = [
    "Estado", "Total Vendas (R$)", "Total Frete (R$)",
    "Peso Total (Kg)", "Qtd Pedidos", "R$/Kg (Frete/Peso)", "Frete/Venda (%)"
]
for col_m in ["Total Vendas (R$)", "Total Frete (R$)"]:
    display_uf[col_m] = display_uf[col_m].apply(formata_moeda)
display_uf["Peso Total (Kg)"]       = display_uf["Peso Total (Kg)"].apply(formata_kg)
display_uf["R$/Kg (Frete/Peso)"]    = display_uf["R$/Kg (Frete/Peso)"].apply(lambda v: f"R$ {v:.2f}")
display_uf["Frete/Venda (%)"]       = display_uf["Frete/Venda (%)"].apply(formata_pct)

st.dataframe(display_uf, use_container_width=True, hide_index=True)

# Detalhamento por estado — pedidos individuais
ufs_disponiveis = sorted(agg_uf[C["uf_destino"]].tolist())
uf_detalhe_p1 = st.selectbox("Ver pedidos de qual Estado?", ufs_disponiveis, key="uf_detalhe_p1")
detalhe_pedidos(df[df[C["uf_destino"]] == uf_detalhe_p1], f"Estado {uf_detalhe_p1}")

# ── Gráficos por UF ──────────────────────────────────────────────────────────
col_g1, col_g2 = st.columns(2)

with col_g1:
    fig_pct = px.bar(
        agg_uf, x=C["uf_destino"], y="pct_frete",
        title="% Frete sobre Venda por Estado",
        labels={C["uf_destino"]: "Estado", "pct_frete": "Frete/Venda (%)"},
        color="pct_frete",
        color_continuous_scale="RdYlGn_r",
        text_auto=".1f",
    )
    fig_pct.update_layout(showlegend=False, coloraxis_showscale=False)
    st.plotly_chart(fig_pct, use_container_width=True)

with col_g2:
    fig_abs = px.bar(
        agg_uf.sort_values("total_frete", ascending=False),
        x=C["uf_destino"], y="total_frete",
        title="Total de Frete Absoluto por Estado (R$)",
        labels={C["uf_destino"]: "Estado", "total_frete": "Total Frete (R$)"},
        color="total_frete",
        color_continuous_scale="Blues",
        text_auto=".2s",
    )
    fig_abs.update_layout(showlegend=False, coloraxis_showscale=False)
    st.plotly_chart(fig_abs, use_container_width=True)

# ── Clientes e Transportadoras por UF ────────────────────────────────────────
st.markdown("### 🔍 Clientes e Transportadoras por Estado")
uf_selecionada_p1 = st.selectbox("Selecione um Estado:", ufs_disponiveis, key="uf_p1")
df_uf_p1 = df[df[C["uf_destino"]] == uf_selecionada_p1]

col_cli, col_transp = st.columns(2)

with col_cli:
    st.markdown(f"**👤 Clientes em {uf_selecionada_p1}**")
    clientes_uf = (
        df_uf_p1.groupby(C["cliente"], as_index=False)
        .agg(
            total_vendas=(C["vlr_pedido"], "sum"),
            total_frete= (C["vlr_frete"],  "sum"),
            qtd_pedidos= (C["vlr_pedido"], "count"),
        )
        .sort_values("total_frete", ascending=False)
    )
    clientes_uf["pct_frete"] = clientes_uf.apply(
        lambda r: r["total_frete"] / r["total_vendas"] * 100 if r["total_vendas"] > 0 else 0, axis=1
    )
    tbl_cli_uf = clientes_uf.copy()
    tbl_cli_uf.columns = ["Cliente", "Total Vendas (R$)", "Total Frete (R$)", "Qtd Pedidos", "Frete/Venda (%)"]
    tbl_cli_uf["Total Vendas (R$)"] = tbl_cli_uf["Total Vendas (R$)"].apply(formata_moeda)
    tbl_cli_uf["Total Frete (R$)"]  = tbl_cli_uf["Total Frete (R$)"].apply(formata_moeda)
    tbl_cli_uf["Frete/Venda (%)"]   = tbl_cli_uf["Frete/Venda (%)"].apply(formata_pct)
    st.dataframe(tbl_cli_uf, use_container_width=True, hide_index=True)

    # Detalhamento por cliente dentro do estado
    clientes_lista = clientes_uf[C["cliente"]].tolist()
    cli_sel = st.selectbox("Ver pedidos do cliente:", clientes_lista, key="cli_sel_p1")
    detalhe_pedidos(
        df_uf_p1[df_uf_p1[C["cliente"]] == cli_sel],
        f"{cli_sel} em {uf_selecionada_p1}"
    )

with col_transp:
    st.markdown(f"**🚛 Transportadoras em {uf_selecionada_p1}**")
    transp_uf = (
        df_uf_p1.groupby(C["transportadora"], as_index=False)
        .agg(
            total_frete= (C["vlr_frete"], "sum"),
            total_peso=  (C["peso"],      "sum"),
            qtd_pedidos= (C["vlr_frete"], "count"),
        )
        .sort_values("total_frete", ascending=False)
    )
    transp_uf["rs_por_kg"] = transp_uf.apply(
        lambda r: r["total_frete"] / r["total_peso"] if r["total_peso"] > 0 else 0, axis=1
    )
    tbl_transp_uf = transp_uf.copy()
    tbl_transp_uf.columns = ["Transportadora", "Total Frete (R$)", "Peso Total (Kg)", "Qtd Pedidos", "R$/Kg"]
    tbl_transp_uf["Total Frete (R$)"] = tbl_transp_uf["Total Frete (R$)"].apply(formata_moeda)
    tbl_transp_uf["Peso Total (Kg)"]  = tbl_transp_uf["Peso Total (Kg)"].apply(formata_kg)
    tbl_transp_uf["R$/Kg"]            = tbl_transp_uf["R$/Kg"].apply(lambda v: f"R$ {v:.2f}")
    st.dataframe(tbl_transp_uf, use_container_width=True, hide_index=True)

    # Detalhamento por transportadora dentro do estado
    transp_lista = transp_uf[C["transportadora"]].tolist()
    transp_sel = st.selectbox("Ver pedidos da transportadora:", transp_lista, key="transp_sel_p1")
    detalhe_pedidos(
        df_uf_p1[df_uf_p1[C["transportadora"]] == transp_sel],
        f"{transp_sel} em {uf_selecionada_p1}"
    )

st.markdown("---")

# ═══════════════════════════════════════════════════════════════════════════════
# PARTE 2 — ANÁLISE DE DEFICIÊNCIA
# ═══════════════════════════════════════════════════════════════════════════════
st.markdown("## 🔬 Parte 2 — Análise de Deficiência (Deep Dive)")

uf_deep = st.selectbox(
    "Selecione o Estado para investigar:",
    ufs_disponiveis,
    index=0,
    key="uf_deep",
)

df_deep = df[df[C["uf_destino"]] == uf_deep].copy()

if df_deep.empty:
    st.warning("Nenhum dado para o estado selecionado.")
    st.stop()

# ── 2.1 Maiores Gastos por Cliente ───────────────────────────────────────────
st.markdown(f"### 👤 2.1 — Maiores Gastos por Cliente em **{uf_deep}**")

cliente_agg = (
    df_deep.groupby(C["cliente"], as_index=False)
    .agg(
        total_venda=(C["vlr_pedido"], "sum"),
        total_frete=(C["vlr_frete"],  "sum"),
        qtd_pedidos=(C["vlr_pedido"], "count"),
    )
)
cliente_agg["pct_frete"] = cliente_agg.apply(
    lambda r: r["total_frete"] / r["total_venda"] * 100 if r["total_venda"] > 0 else 0, axis=1
)
cliente_agg = cliente_agg.sort_values("total_frete", ascending=False)

col_c1, col_c2 = st.columns(2)

with col_c1:
    fig_cli_abs = px.bar(
        cliente_agg.head(15),
        x="total_frete", y=C["cliente"],
        orientation="h",
        title=f"Top 15 Clientes — Frete Absoluto (R$) em {uf_deep}",
        labels={"total_frete": "Total Frete (R$)", C["cliente"]: "Cliente"},
        color="total_frete",
        color_continuous_scale="Reds",
    )
    fig_cli_abs.update_layout(yaxis={"categoryorder": "total ascending"}, coloraxis_showscale=False)
    st.plotly_chart(fig_cli_abs, use_container_width=True)

with col_c2:
    fig_cli_pct = px.bar(
        cliente_agg.sort_values("pct_frete", ascending=False).head(15),
        x="pct_frete", y=C["cliente"],
        orientation="h",
        title=f"Top 15 Clientes — % Frete/Venda em {uf_deep}",
        labels={"pct_frete": "Frete/Venda (%)", C["cliente"]: "Cliente"},
        color="pct_frete",
        color_continuous_scale="OrRd",
    )
    fig_cli_pct.update_layout(yaxis={"categoryorder": "total ascending"}, coloraxis_showscale=False)
    st.plotly_chart(fig_cli_pct, use_container_width=True)

# Tabela + detalhamento por cliente
with st.expander("📄 Ver tabela completa por cliente"):
    tbl_cli = cliente_agg.copy()
    tbl_cli.columns = ["Cliente", "Total Venda (R$)", "Total Frete (R$)", "Qtd Pedidos", "Frete/Venda (%)"]
    tbl_cli["Total Venda (R$)"] = tbl_cli["Total Venda (R$)"].apply(formata_moeda)
    tbl_cli["Total Frete (R$)"] = tbl_cli["Total Frete (R$)"].apply(formata_moeda)
    tbl_cli["Frete/Venda (%)"]  = tbl_cli["Frete/Venda (%)"].apply(formata_pct)
    st.dataframe(tbl_cli, use_container_width=True, hide_index=True)

# Seletor de cliente para ver pedidos individuais
clientes_deep = cliente_agg[C["cliente"]].tolist()
cli_deep_sel = st.selectbox("Ver pedidos individuais do cliente:", clientes_deep, key="cli_deep_sel")
detalhe_pedidos(
    df_deep[df_deep[C["cliente"]] == cli_deep_sel],
    f"{cli_deep_sel} em {uf_deep}"
)

# ── 2.2 Comparativo de Transportadoras por Rota ──────────────────────────────
st.markdown(f"### 🔀 2.2 — Comparativo de Transportadoras por Rota em **{uf_deep}**")
st.caption("Isole uma rota idêntica (mesma origem → destino) e compare o R$/Kg de cada transportadora.")

df_deep["_rota"] = df_deep[C["cidade_origem"]] + " → " + df_deep[C["cidade_destino"]]
rotas = sorted(df_deep["_rota"].unique().tolist())

if not rotas:
    st.info("Nenhuma rota disponível para o estado selecionado.")
else:
    rota_sel = st.selectbox("Selecione a Rota:", rotas, key="rota_sel")
    df_rota = df_deep[df_deep["_rota"] == rota_sel].copy()
    df_rota = df_rota[df_rota[C["peso"]] > 0]

    if df_rota.empty:
        st.warning("Nenhum registro com peso válido para esta rota.")
    else:
        rota_agg = (
            df_rota.groupby(C["transportadora"], as_index=False)
            .agg(
                total_frete=        (C["vlr_frete"], "sum"),
                total_peso=         (C["peso"],       "sum"),
                qtd_embarques=      (C["vlr_frete"], "count"),
                ticket_medio_frete= (C["vlr_frete"], "mean"),
            )
        )
        rota_agg["rs_por_kg"] = rota_agg.apply(
            lambda r: r["total_frete"] / r["total_peso"] if r["total_peso"] > 0 else 0, axis=1
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
            rota_agg,
            x=C["transportadora"], y="rs_por_kg",
            title=f"R$/Kg por Transportadora — Rota: {rota_sel}",
            labels={C["transportadora"]: "Transportadora", "rs_por_kg": "R$/Kg"},
            color="rs_por_kg",
            color_continuous_scale="RdYlGn_r",
            text_auto=".3f",
        )
        fig_rota.update_layout(coloraxis_showscale=False)
        st.plotly_chart(fig_rota, use_container_width=True)

        # Tabela consolidada por transportadora na rota
        tbl_rota = rota_agg.copy()
        tbl_rota.columns = [
            "Transportadora", "Total Frete (R$)", "Peso Total (Kg)",
            "Qtd Embarques", "Ticket Médio (R$)", "R$/Kg"
        ]
        tbl_rota["Total Frete (R$)"]  = tbl_rota["Total Frete (R$)"].apply(formata_moeda)
        tbl_rota["Peso Total (Kg)"]   = tbl_rota["Peso Total (Kg)"].apply(formata_kg)
        tbl_rota["Ticket Médio (R$)"] = tbl_rota["Ticket Médio (R$)"].apply(formata_moeda)
        tbl_rota["R$/Kg"]             = tbl_rota["R$/Kg"].apply(lambda v: f"R$ {v:.4f}")
        st.dataframe(tbl_rota, use_container_width=True, hide_index=True)

        # Detalhamento: pedidos individuais por transportadora na rota
        transp_rota_lista = rota_agg[C["transportadora"]].tolist()
        transp_rota_sel = st.selectbox(
            "Ver embarques individuais da transportadora:", transp_rota_lista, key="transp_rota_sel"
        )
        detalhe_pedidos(
            df_rota[df_rota[C["transportadora"]] == transp_rota_sel],
            f"{transp_rota_sel} — {rota_sel}"
        )

        # Dispersão geral de todos os embarques na rota
        with st.expander("📊 Ver dispersão de todos os embarques nesta rota"):
            df_rota["rs_kg_ind"] = df_rota[C["vlr_frete"]] / df_rota[C["peso"]]
            fig_scatter = px.strip(
                df_rota,
                x=C["transportadora"],
                y="rs_kg_ind",
                color=C["transportadora"],
                title="Distribuição de R$/Kg por Embarque e Transportadora",
                labels={
                    C["transportadora"]: "Transportadora",
                    "rs_kg_ind": "R$/Kg por Embarque",
                },
                hover_data=[C["cliente"], C["vlr_frete"], C["peso"]],
            )
            st.plotly_chart(fig_scatter, use_container_width=True)

# ─── Footer ───────────────────────────────────────────────────────────────────
st.markdown("---")
st.caption("🚚 Frete Analytics • Atualizado com o arquivo: " + arquivo.name)
