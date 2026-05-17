"""
Transformação de dados reais do CISSPoder → Data Warehouse.

CAMPOS CONFIRMADOS via API real (vlxrr.dyndns.org:3046):

cad_pessoas:
  idclifor, tipofisicajuridica, tipocadastro, flaginativo, nome,
  nomefantasia, cnpjcpf, rg, emissorrg, dtcadastro, endereco,
  numero, bairro, descrcidade, idcep, complemento, uf,
  nomecontato1, nomecontato2, fone1, fonefax, email, inscrmunicipal,
  inscrestadual, descratividade, descrgrupoeconomico, descrsituacao,
  vallimiteconvenio, vallimitecredito, tiporegimetribfederal,
  tiporegimetributacao, dtalteracao, statusfinan, codigoibge, contribuinte

cad_produtos (65.556 produtos):
  idproduto, idsubproduto, descrcomproduto, descrresproduto,
  subdescricao, nrcodbarprod, idsecao, descrsecao, idgrupo,
  descrgrupo, idsubgrupo, descrsubgrupo, idmarcafabricante, descricao,
  embalagementrada, valgramaentrada, embalagemsaida, valgramasaida,
  ncm, peripi, flaginativo, dtcadastro, dtalteracao, pesoliquido,
  pesobruto, altura, largura, comprimento, referencia, flagbloqueiavenda,
  idmodelo, flagutilizaecommerce, valmultivendas

get_pedido_venda_prod (9.296.514 itens — é um flat de ITENS, não cabeçalho):
  idproduto, idsubproduto, idorcamento, idempresa, qtdproduto,
  idlocalentrega, numsequencia, valdescontopro, valunitbruto,
  valtotliquido, idvendedor, perdescontopro, valacrescimopro,
  valdescontofinanceiro, valacrescimofinanceiro, observacao,
  valicmsubst, valfrete, flagprecopromocao, valdescontoicms,
  dadoscomplementares, peripi, valipi, valbaseipi, vallucro,
  permargemlucro, largura, altura, comprimento, flagevidencia,
  qtditem, flagentregaprogramada, dtalteracao, flagcancelado,
  valacessorios, valipiacessorios, flagproddescqtd, valtaxasdiversas,
  valunitbrutoatacarejo, pericm, valicms, flagtributaestadoempresa,
  tipoprecoutilizado, valipifrete

NOTA: get_pedido_venda_prod retorna ITENS (não cabeçalho). idorcamento é o ID do pedido.
      Não há serviço de estoque disponível — usamos cad_produtos p/ referência.
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_float(val: Any) -> float:
    if val is None:
        return 0.0
    s = str(val).strip().replace(" ", "")
    if not s:
        return 0.0
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    elif s.count(".") > 1:
        parts = s.split(".")
        s = "".join(parts[:-1]) + "." + parts[-1]
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def _safe_str(val: Any, max_len: int = 500) -> str:
    if val is None:
        return ""
    return str(val).strip()[:max_len]


def _safe_date(val: Any) -> str | None:
    """Normaliza data para YYYY-MM-DD (API retorna ISO ou datetime)."""
    if not val:
        return None
    s = str(val).strip()
    if "/" in s:
        parts = s.split("/")
        if len(parts) == 3:
            dia, mes, ano = parts
            if len(ano) == 4 and dia.isdigit() and mes.isdigit():
                return f"{ano}-{mes.zfill(2)}-{dia.zfill(2)}"
    try:
        if len(s) >= 10:
            # "2016-07-01 08:35:11" ou "2026-05-02T12:41:10" → "2026-05-02"
            return s[:10]
    except Exception:
        pass
    return None


def _flag_bool(val: Any) -> int:
    """Converte flag 'F'/'T'/'S'/'N'/0/1 → 0 ou 1."""
    if val is None:
        return 1
    s = str(val).upper().strip()
    # flaginativo: "F" = não inativo = ativo
    # Aqui retornamos 1 se ATIVO (flaginativo == "F")
    return 1 if s in ("F", "N", "0", "FALSE", "NAO", "NÃO") else 0


def _produto_key(r: dict) -> str:
    """Chave SKU CISS: idproduto + idsubproduto quando a variante existir."""
    id_produto = str(r.get("idproduto") or r.get("id_produto") or "").strip()
    id_subproduto = str(r.get("idsubproduto") or r.get("id_subproduto") or "").strip()
    if id_subproduto and id_subproduto not in {"0", id_produto}:
        return f"{id_produto}_{id_subproduto}"
    return id_produto


def _extract_cliente_id(r: dict) -> str:
    """
    Extrai ID do cliente a partir de possíveis chaves retornadas por serviços CISS.

    Alguns ambientes retornam o vínculo de cliente no item de venda com nomes
    diferentes de campo, então consolidamos aqui.
    """
    for key in ("id_cliente", "idcliente", "idclifor", "idpessoa", "id_clientevenda"):
        value = str(r.get(key, "")).strip()
        if value and value != "0":
            return value
    return ""


# ── Clientes / Pessoas ────────────────────────────────────────────────────────

def transform_clientes(raw: list[dict]) -> list[dict]:
    """
    Normaliza cad_pessoas → dw_clientes.

    Filtra apenas tipocadastro='F' (clientes finais) ou 'A' (ambos).
    tipocadastro: 'F'=Cliente, 'R'=Fornecedor, 'A'=Ambos, 'T'=Transportador
    """
    out = []
    for r in raw:
        id_cliente = str(r.get("idclifor", "")).strip()
        if not id_cliente or id_cliente == "0":
            continue

        # Filtrar apenas clientes (não só fornecedores puros)
        tipo = str(r.get("tipocadastro", "F")).upper()
        if tipo not in ("F", "A", "C"):
            continue  # Pula fornecedores (R) e transportadores (T)

        out.append({
            "id_cliente":       id_cliente,
            "nome":             _safe_str(r.get("nome")),
            "nome_fantasia":    _safe_str(r.get("nomefantasia")),
            "documento":        _safe_str(r.get("cnpjcpf"), 18),
            "cpf_cnpj":          _safe_str(r.get("cnpjcpf"), 18),
            "tipo_pessoa":      _safe_str(r.get("tipofisicajuridica"), 1),  # J/F
            "tipo_cadastro":    tipo,
            "email":            _safe_str(r.get("email"), 200),
            "telefone":         _safe_str(r.get("fone1"), 20),
            "cidade":           _safe_str(r.get("descrcidade"), 100),
            "estado":           _safe_str(r.get("uf"), 2),
            "bairro":           _safe_str(r.get("bairro"), 100),
            "endereco":         _safe_str(r.get("endereco"), 200),
            "cep":              str(r.get("idcep", "")).strip(),
            "atividade":        _safe_str(r.get("descratividade"), 100),
            "segmento":          _safe_str(r.get("descratividade"), 100),
            "situacao_financ":  _safe_str(r.get("statusfinan"), 20),
            "limite_credito":   _safe_float(r.get("vallimitecredito")),
            "data_cadastro":    _safe_date(r.get("dtcadastro")),
            "data_atualizacao": _safe_date(r.get("dtalteracao")),
            "atualizado_em":     _safe_date(r.get("dtalteracao")),
            "ativo":            _flag_bool(r.get("flaginativo")),  # flaginativo F=ativo
        })

    logger.debug("transform_clientes: %d/%d válidos (só clientes)", len(out), len(raw))
    return out


# ── Itens de Venda ────────────────────────────────────────────────────────────

def transform_itens_venda(raw: list[dict]) -> list[dict]:
    """
    Normaliza get_pedido_venda_prod → dw_itens_venda.

    IMPORTANTE: Este serviço retorna ITENS individuais (não cabeçalho).
      idorcamento = ID do pedido/orçamento
      idproduto   = ID do produto
      qtdproduto  = quantidade
      valunitbruto = preço unitário
      valtotliquido = valor total do item
      dtalteracao = data da operação
      flagcancelado = "T"/"F"
    """
    out = []
    for r in raw:
        id_orcamento = str(r.get("idorcamento", "")).strip()
        id_produto = _produto_key(r)

        if not id_orcamento or not id_produto or id_orcamento == "0":
            continue

        # Ignorar itens cancelados
        cancelado = str(r.get("flagcancelado", "F")).upper()
        if cancelado == "T":
            continue

        # Data da venda: dtalteracao (data de criação/alteração do item)
        data_venda = _safe_date(r.get("dtalteracao"))
        if not data_venda:
            continue

        seq = str(r.get("numsequencia", "1"))
        id_item = f"{id_orcamento}_{seq.zfill(4)}"

        qtd = _safe_float(r.get("qtdproduto"))
        val_unit = _safe_float(r.get("valunitbruto"))
        val_total = _safe_float(r.get("valtotliquido"))

        # Calcular total se não vier explícito
        if val_total == 0 and qtd > 0 and val_unit > 0:
            val_total = round(qtd * val_unit, 4)

        out.append({
            "id_item":         id_item,
            "id_venda":        id_orcamento,
            "id_cliente":      _extract_cliente_id(r),
            "id_produto":      id_produto,
            "codigo_produto":   _safe_str(r.get("nrcodbarprod") or r.get("codproduto") or id_produto, 80),
            "nome_produto":     _safe_str(r.get("descrcomproduto") or r.get("descrresproduto"), 200),
            "id_vendedor":     str(r.get("idvendedor", "")).strip(),
            "data_venda":      data_venda,
            "quantidade":      qtd,
            "preco_unitario":  val_unit,
            "desconto_item":   _safe_float(r.get("valdescontopro")),
            "desconto_pct":    _safe_float(r.get("perdescontopro")),
            "desconto_val":    _safe_float(r.get("valdescontopro")),
            "total_item":      val_total,
            "val_lucro":       _safe_float(r.get("vallucro")),
            "per_margem":      _safe_float(r.get("permargemlucro")),
            "observacao":      _safe_str(r.get("observacao"), 200),
            "id_empresa":      str(r.get("idempresa", "1")).strip(),
        })

    logger.debug("transform_itens_venda: %d/%d válidos", len(out), len(raw))
    return out


def transform_vendas_do_itens(itens: list[dict]) -> list[dict]:
    """
    Agrega itens em cabeçalhos de venda para o DW.

    Como o get_pedido_venda_prod retorna itens (não cabeçalho),
    agrupamos por id_venda para reconstituir os totais do pedido.
    """
    from collections import defaultdict

    pedidos: dict[str, dict] = defaultdict(lambda: {
        "id_venda": "",
        "data_venda": None,
        "id_cliente": "",
        "id_vendedor": "",
        "total_bruto": 0.0,
        "total_desconto": 0.0,
        "total_liquido": 0.0,
        "num_itens": 0,
        "id_empresa": "1",
        "status": "aprovado",
    })

    for item in itens:
        pid = item["id_venda"]
        p = pedidos[pid]
        p["id_venda"] = pid
        if not p["data_venda"]:
            p["data_venda"] = item["data_venda"]
        if not p["id_vendedor"]:
            p["id_vendedor"] = item["id_vendedor"]
        if not p["id_cliente"]:
            p["id_cliente"] = _safe_str(item.get("id_cliente"), 80)
        p["total_bruto"]   += item["preco_unitario"] * item["quantidade"]
        p["total_desconto"] += item["desconto_val"]
        p["total_liquido"]  += item["total_item"]
        p["num_itens"]      += 1
        p["id_empresa"]      = item["id_empresa"]

    result = [
        {**v,
         "total_bruto":   round(v["total_bruto"], 4),
         "total_desconto": round(v["total_desconto"], 4),
         "total_liquido":  round(v["total_liquido"], 4),
        }
        for v in pedidos.values()
        if v["id_venda"] and v["data_venda"]
    ]

    logger.debug("transform_vendas_do_itens: %d pedidos reconstituídos", len(result))
    return result


def transform_vendas(raw: list[dict]) -> list[dict]:
    """Compatibilidade para cargas que ja chegam como cabecalho de venda."""
    out = []
    for r in raw:
        id_venda = _safe_str(r.get("id") or r.get("id_venda") or r.get("idorcamento"), 80)
        data_venda = _safe_date(r.get("data_venda") or r.get("dtemissao") or r.get("dtalteracao"))
        if not id_venda or not data_venda:
            continue

        out.append({
            "id_venda": id_venda,
            "numero_nf": _safe_str(r.get("numero_nf") or r.get("numnota"), 80),
            "data_venda": data_venda,
            "hora_venda": _safe_str(r.get("hora_venda"), 20),
            "id_cliente": _safe_str(r.get("id_cliente") or r.get("idclifor"), 80),
            "nome_cliente": _safe_str(r.get("nome_cliente") or r.get("nome"), 200),
            "id_vendedor": _safe_str(r.get("id_vendedor") or r.get("idvendedor"), 80),
            "nome_vendedor": _safe_str(r.get("nome_vendedor"), 200),
            "total_bruto": _safe_float(r.get("total_bruto")),
            "total_desconto": _safe_float(r.get("total_desconto")),
            "total_liquido": _safe_float(r.get("total_liquido") or r.get("valtotliquido")),
            "status": _safe_str(r.get("status") or "aprovado", 40),
            "tipo_pagamento": _safe_str(r.get("tipo_pagamento"), 80),
            "canal_venda": _safe_str(r.get("canal_venda"), 80),
            "atualizado_em": _safe_date(r.get("atualizado_em") or r.get("dtalteracao")),
        })
    return out


# ── Produtos ──────────────────────────────────────────────────────────────────

def transform_produtos(raw: list[dict]) -> list[dict]:
    """
    Normaliza cad_produtos → dw_produtos.

    Campos reais confirmados:
      descrcomproduto = descrição completa
      descrsecao = seção (ex: ACESSORIOS P/LAR)
      descrgrupo = grupo (ex: FECHADURAS EM GERAL)
      descrsubgrupo = subgrupo (ex: CADEADOS)
      descricao = marca/fabricante
      embalagementrada / embalagemsaida = unidade
      flaginativo = "F" (ativo) / "T" (inativo)
    """
    out = []
    for r in raw:
        id_produto = _produto_key(r)
        if not id_produto or id_produto == "0":
            continue
        id_base = str(r.get("idproduto", "")).strip()
        id_subproduto = str(r.get("idsubproduto", "")).strip()
        codigo = str(r.get("nrcodbarprod") or id_subproduto or id_base or id_produto).strip()

        out.append({
            "id_produto":       id_produto,
            "codigo":           codigo,
            "codigo_produto":   codigo,
            "nome":             _safe_str(r.get("descrcomproduto") or r.get("descrresproduto")),
            "nome_produto":     _safe_str(r.get("descrcomproduto") or r.get("descrresproduto")),
            "descricao":        _safe_str(r.get("subdescricao") or r.get("descrresproduto")),
            "id_categoria":     str(r.get("idgrupo", "")).strip(),
            "nome_secao":       _safe_str(r.get("descrsecao"), 100),
            "nome_categoria":   _safe_str(r.get("descrgrupo"), 100),   # grupo
            "nome_subcategoria": _safe_str(r.get("descrsubgrupo"), 100),
            "marca":            _safe_str(r.get("descricao"), 100),    # fabricante/marca
            "referencia":       _safe_str(r.get("referencia") or id_subproduto, 50),
            "ncm":              _safe_str(r.get("ncm"), 10),
            "unidade":          _safe_str(r.get("embalagemsaida"), 10) or "UN",
            "unidade_entrada":  _safe_str(r.get("embalagementrada"), 10) or "UN",
            "unidade_saida":    _safe_str(r.get("embalagemsaida"), 10) or "UN",
            "preco_custo":      _safe_float(r.get("precocusto") or r.get("valprecocusto")),
            "preco_venda":      _safe_float(r.get("precovenda") or r.get("valprecovenda") or r.get("valmultivendas")),
            "peso_liquido":     _safe_float(r.get("pesoliquido")),
            "peso_bruto":       _safe_float(r.get("pesobruto")),
            "ativo":            _flag_bool(r.get("flaginativo")),
            "bloqueado_venda":  1 if str(r.get("flagbloqueiavenda", "F")).upper() == "T" else 0,
            "data_cadastro":    _safe_date(r.get("dtcadastro")),
            "data_atualizacao": _safe_date(r.get("dtalteracao")),
            "atualizado_em":    _safe_date(r.get("dtalteracao")),
            "id_secao":         str(r.get("idsecao", "")).strip(),
            "id_grupo":         str(r.get("idgrupo", "")).strip(),
            "id_subgrupo":      str(r.get("idsubgrupo", "")).strip(),
        })

    logger.debug("transform_produtos: %d/%d válidos", len(out), len(raw))
    return out


def transform_estoque(raw: list[dict]) -> list[dict]:
    """Normaliza posicao de estoque Integrim para dw_estoque."""
    out = []
    for r in raw:
        id_produto = _produto_key(r)
        id_deposito = _safe_str(r.get("id_deposito") or r.get("iddeposito") or "default", 80)
        if not id_produto:
            continue

        out.append({
            "id_estoque": _safe_str(r.get("id_estoque") or f"{id_produto}_{id_deposito}", 180),
            "id_produto": id_produto,
            "codigo_produto": _safe_str(r.get("codigo_produto") or r.get("codproduto"), 80),
            "nome_produto": _safe_str(r.get("nome_produto") or r.get("descrcomproduto"), 200),
            "id_deposito": id_deposito,
            "nome_deposito": _safe_str(r.get("nome_deposito") or r.get("descrdeposito"), 120),
            "quantidade": _safe_float(r.get("quantidade") or r.get("qtdestoque") or r.get("saldo")),
            "estoque_minimo": _safe_float(r.get("estoque_minimo") or r.get("qtdminima")),
            "estoque_maximo": _safe_float(r.get("estoque_maximo") or r.get("qtdmaxima")),
            "atualizado_em": _safe_date(r.get("atualizado_em") or r.get("dtalteracao")) or "",
        })
    return out
