-- Tabela única do histórico: uma linha por nota fiscal.
-- Criada em 30/09/2026 na reescrita do app. As tabelas antigas
-- (pedidos_historico e fretes_mensais) ficam como estão, só para consulta.

create table if not exists public.notas (
    chave                  text primary key,          -- CNPJ emitente | número | série
    mes                    text not null,             -- AAAA-MM da data de emissão
    data_emissao           date not null,
    numero                 text not null,
    serie                  text,
    emitente_cnpj          text not null,
    emitente_nome          text,
    estabelecimento        text,                      -- LGR RS (matriz), LGR SP (filial), Metal Mecânica
    cliente_cnpj           text,
    cliente_nome           text,
    natureza               text,
    cfop                   text,
    cif_fob                text,
    eh_cif                 boolean not null,
    eh_interna             boolean not null,          -- cliente com CNPJ do grupo
    transportadora         text,
    uf_origem              text,
    cidade_origem          text,
    uf_destino             text,
    cidade_destino         text,
    vlr_nf                 numeric,
    peso                   numeric,
    dt_entrega             text,                      -- conhecimento de transporte (DT)
    frete_entrega          numeric,                   -- vazio = sem conhecimento lançado
    frete_complementar     numeric,
    frete_reentrega        numeric,
    frete_devolucao        numeric,
    frete_aprovisionamento numeric,
    status_frete           text not null,             -- cobrado | estimado | sem_info
    frete_real             numeric not null default 0,
    frete_estimado         numeric not null default 0,
    carregado_em           timestamptz not null default now()
);

create index if not exists notas_mes_idx on public.notas (mes);
