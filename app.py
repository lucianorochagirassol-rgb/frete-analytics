"""Frete Analytics — telas.

As regras de negócio ficam em regras.py e o acesso ao banco em banco.py.
Este arquivo só monta as telas a partir das notas já classificadas.
"""

from __future__ import annotations

import datetime
import io
import os

import pandas as pd
import plotly.express as px
import streamlit as st

import banco
import regras as R

LOGO = "logo.png"
st.set_page_config(
    page_title="Frete Analytics",
    page_icon=LOGO if os.path.exists(LOGO) else "🚚",
    layout="wide",
)

MESES_ABREV = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
ROTULO_STATUS = {
    R.STATUS_COBRADO: "Frete cobrado",
    R.STATUS_ESTIMADO: "Sem conhecimento (estimado)",
    R.STATUS_SEM_INFO: "Sem frete e sem estimativa",
}


# ─── Formatação ─────────────────────────────────────────────────────────────
def moeda(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def moeda_curta(v: float) -> str:
    """Para os cartões de métrica, que cortam textos longos."""
    if abs(v) >= 1_000_000:
        return f"R$ {v / 1_000_000:.2f} mi".replace(".", ",")
    if abs(v) >= 10_000:
        return f"R$ {v / 1_000:.1f} mil".replace(".", ",")
    return moeda(v)


def pct(v: float, casas: int = 2) -> str:
    return f"{v:.{casas}f}%".replace(".", ",")


def inteiro(v: float) -> str:
    return f"{v:,.0f}".replace(",", ".")


def rotulo_mes(m: str) -> str:
    ano, mes = m.split("-")
    return f"{MESES_ABREV[int(mes) - 1]}/{ano}"


# Colunas numéricas: nome exibido (com a unidade) e casas decimais. O formato
# "localized" usa o padrão do navegador (1.234.567,89 em português) e mantém o
# valor numérico, então ordenar pela coluna continua funcionando.
COLUNAS_NUM = {
    "Vendas": ("Vendas (R$)", 2),
    "Valor das notas": ("Valor das notas (R$)", 2),
    "Frete": ("Frete (R$)", 2),
    "Frete estimado": ("Frete estimado (R$)", 2),
    "Peso (kg)": ("Peso (kg)", 0),
    "% frete": ("% frete", 2),
    "% com estimado": ("% com estimado", 2),
    "R$/kg": ("Frete por kg (R$)", 2),
    "% sem frete": ("% sem frete", 0),
    "Notas": ("Notas", 0),
    "Sem frete": ("Sem frete", 0),
}


def tabela(df: pd.DataFrame, **kw):
    """st.dataframe com números no padrão brasileiro, ocupando a largura toda."""
    kw.pop("key", None)  # nem toda versão aceita key em st.dataframe
    kw.pop("column_config", None)
    df = df.copy()
    cfg, nomes = {}, {}
    for col, (nome, casas) in COLUNAS_NUM.items():
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").round(casas)
            nomes[col] = nome
            cfg[nome] = st.column_config.NumberColumn(format="localized")
    df = df.rename(columns=nomes)
    try:
        st.dataframe(df, width="stretch", hide_index=True, column_config=cfg, **kw)
    except Exception:  # versões antigas não aceitam width="stretch"
        st.dataframe(df, use_container_width=True, hide_index=True, column_config=cfg, **kw)


def resumo_tabela(df: pd.DataFrame, por: str, rotulo: str, estimado: bool) -> pd.DataFrame:
    g = R.resumir(df, por, estimado)
    if g.empty:
        return g
    g = g.sort_values("frete", ascending=False)
    return g.rename(columns={
        por: rotulo, "vendas": "Vendas", "frete": "Frete", "peso": "Peso (kg)",
        "notas": "Notas", "sem_frete": "Sem frete", "pct_frete": "% frete",
        "rs_kg": "R$/kg", "pct_sem_frete": "% sem frete",
    })[[rotulo, "Vendas", "Frete", "% frete", "Peso (kg)", "R$/kg", "Notas", "% sem frete"]]


def detalhe_notas(df: pd.DataFrame, titulo: str, chave: str):
    with st.expander(f"🔎 Ver notas — {titulo} ({inteiro(len(df))})"):
        if df.empty:
            st.caption("Nenhuma nota.")
            return
        t = df[[
            "data_emissao", "numero", "estabelecimento", "cliente_nome", "transportadora",
            "cidade_origem", "cidade_destino", "uf_destino", "vlr_nf", "peso",
            "frete_real", "frete_estimado", "status_frete",
        ]].copy()
        t["status_frete"] = t["status_frete"].map(ROTULO_STATUS)
        t = t.rename(columns={
            "data_emissao": "Emissão", "numero": "NF", "estabelecimento": "Emitente",
            "cliente_nome": "Cliente", "transportadora": "Transportadora",
            "cidade_origem": "Origem", "cidade_destino": "Destino", "uf_destino": "UF",
            "vlr_nf": "Vendas", "peso": "Peso (kg)", "frete_real": "Frete",
            "frete_estimado": "Frete estimado", "status_frete": "Situação do frete",
        }).sort_values("Emissão")
        tabela(t, key=f"det_{chave}")
        st.download_button(
            "⬇️ Baixar estas notas (CSV)",
            t.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
            file_name=f"notas_{chave}.csv", mime="text/csv", key=f"dl_{chave}",
        )


# ─── Dados ──────────────────────────────────────────────────────────────────
@st.cache_data(ttl=60, show_spinner=False)
def status_banco() -> tuple[bool, str]:
    return banco.testar_conexao()


@st.cache_data(show_spinner="Lendo o arquivo…")
def processar_arquivo(conteudo: bytes):
    bruto = R.ler_csv(io.BytesIO(conteudo))
    faltando = R.colunas_faltando(bruto)
    if faltando:
        return None, {"faltando": faltando}
    return R.preparar(bruto)


erro_carga = None
try:
    todas = banco.carregar_notas()
except banco.ErroBanco as e:
    todas = pd.DataFrame(columns=banco.COLUNAS)
    erro_carga = str(e)


# ─── Barra lateral: filtros ─────────────────────────────────────────────────
with st.sidebar:
    if os.path.exists(LOGO):
        st.image(LOGO, width=160)
    st.title("🚚 Frete Analytics")

    ok, msg = status_banco()
    if erro_carga:
        st.error(erro_carga)
    elif ok:
        st.success(f"🟢 {msg}")
    else:
        st.error(msg)

    meses = sorted(todas["mes"].dropna().unique().tolist()) if not todas.empty else []
    if meses:
        if len(meses) > 1:
            ini, fim = st.select_slider(
                "📅 Período", options=meses, value=(meses[0], meses[-1]), format_func=rotulo_mes,
            )
        else:
            ini = fim = meses[0]
            st.caption(f"📅 Período: {rotulo_mes(ini)}")
        estabs = sorted(todas["estabelecimento"].dropna().unique().tolist())
        estab_sel = st.multiselect("🏭 Emitente", estabs, default=estabs)
        incluir_estimado = st.toggle(
            "Incluir frete estimado",
            value=False,
            help="Entregas sem conhecimento de transporte lançado (principalmente as entregas "
                 "locais da filial de SP) entram com o valor do aprovisionamento do sistema.",
        )
    else:
        ini = fim = None
        estab_sel = []
        incluir_estimado = False
        if not erro_carga:
            st.info("Nenhum mês salvo ainda. Envie um CSV na aba **📤 Enviar arquivo**.")

    st.markdown("---")
    st.caption(f"Streamlit {st.__version__} · pandas {pd.__version__}")

if meses:
    no_periodo = todas[
        todas["mes"].between(ini, fim) & todas["estabelecimento"].isin(estab_sel)
    ]
else:
    no_periodo = todas
base = R.base_clientes(no_periodo)  # clientes de fato, CIF

st.markdown("## 🚚 Análise de Logística & Eficiência de Fretes")
if meses:
    st.caption(
        f"{rotulo_mes(ini)} a {rotulo_mes(fim)} · notas CIF para clientes"
        + (" · inclui frete estimado" if incluir_estimado else " · só frete cobrado")
    )

aba_env, aba_geral, aba_uf, aba_comp, aba_transf = st.tabs([
    "📤 Enviar arquivo",
    "📊 Visão geral",
    "📍 Estados e rotas",
    "📅 Comparar períodos",
    "🔁 Transferências internas",
])


# ═══ ENVIAR ARQUIVO ═════════════════════════════════════════════════════════
with aba_env:
    st.markdown("### 📂 Envie o CSV exportado do sistema")
    st.caption(
        "Relatório \"Análise de Frete por NF\". Pode conter um ou vários meses: cada nota vai "
        "para o mês da sua data de emissão. Nada é salvo antes de você conferir e confirmar."
    )
    if "msg_salvo" in st.session_state:
        st.success(st.session_state.pop("msg_salvo"))
    arquivo = st.file_uploader("Arquivo CSV", type=["csv"])

    if arquivo is not None:
        notas_arq, rel = processar_arquivo(arquivo.getvalue())
        if notas_arq is None:
            st.error(
                "⚠️ O arquivo não tem as colunas necessárias:\n\n- "
                + "\n- ".join(rel["faltando"])
                + "\n\nConfira se é o relatório \"Análise de Frete por NF\"."
            )
        elif notas_arq.empty:
            st.error("Nenhuma nota emitida pelas empresas do grupo foi encontrada no arquivo.")
        else:
            st.markdown("#### 1. Conferência do arquivo")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Linhas no arquivo", inteiro(rel["linhas_lidas"]))
            c2.metric("Notas do grupo", inteiro(rel["notas"]))
            c3.metric("Notas repetidas removidas", inteiro(rel["duplicadas"]),
                      help="A mesma nota (CNPJ + número + série) aparecendo mais de uma vez "
                           "na exportação. Fica uma cópia só, a que tem frete.")
            c4.metric("De fora do grupo (ignoradas)", inteiro(rel["fora_do_escopo"]),
                      help="Notas de fornecedores e outras empresas.")

            avisos = []
            if rel["duplicadas"]:
                quem = ", ".join(f"{k} ({v})" for k, v in rel["duplicadas_emitentes"].items())
                avisos.append(
                    f"**{inteiro(rel['duplicadas'])} notas vieram repetidas** na exportação "
                    f"({moeda(rel['duplicadas_valor'])} que seriam contados duas vezes): {quem}. "
                    "Vale verificar o filtro da exportação no sistema."
                )
            if rel["sem_data"]:
                avisos.append(f"**{rel['sem_data']} notas sem data de emissão válida** foram ignoradas.")
            for a in avisos:
                st.warning(a)
            st.caption(
                f"{inteiro(rel['internas'])} transferências internas (cliente com CNPJ do grupo) "
                f"e {inteiro(rel['fob'])} notas FOB ({moeda(rel['fob_valor'])}, frete pago pelo "
                "cliente) ficam fora do % de frete."
            )

            base_arq = R.base_clientes(notas_arq)
            status = rel["status_frete"]
            sem = status.get(R.STATUS_ESTIMADO, 0) + status.get(R.STATUS_SEM_INFO, 0)
            if sem:
                st.markdown("#### 2. Notas sem frete cobrado")
                st.info(
                    f"{inteiro(sem)} de {inteiro(rel['base_notas'])} notas CIF para clientes "
                    f"({pct(sem / max(rel['base_notas'], 1) * 100, 0)}) não têm conhecimento de "
                    "transporte lançado. Quando a transportadora aparece com 100% abaixo, o "
                    "custo dela não é lançado nota a nota no sistema (não é atraso de romaneio). "
                    "Essas notas entram com o valor do aprovisionamento quando a opção "
                    "**Incluir frete estimado** está ligada."
                )
                sf = R.sem_frete_por_transportadora(base_arq).rename(columns={
                    "estabelecimento": "Emitente", "transportadora": "Transportadora",
                    "notas": "Notas", "sem_frete": "Sem frete", "vendas": "Vendas",
                    "estimado": "Frete estimado", "pct_sem_frete": "% sem frete",
                })
                tabela(sf, key="sem_frete_arq")

            st.markdown("#### 3. Resumo por mês")
            por_mes = R.resumir(base_arq, "mes", False)
            est_mes = R.resumir(base_arq, "mes", True)[["mes", "frete", "pct_frete"]]
            est_mes.columns = ["mes", "frete_total", "pct_total"]
            por_mes = por_mes.merge(est_mes, on="mes")
            ja_salvos = set(meses)
            por_mes["Situação"] = por_mes["mes"].map(
                lambda m: "já salvo, será substituído" if m in ja_salvos else "novo"
            )
            por_mes["Mês"] = por_mes["mes"].map(rotulo_mes)
            tabela(
                por_mes.rename(columns={
                    "notas": "Notas", "vendas": "Vendas", "frete": "Frete",
                    "pct_frete": "% frete", "pct_total": "% com estimado",
                })[["Mês", "Notas", "Vendas", "Frete", "% frete", "% com estimado", "Situação"]],
                column_config={"% com estimado": st.column_config.NumberColumn(format="%.2f%%")},
                key="resumo_arq",
            )

            st.markdown("#### 4. Salvar no histórico")
            meses_arq = rel["meses"]
            rotulo = (rotulo_mes(meses_arq[0]) if len(meses_arq) == 1
                      else f"{rotulo_mes(meses_arq[0])} a {rotulo_mes(meses_arq[-1])}")
            st.caption(
                "Grava todas as notas do grupo (inclusive transferências e FOB, que ficam "
                "marcadas). Nos meses do arquivo, o histórico passa a ser exatamente o arquivo."
            )
            if st.button(
                f"💾 Salvar {inteiro(len(notas_arq))} notas ({rotulo})",
                type="primary", disabled=not banco.configurado(),
            ):
                try:
                    r = banco.salvar_notas(notas_arq)
                    status_banco.clear()
                    st.session_state["msg_salvo"] = (
                        f"✅ {inteiro(r['gravadas'])} notas salvas ({rotulo})."
                        + (f" {inteiro(r['removidas'])} notas antigas desses meses que não "
                           "estavam no arquivo foram removidas." if r["removidas"] else "")
                    )
                    st.rerun()
                except banco.ErroBanco as e:
                    st.error(str(e))


# ═══ VISÃO GERAL ════════════════════════════════════════════════════════════
with aba_geral:
    if base.empty:
        st.info("Sem notas no período e filtros escolhidos.")
    else:
        t_real = R.totais(base, False)
        t_est = R.totais(base, True)
        t = t_est if incluir_estimado else t_real
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("💰 Vendas", moeda_curta(t["vendas"]), help=moeda(t["vendas"]))
        c2.metric("🚛 Frete", moeda_curta(t["frete"]), help=moeda(t["frete"]))
        c3.metric("📊 Frete / venda", pct(t["pct_frete"]))
        c4.metric("⚖️ R$ por kg", moeda(t["rs_kg"]))
        c5, c6, c7, c8 = st.columns(4)
        c5.metric("Frete / venda só com frete cobrado", pct(t_real["pct_frete"]))
        c6.metric("Frete / venda com estimado", pct(t_est["pct_frete"]))
        c7.metric("Notas sem frete cobrado", pct(t["pct_sem_frete"], 0))
        c8.metric("Notas", inteiro(t["notas"]))

        fob = no_periodo[~no_periodo["eh_interna"] & ~no_periodo["eh_cif"]]
        if not fob.empty:
            st.caption(
                f"Fora do cálculo: {inteiro(len(fob))} notas FOB ({moeda(fob['vlr_nf'].sum())} "
                "em vendas, frete pago pelo cliente)."
            )

        mensal_r = R.resumir(base, "mes", False)[["mes", "pct_frete"]].assign(serie="Só frete cobrado")
        mensal_e = R.resumir(base, "mes", True)[["mes", "pct_frete"]].assign(serie="Com frete estimado")
        mensal = pd.concat([mensal_r, mensal_e]).sort_values("mes")
        mensal["Mês"] = mensal["mes"].map(rotulo_mes)
        if mensal["mes"].nunique() > 1:
            fig = px.line(
                mensal, x="Mês", y="pct_frete", color="serie", markers=True,
                title="Frete sobre vendas por mês",
                labels={"pct_frete": "Frete / venda (%)", "serie": ""},
            )
            fig.update_layout(legend={"orientation": "h", "y": -0.2})
            st.plotly_chart(fig)

        st.markdown("### 🏭 Por emitente")
        tabela(resumo_tabela(base, "estabelecimento", "Emitente", incluir_estimado), key="por_emit")

        st.markdown("### 🚛 Por transportadora")
        st.caption("\"% sem frete\" alto indica transportadora cujo custo não é lançado nota a nota.")
        tabela(resumo_tabela(base, "transportadora", "Transportadora", incluir_estimado), key="por_transp")

        st.markdown("### 👤 Por cliente")
        tabela(resumo_tabela(base, "cliente_nome", "Cliente", incluir_estimado), key="por_cli")
        detalhe_notas(base, "período selecionado", "geral")


# ═══ ESTADOS E ROTAS ════════════════════════════════════════════════════════
with aba_uf:
    if base.empty:
        st.info("Sem notas no período e filtros escolhidos.")
    else:
        por_uf = resumo_tabela(base, "uf_destino", "UF", incluir_estimado)
        g1, g2 = st.columns(2)
        with g1:
            fig = px.bar(
                por_uf.sort_values("% frete", ascending=False), x="UF", y="% frete",
                title="Frete sobre vendas por estado de destino", text_auto=".1f",
            )
            fig.update_traces(marker_color="#4C78A8")
            st.plotly_chart(fig)
        with g2:
            fig = px.bar(
                por_uf.sort_values("Frete", ascending=False), x="UF", y="Frete",
                title="Frete total por estado de destino (R$)", text_auto=".2s",
            )
            fig.update_traces(marker_color="#9DA5AE")
            st.plotly_chart(fig)
        tabela(por_uf, key="tab_uf")

        st.markdown("---")
        ufs = por_uf["UF"].tolist()
        uf = st.selectbox("Estado para investigar", ufs, key="uf_sel")
        df_uf = base[base["uf_destino"] == uf]

        st.markdown(f"### 👤 Clientes em {uf}")
        tabela(resumo_tabela(df_uf, "cliente_nome", "Cliente", incluir_estimado), key="cli_uf")

        st.markdown(f"### 🔀 Transportadoras na mesma rota ({uf})")
        st.caption(
            "Compara R$/kg entre transportadoras numa rota idêntica (mesma origem e destino). "
            "Usa só notas com frete cobrado, para não comparar custo real com estimativa."
        )
        cobradas = df_uf[df_uf["status_frete"].eq(R.STATUS_COBRADO) & (df_uf["peso"] > 0)].copy()
        if cobradas.empty:
            st.info("Nenhuma nota com frete cobrado neste estado.")
        else:
            cobradas["rota"] = cobradas["cidade_origem"] + " → " + cobradas["cidade_destino"]
            rotas = cobradas["rota"].value_counts()
            rota = st.selectbox(
                "Rota", rotas.index.tolist(),
                format_func=lambda r: f"{r} ({rotas[r]} notas)", key="rota_sel",
            )
            df_rota = cobradas[cobradas["rota"] == rota]
            comp = R.resumir(df_rota, "transportadora", False).sort_values("rs_kg")
            if len(comp) > 1:
                barato, caro = comp.iloc[0], comp.iloc[-1]
                m1, m2, m3 = st.columns(3)
                m1.metric("✅ Menor R$/kg", barato["transportadora"], moeda(barato["rs_kg"]),
                          delta_color="off")
                m2.metric("⚠️ Maior R$/kg", caro["transportadora"], moeda(caro["rs_kg"]),
                          delta_color="off")
                m3.metric("Diferença", pct((caro["rs_kg"] / barato["rs_kg"] - 1) * 100, 0)
                          if barato["rs_kg"] else "—")
            tabela(comp.rename(columns={
                "transportadora": "Transportadora", "vendas": "Vendas", "frete": "Frete",
                "peso": "Peso (kg)", "notas": "Notas", "pct_frete": "% frete", "rs_kg": "R$/kg",
            })[["Transportadora", "Notas", "Peso (kg)", "Frete", "R$/kg", "Vendas", "% frete"]],
                key="tab_rota")
            detalhe_notas(df_rota, rota, "rota")


# ═══ COMPARAR PERÍODOS ══════════════════════════════════════════════════════
with aba_comp:
    st.markdown("### 📅 Comparar períodos")
    st.caption(
        "Compare intervalos de datas quaisquer (por exemplo, a mesma semana em anos diferentes). "
        "Usa todo o histórico salvo, com o filtro de emitente da barra lateral."
    )
    hist = R.base_clientes(todas[todas["estabelecimento"].isin(estab_sel)]) if meses else todas
    if hist.empty:
        st.info("Nenhum dado salvo ainda.")
    else:
        dmin, dmax = min(hist["data_emissao"]), max(hist["data_emissao"])
        if "periodos" not in st.session_state:
            ini_p = max(dmin, dmax - datetime.timedelta(days=29))
            st.session_state.periodos = [
                {"id": 1, "nome": "Período 1", "ini": ini_p, "fim": dmax},
                {"id": 2, "nome": "Período 2", "ini": dmin, "fim": min(dmax, dmin + datetime.timedelta(days=29))},
            ]
            st.session_state.prox_id = 3

        for p in st.session_state.periodos:
            a, b, c, d = st.columns([2, 2, 2, 1])
            p["nome"] = a.text_input("Nome", p["nome"], key=f"pn{p['id']}")
            p["ini"] = b.date_input("Início", p["ini"], min_value=dmin, max_value=dmax,
                                    format="DD/MM/YYYY", key=f"pi{p['id']}")
            p["fim"] = c.date_input("Fim", p["fim"], min_value=dmin, max_value=dmax,
                                    format="DD/MM/YYYY", key=f"pf{p['id']}")
            d.write("")
            if d.button("🗑️", key=f"pr{p['id']}") and len(st.session_state.periodos) > 1:
                st.session_state.periodos = [x for x in st.session_state.periodos if x["id"] != p["id"]]
                st.rerun()
        if st.button("➕ Adicionar período"):
            ult = st.session_state.periodos[-1]
            st.session_state.periodos.append({
                "id": st.session_state.prox_id,
                "nome": f"Período {len(st.session_state.periodos) + 1}",
                "ini": ult["ini"], "fim": ult["fim"],
            })
            st.session_state.prox_id += 1
            st.rerun()

        linhas, recortes = [], {}
        for p in st.session_state.periodos:
            if p["ini"] > p["fim"]:
                st.warning(f"'{p['nome']}': a data de início é depois da data de fim.")
                continue
            sub = hist[(hist["data_emissao"] >= p["ini"]) & (hist["data_emissao"] <= p["fim"])]
            tt = R.totais(sub, incluir_estimado)
            linhas.append({
                "Período": p["nome"],
                "Intervalo": f"{p['ini']:%d/%m/%Y} a {p['fim']:%d/%m/%Y}",
                "Notas": tt["notas"], "Vendas": tt["vendas"], "Frete": tt["frete"],
                "% frete": tt["pct_frete"], "R$/kg": tt["rs_kg"],
                "% sem frete": tt["pct_sem_frete"],
            })
            recortes[p["nome"]] = sub
        if linhas:
            comp = pd.DataFrame(linhas)
            tabela(comp, key="tab_periodos")
            fig = px.bar(comp, x="Período", y="% frete", text_auto=".2f",
                         title="Frete sobre vendas por período")
            fig.update_traces(marker_color="#4C78A8")
            st.plotly_chart(fig)
            escolhido = st.selectbox("Ver notas de um período", list(recortes), key="per_det")
            detalhe_notas(recortes[escolhido], escolhido, "periodo")


# ═══ TRANSFERÊNCIAS INTERNAS ════════════════════════════════════════════════
with aba_transf:
    st.markdown("### 🔁 Transferências internas")
    st.caption(
        "Notas cujo cliente tem CNPJ do próprio grupo (LGR matriz, filial SP ou Metal Mecânica). "
        "Não entram nos indicadores das outras abas."
    )
    internas = no_periodo[no_periodo["eh_interna"]] if not no_periodo.empty else no_periodo
    if internas.empty:
        st.info("Nenhuma transferência interna no período e filtros escolhidos.")
    else:
        internas = internas.assign(
            destino=internas["cliente_cnpj"].map(R.ESTABELECIMENTOS).fillna(internas["cliente_nome"])
        )
        ti = R.totais(internas, incluir_estimado)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Notas", inteiro(ti["notas"]))
        c2.metric("Valor transferido", moeda_curta(ti["vendas"]), help=moeda(ti["vendas"]))
        c3.metric("Frete", moeda_curta(ti["frete"]), help=moeda(ti["frete"]))
        c4.metric("Peso", f"{inteiro(ti['peso'])} kg")

        internas = internas.assign(fluxo=internas["estabelecimento"] + " → " + internas["destino"])
        tabela(resumo_tabela(internas, "fluxo", "De → Para", incluir_estimado)
              .rename(columns={"Vendas": "Valor das notas"}), key="tab_fluxo")
        mensal_t = R.resumir(internas, "mes", incluir_estimado)
        if len(mensal_t) > 1:
            mensal_t["Mês"] = mensal_t["mes"].map(rotulo_mes)
            fig = px.bar(mensal_t, x="Mês", y="frete", title="Frete de transferências por mês (R$)",
                         labels={"frete": "Frete (R$)"}, text_auto=".2s")
            fig.update_traces(marker_color="#9DA5AE")
            st.plotly_chart(fig)
        detalhe_notas(internas, "transferências", "transf")
