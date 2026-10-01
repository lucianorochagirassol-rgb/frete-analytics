"""Acesso ao banco (Supabase).

Diferente da versão anterior, NENHUMA função aqui esconde erro: se o banco
estiver pausado, fora do ar ou sem a tabela, a exceção sobe com uma mensagem
clara e a tela mostra o problema, em vez de fingir que não há dados.
"""

from __future__ import annotations

import math
from datetime import date

import pandas as pd
import streamlit as st

TABELA = "notas"
TAMANHO_PAGINA = 1000
TAMANHO_LOTE = 500

COLUNAS = [
    "chave", "mes", "data_emissao", "numero", "serie", "emitente_cnpj",
    "emitente_nome", "estabelecimento", "cliente_cnpj", "cliente_nome",
    "natureza", "cfop", "cif_fob", "eh_cif", "eh_interna", "transportadora",
    "uf_origem", "cidade_origem", "uf_destino", "cidade_destino", "vlr_nf",
    "peso", "dt_entrega", "frete_entrega", "frete_complementar",
    "frete_reentrega", "frete_devolucao", "frete_aprovisionamento",
    "status_frete", "frete_real", "frete_estimado",
]
NUMERICAS = [
    "vlr_nf", "peso", "frete_entrega", "frete_complementar", "frete_reentrega",
    "frete_devolucao", "frete_aprovisionamento", "frete_real", "frete_estimado",
]


class ErroBanco(Exception):
    """Erro de banco com mensagem para o usuário."""


def _segredo(nome: str):
    try:
        return st.secrets.get(nome)
    except Exception:  # sem arquivo de secrets
        return None


def configurado() -> bool:
    return bool(_segredo("SUPABASE_URL") and _segredo("SUPABASE_KEY"))


@st.cache_resource
def _cliente():
    from supabase import create_client
    return create_client(_segredo("SUPABASE_URL"), _segredo("SUPABASE_KEY"))


def _traduzir(e: Exception) -> ErroBanco:
    msg = str(e)
    if "Name or service not known" in msg or "nodename nor servname" in msg:
        return ErroBanco(
            "Não foi possível encontrar o servidor do Supabase. O projeto provavelmente "
            "está pausado por inatividade: entre em supabase.com, abra o projeto e clique "
            "em \"Resume project\". Detalhe técnico: " + msg
        )
    if "PGRST205" in msg or ("relation" in msg and "does not exist" in msg):
        return ErroBanco(
            f"A tabela \"{TABELA}\" não existe no banco. Rode o arquivo sql/schema.sql "
            "no SQL Editor do Supabase. Detalhe técnico: " + msg
        )
    if "row-level security" in msg.lower():
        return ErroBanco(
            "O banco recusou a operação por causa das regras de acesso (RLS). "
            "Detalhe técnico: " + msg
        )
    return ErroBanco("Erro ao acessar o banco: " + msg)


def testar_conexao() -> tuple[bool, str]:
    """Faz uma consulta real (não só confere se a senha existe)."""
    if not configurado():
        return False, "SUPABASE_URL e SUPABASE_KEY não estão cadastrados em Secrets."
    try:
        _cliente().table(TABELA).select("chave").limit(1).execute()
        return True, "Banco conectado"
    except Exception as e:  # noqa: BLE001
        return False, str(_traduzir(e))


@st.cache_data(ttl=600, show_spinner="Carregando histórico…")
def carregar_notas() -> pd.DataFrame:
    """Todas as notas salvas. Paginado e ORDENADO pela chave: sem ordem fixa,
    páginas consecutivas podem repetir ou pular linhas."""
    if not configurado():
        return pd.DataFrame(columns=COLUNAS)
    paginas = []
    inicio = 0
    try:
        while True:
            resp = (
                _cliente().table(TABELA).select(",".join(COLUNAS)).order("chave")
                .range(inicio, inicio + TAMANHO_PAGINA - 1).execute()
            )
            lote = resp.data or []
            if lote:
                paginas.append(pd.DataFrame(lote))
            if len(lote) < TAMANHO_PAGINA:
                break
            inicio += TAMANHO_PAGINA
    except Exception as e:  # noqa: BLE001
        raise _traduzir(e) from e

    if not paginas:
        return pd.DataFrame(columns=COLUNAS)
    return ajustar_tipos(pd.concat(paginas, ignore_index=True))


def ajustar_tipos(df: pd.DataFrame) -> pd.DataFrame:
    """Converte o que volta do banco (JSON) para os tipos usados nas telas."""
    for c in NUMERICAS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ("frete_real", "frete_estimado", "vlr_nf", "peso"):
        df[c] = df[c].fillna(0.0)
    df["eh_cif"] = df["eh_cif"].astype(bool)
    df["eh_interna"] = df["eh_interna"].astype(bool)
    df["data_emissao"] = pd.to_datetime(df["data_emissao"], errors="coerce").dt.date
    return df


def _para_json(valor):
    if valor is None:
        return None
    if isinstance(valor, float) and math.isnan(valor):
        return None
    if isinstance(valor, (pd.Timestamp, date)):
        return valor.isoformat()[:10]
    if hasattr(valor, "item"):  # numpy
        return valor.item()
    return valor


def salvar_notas(notas: pd.DataFrame) -> dict:
    """Substitui no banco os meses presentes no arquivo pelas notas do arquivo.

    Ordem segura: primeiro grava/atualiza todas as notas do arquivo (upsert pela
    chave) e só depois remove, desses meses, as notas que não vieram no arquivo.
    Se a gravação falhar no meio, nada do histórico anterior foi apagado.

    Retorna {"meses": [...], "gravadas": n, "removidas": n}.
    """
    if notas.empty:
        return {"meses": [], "gravadas": 0, "removidas": 0}
    meses = sorted(notas["mes"].unique().tolist())
    registros = [
        {c: _para_json(v) for c, v in linha.items()}
        for linha in notas[COLUNAS].to_dict(orient="records")
    ]
    novas = set(notas["chave"])
    try:
        cli = _cliente()
        for i in range(0, len(registros), TAMANHO_LOTE):
            cli.table(TABELA).upsert(registros[i:i + TAMANHO_LOTE], on_conflict="chave").execute()

        existentes = []
        inicio = 0
        while True:
            resp = (
                cli.table(TABELA).select("chave").in_("mes", meses).order("chave")
                .range(inicio, inicio + TAMANHO_PAGINA - 1).execute()
            )
            lote = resp.data or []
            existentes += [r["chave"] for r in lote]
            if len(lote) < TAMANHO_PAGINA:
                break
            inicio += TAMANHO_PAGINA
        sobrando = sorted(set(existentes) - novas)
        for i in range(0, len(sobrando), 200):
            cli.table(TABELA).delete().in_("chave", sobrando[i:i + 200]).execute()
    except Exception as e:  # noqa: BLE001
        raise _traduzir(e) from e
    finally:
        carregar_notas.clear()
    return {"meses": meses, "gravadas": len(registros), "removidas": len(sobrando)}
