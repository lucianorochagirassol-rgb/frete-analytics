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
    "vlr_frete":      "DT: R$ Total Cobrado",
    "tipo_frete":     "NF: CIF/FOB",
}
C = COLS

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

# ─── Conexão Supabase (histórico mensal) ─────────────────────────────────────
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

TABELA_HISTORICO = "fretes_mensais"


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
    """Busca todo o histórico salvo no banco. Cache de 5 minutos."""
    if not SUPABASE_DISPONIVEL:
        return pd.DataFrame()
    client = get_supabase_client()
    resp = client.table(TABELA_HISTORICO).select("*").order("mes").execute()
    return pd.DataFrame(resp.data)


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


MESES_PT = [
    "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]

# ─── Sidebar ─────────────────────────────────────────────────────────────────
with st.sidebar:
    if LOGO_DISPONIVEL:
        st.image(LOGO_PATH, width=160)
    else:
        st.image("https://cdn-icons-png.flaticon.com/512/2590/2590584.png", width=64)
    st.title("🚚 Frete Analytics")
    st.markdown("---")
    arquivo = st.file_uploader("📂 Upload do CSV mensal", type=["csv"])

    st.markdown("### 📅 Competência do arquivo")
    hoje = datetime.date.today()
    col_mes, col_ano = st.columns(2)
    mes_nome = col_mes.selectbox("Mês", MESES_PT, index=hoje.month - 1)
    ano_sel = col_ano.number_input("Ano", min_value=2020, max_value=2100, value=hoje.year, step=1)
    mes_competencia = f"{ano_sel}-{MESES_PT.index(mes_nome) + 1:02d}"

    st.markdown("---")
    if SUPABASE_DISPONIVEL:
        st.success("🟢 Histórico conectado")
    else:
        st.warning("🟡 Histórico desconectado\n\nConfigure SUPABASE_URL e SUPABASE_KEY em Secrets para habilitar.")
    st.caption("Envie o arquivo CSV exportado do seu sistema todo mês. O app processa tudo automaticamente.")

# ─── Carrega e valida (se houver upload) ─────────────────────────────────────
df = None
faltando = []
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

st.markdown("## 🚚 Análise de Logística & Eficiência de Fretes")

if df is not None:
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

tab1, tab2, tab3 = st.tabs([
    "📍 Visão por Estado",
    "🔬 Análise de Deficiência",
    "📊 Comparação Mensal",
])

# ═══════════════════════════════════════════════════════════════════════════════
# ABA 1 — VISÃO POR ESTADO
# ═══════════════════════════════════════════════════════════════════════════════
with tab1:
    if df is None:
        st.info("📂 Faça upload do CSV mensal na barra lateral para ver esta análise.")
    else:
        agg_uf = (
            df.groupby(C["uf_destino"], as_index=False)
            .agg(
                total_vendas=(C["vlr_pedido"], "sum"),
                total_frete= (C["vlr_frete"],  "sum"),
                total_peso=  (C["peso"],        "sum"),
                qtd_pedidos= (C["vlr_pedido"], "count"),
            )
        )
        # R$/Kg = Total Frete / Peso Total
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
        uf_detalhe_p1 = st.selectbox("Ver pedidos de qual Estado?", ufs_disponiveis, key="uf_detalhe_p1")
        detalhe_pedidos(df[df[C["uf_destino"]] == uf_detalhe_p1], f"Estado {uf_detalhe_p1}")

        # Botão para salvar no histórico
        st.markdown("### 💾 Salvar no Histórico Mensal")
        col_save1, col_save2 = st.columns([3, 1])
        col_save1.caption(
            f"Salva os totais agregados por estado para a competência **{mes_competencia}** "
            f"no banco de histórico. Se já existir um registro para este mês/estado, ele será substituído."
        )
        if col_save2.button("💾 Salvar agora", type="primary", disabled=not SUPABASE_DISPONIVEL):
            try:
                salvar_historico_mensal(mes_competencia, agg_uf)
                carregar_historico_mensal.clear()
                st.success(f"✅ Dados de {mes_competencia} salvos no histórico!")
            except Exception as e:
                st.error(f"Erro ao salvar no histórico: {e}")
        if not SUPABASE_DISPONIVEL:
            st.caption("⚠️ Conecte o Supabase (Secrets) para habilitar o salvamento.")

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
            st.plotly_chart(fig_pct, use_container_width=True)

        with col_g2:
            fig_abs = px.bar(
                agg_uf.sort_values("total_frete", ascending=False),
                x=C["uf_destino"], y="total_frete",
                title="Total de Frete Absoluto por Estado (R$)",
                labels={C["uf_destino"]: "Estado", "total_frete": "Total Frete (R$)"},
                color="total_frete", color_continuous_scale="Blues", text_auto=".2s",
            )
            fig_abs.update_layout(showlegend=False, coloraxis_showscale=False)
            st.plotly_chart(fig_abs, use_container_width=True)

        # Clientes e Transportadoras por UF
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

            clientes_lista = clientes_uf[C["cliente"]].tolist()
            cli_sel = st.selectbox("Ver pedidos do cliente:", clientes_lista, key="cli_sel_p1")
            detalhe_pedidos(df_uf_p1[df_uf_p1[C["cliente"]] == cli_sel], f"{cli_sel} em {uf_selecionada_p1}")

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

            transp_lista = transp_uf[C["transportadora"]].tolist()
            transp_sel = st.selectbox("Ver pedidos da transportadora:", transp_lista, key="transp_sel_p1")
            detalhe_pedidos(df_uf_p1[df_uf_p1[C["transportadora"]] == transp_sel], f"{transp_sel} em {uf_selecionada_p1}")

# ═══════════════════════════════════════════════════════════════════════════════
# ABA 2 — ANÁLISE DE DEFICIÊNCIA
# ═══════════════════════════════════════════════════════════════════════════════
with tab2:
    if df is None:
        st.info("📂 Faça upload do CSV mensal na barra lateral para ver esta análise.")
    else:
        ufs_disponiveis_p2 = sorted(df[C["uf_destino"]].unique().tolist())
        uf_deep = st.selectbox("Selecione o Estado para investigar:", ufs_disponiveis_p2, index=0, key="uf_deep")
        df_deep = df[df[C["uf_destino"]] == uf_deep].copy()

        if df_deep.empty:
            st.warning("Nenhum dado para o estado selecionado.")
        else:
            st.markdown(f"### 👤 Maiores Gastos por Cliente em **{uf_deep}**")
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
                tbl_cli.columns = ["Cliente", "Total Venda (R$)", "Total Frete (R$)", "Qtd Pedidos", "Frete/Venda (%)"]
                tbl_cli["Total Venda (R$)"] = tbl_cli["Total Venda (R$)"].apply(formata_moeda)
                tbl_cli["Total Frete (R$)"] = tbl_cli["Total Frete (R$)"].apply(formata_moeda)
                tbl_cli["Frete/Venda (%)"]  = tbl_cli["Frete/Venda (%)"].apply(formata_pct)
                st.dataframe(tbl_cli, use_container_width=True, hide_index=True)

            clientes_deep = cliente_agg[C["cliente"]].tolist()
            cli_deep_sel = st.selectbox("Ver pedidos individuais do cliente:", clientes_deep, key="cli_deep_sel")
            detalhe_pedidos(df_deep[df_deep[C["cliente"]] == cli_deep_sel], f"{cli_deep_sel} em {uf_deep}")

            st.markdown(f"### 🔀 Comparativo de Transportadoras por Rota em **{uf_deep}**")
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
                        rota_agg, x=C["transportadora"], y="rs_por_kg",
                        title=f"R$/Kg por Transportadora — Rota: {rota_sel}",
                        labels={C["transportadora"]: "Transportadora", "rs_por_kg": "R$/Kg"},
                        color="rs_por_kg", color_continuous_scale="RdYlGn_r", text_auto=".3f",
                    )
                    fig_rota.update_layout(coloraxis_showscale=False)
                    st.plotly_chart(fig_rota, use_container_width=True)

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

                    transp_rota_lista = rota_agg[C["transportadora"]].tolist()
                    transp_rota_sel = st.selectbox(
                        "Ver embarques individuais da transportadora:", transp_rota_lista, key="transp_rota_sel"
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
# ABA 3 — COMPARAÇÃO MENSAL (histórico no banco)
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
        historico = carregar_historico_mensal()

        if historico.empty:
            st.info(
                "Nenhum histórico salvo ainda. Faça upload de um CSV mensal na aba "
                "**Visão por Estado** e clique em **Salvar agora** para começar a registrar."
            )
        else:
            historico = historico.sort_values(["mes", "estado"]).reset_index(drop=True)
            meses_disponiveis = sorted(historico["mes"].unique().tolist())
            estados_disponiveis_hist = sorted(historico["estado"].unique().tolist())

            st.markdown("### 🗂️ Histórico Completo")
            tbl_hist = historico.copy()
            tbl_hist = tbl_hist[["mes", "estado", "venda_total", "frete_total",
                                  "peso_total", "frete_sobre_venda", "custo_por_kg"]]
            tbl_hist.columns = ["Mês", "Estado", "Venda Total (R$)", "Frete Total (R$)",
                                 "Peso Total (Kg)", "Frete/Venda (%)", "R$/Kg"]
            tbl_hist["Venda Total (R$)"] = tbl_hist["Venda Total (R$)"].apply(formata_moeda)
            tbl_hist["Frete Total (R$)"] = tbl_hist["Frete Total (R$)"].apply(formata_moeda)
            tbl_hist["Peso Total (Kg)"]  = tbl_hist["Peso Total (Kg)"].apply(formata_kg)
            tbl_hist["Frete/Venda (%)"]  = tbl_hist["Frete/Venda (%)"].apply(formata_pct)
            tbl_hist["R$/Kg"]            = tbl_hist["R$/Kg"].apply(lambda v: f"R$ {v:.2f}")
            st.dataframe(tbl_hist, use_container_width=True, hide_index=True)

            st.markdown("### 🏢 Total da Empresa por Mês")
            st.caption("Soma de todos os estados em cada competência — visão consolidada da empresa.")
            total_empresa = (
                historico.groupby("mes", as_index=False)
                .agg(
                    venda_total=("venda_total", "sum"),
                    frete_total=("frete_total", "sum"),
                    peso_total=("peso_total", "sum"),
                )
                .sort_values("mes")
            )
            total_empresa["frete_sobre_venda"] = total_empresa.apply(
                lambda r: r["frete_total"] / r["venda_total"] * 100 if r["venda_total"] > 0 else 0, axis=1
            )
            total_empresa["custo_por_kg"] = total_empresa.apply(
                lambda r: r["frete_total"] / r["peso_total"] if r["peso_total"] > 0 else 0, axis=1
            )

            tbl_total_empresa = total_empresa.copy()
            tbl_total_empresa.columns = [
                "Mês", "Venda Total (R$)", "Frete Total (R$)",
                "Peso Total (Kg)", "Frete/Venda (%)", "R$/Kg"
            ]
            tbl_total_empresa["Venda Total (R$)"] = tbl_total_empresa["Venda Total (R$)"].apply(formata_moeda)
            tbl_total_empresa["Frete Total (R$)"] = tbl_total_empresa["Frete Total (R$)"].apply(formata_moeda)
            tbl_total_empresa["Peso Total (Kg)"]  = tbl_total_empresa["Peso Total (Kg)"].apply(formata_kg)
            tbl_total_empresa["Frete/Venda (%)"]  = tbl_total_empresa["Frete/Venda (%)"].apply(formata_pct)
            tbl_total_empresa["R$/Kg"]            = tbl_total_empresa["R$/Kg"].apply(lambda v: f"R$ {v:.2f}")
            st.dataframe(tbl_total_empresa, use_container_width=True, hide_index=True)

            col_te1, col_te2 = st.columns(2)
            with col_te1:
                fig_total_frete = px.line(
                    total_empresa, x="mes", y="frete_total", markers=True,
                    title="Evolução do Frete Total da Empresa",
                    labels={"mes": "Mês", "frete_total": "Total Frete (R$)"},
                )
                st.plotly_chart(fig_total_frete, use_container_width=True)
            with col_te2:
                fig_total_pct = px.line(
                    total_empresa, x="mes", y="frete_sobre_venda", markers=True,
                    title="Evolução do % Frete/Venda da Empresa",
                    labels={"mes": "Mês", "frete_sobre_venda": "Frete/Venda (%)"},
                )
                st.plotly_chart(fig_total_pct, use_container_width=True)

            st.markdown("### 📈 Evolução dos Indicadores por Estado")
            default_estados = estados_disponiveis_hist[:5] if len(estados_disponiveis_hist) > 5 else estados_disponiveis_hist
            estados_sel = st.multiselect(
                "Selecione os estados para comparar:", estados_disponiveis_hist, default=default_estados
            )
            hist_filtrado = historico[historico["estado"].isin(estados_sel)]

            if hist_filtrado.empty:
                st.info("Selecione ao menos um estado para ver a evolução.")
            else:
                fig_eve_frete = px.line(
                    hist_filtrado, x="mes", y="frete_total", color="estado", markers=True,
                    title="Evolução do Frete Total por Estado",
                    labels={"mes": "Mês", "frete_total": "Total Frete (R$)", "estado": "Estado"},
                )
                st.plotly_chart(fig_eve_frete, use_container_width=True)

                fig_eve_pct = px.line(
                    hist_filtrado, x="mes", y="frete_sobre_venda", color="estado", markers=True,
                    title="Evolução do % Frete/Venda por Estado",
                    labels={"mes": "Mês", "frete_sobre_venda": "Frete/Venda (%)", "estado": "Estado"},
                )
                st.plotly_chart(fig_eve_pct, use_container_width=True)

                fig_eve_kg = px.line(
                    hist_filtrado, x="mes", y="custo_por_kg", color="estado", markers=True,
                    title="Evolução do R$/Kg por Estado",
                    labels={"mes": "Mês", "custo_por_kg": "R$/Kg", "estado": "Estado"},
                )
                st.plotly_chart(fig_eve_kg, use_container_width=True)

            st.markdown("### 🔄 Comparar Dois Meses")
            if len(meses_disponiveis) < 2:
                st.info("É preciso ter ao menos 2 meses salvos no histórico para fazer esta comparação.")
            else:
                col_ma, col_mb = st.columns(2)
                mes_a = col_ma.selectbox("Mês A (base)", meses_disponiveis, index=len(meses_disponiveis) - 2, key="mes_a")
                mes_b = col_mb.selectbox("Mês B (comparação)", meses_disponiveis, index=len(meses_disponiveis) - 1, key="mes_b")

                df_a = historico[historico["mes"] == mes_a].set_index("estado")
                df_b = historico[historico["mes"] == mes_b].set_index("estado")
                estados_comuns = sorted(set(df_a.index) & set(df_b.index))

                if not estados_comuns:
                    st.info("Não há estados em comum entre os meses selecionados.")
                else:
                    comp = pd.DataFrame({"Estado": estados_comuns})
                    comp["Frete A"]  = [df_a.loc[e, "frete_total"] for e in estados_comuns]
                    comp["Frete B"]  = [df_b.loc[e, "frete_total"] for e in estados_comuns]
                    comp["Δ Frete (%)"] = comp.apply(
                        lambda r: (r["Frete B"] - r["Frete A"]) / r["Frete A"] * 100 if r["Frete A"] > 0 else 0, axis=1
                    )
                    comp["Frete/Venda A (%)"] = [df_a.loc[e, "frete_sobre_venda"] for e in estados_comuns]
                    comp["Frete/Venda B (%)"] = [df_b.loc[e, "frete_sobre_venda"] for e in estados_comuns]
                    comp["Δ Frete/Venda (p.p.)"] = comp["Frete/Venda B (%)"] - comp["Frete/Venda A (%)"]
                    comp["R$/Kg A"] = [df_a.loc[e, "custo_por_kg"] for e in estados_comuns]
                    comp["R$/Kg B"] = [df_b.loc[e, "custo_por_kg"] for e in estados_comuns]
                    comp["Δ R$/Kg (%)"] = comp.apply(
                        lambda r: (r["R$/Kg B"] - r["R$/Kg A"]) / r["R$/Kg A"] * 100 if r["R$/Kg A"] > 0 else 0, axis=1
                    )

                    comp_display = comp.copy()
                    comp_display["Frete A"]  = comp_display["Frete A"].apply(formata_moeda)
                    comp_display["Frete B"]  = comp_display["Frete B"].apply(formata_moeda)
                    comp_display["Δ Frete (%)"] = comp_display["Δ Frete (%)"].apply(lambda v: f"{v:+.1f}%")
                    comp_display["Frete/Venda A (%)"] = comp_display["Frete/Venda A (%)"].apply(formata_pct)
                    comp_display["Frete/Venda B (%)"] = comp_display["Frete/Venda B (%)"].apply(formata_pct)
                    comp_display["Δ Frete/Venda (p.p.)"] = comp_display["Δ Frete/Venda (p.p.)"].apply(lambda v: f"{v:+.2f} p.p.")
                    comp_display["R$/Kg A"] = comp_display["R$/Kg A"].apply(lambda v: f"R$ {v:.2f}")
                    comp_display["R$/Kg B"] = comp_display["R$/Kg B"].apply(lambda v: f"R$ {v:.2f}")
                    comp_display["Δ R$/Kg (%)"] = comp_display["Δ R$/Kg (%)"].apply(lambda v: f"{v:+.1f}%")

                    st.caption(f"Comparando **{mes_a}** (A) com **{mes_b}** (B)")
                    st.dataframe(comp_display, use_container_width=True, hide_index=True)

# ─── Footer ───────────────────────────────────────────────────────────────────
st.markdown("---")
if arquivo is not None:
    st.caption("🚚 Frete Analytics • Arquivo carregado: " + arquivo.name)
else:
    st.caption("🚚 Frete Analytics")
