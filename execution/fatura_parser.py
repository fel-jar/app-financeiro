"""Extrai lançamentos de uma fatura Bradesco em PDF (parser determinístico,
sem LLM) -- usado pra auditoria: comparar o que a Pluggy sincronizou com o
que a fatura real mostra (ver agente_ferramentas.auditar_fatura).

Formato aprendido testando contra faturas reais em 2026-07-26 (ver
directives/agente_telegram.md):
- `page.extract_text()` mistura a coluna de lançamentos com a barra
  lateral (Limites/Taxas) na ordem visual da página -- não confiável pra
  extrair as linhas de compra sozinho.
- `page.extract_tables()` já isola a tabela de "Lançamentos" numa célula
  só, com uma linha de texto por lançamento (`\n`-separado) -- essa
  célula é a fonte confiável dos itens.
- Cada linha de lançamento tem o formato
  `DD/MM DESCRIÇÃO [CIDADE] VALOR[-]` -- o `-` no final indica
  CRÉDITO/estorno (pagamento de fatura, devolução), sem `-` é despesa
  normal (DEBIT).
- Linhas de troca de portador ("FELIPE LIMA Cartão 6516...") e subtotal
  ("Total paraFULANO valor") não começam com `DD/MM`, então o regex já as
  ignora sem tratamento especial.
- Nome do cartão (ex. "ELO NANQUIM PRIME") não tem posição fixa
  confiável no texto da página 1 -- em vez de tentar extrair, procura
  qual conta CREDIT já cadastrada (`contas.account_name`) aparece como
  substring no texto da fatura.
"""
import re

import pdfplumber

RE_TOTAL_VENCIMENTO = re.compile(r"R\$\s*([\d.]+,\d{2})\s+(\d{2}/\d{2}/\d{4})")
RE_LANCAMENTO = re.compile(r"^(\d{2}/\d{2})\s+(.*?)\s+([\d]{1,3}(?:\.\d{3})*,\d{2})\s*(-)?$")


def _valor_para_float(valor_str: str) -> float:
    return float(valor_str.replace(".", "").replace(",", "."))


def _ano_do_lancamento(mes_dia: str, ano_vencimento: int, mes_vencimento: int) -> int:
    """Lançamento sem ano (só DD/MM) -- assume o ano do vencimento, exceto
    quando o mês do lançamento é MAIOR que o mês de vencimento (fatura de
    janeiro pode ter lançamento de dezembro do ano anterior)."""
    mes_lanc = int(mes_dia.split("/")[1])
    return ano_vencimento - 1 if mes_lanc > mes_vencimento else ano_vencimento


def extrair_fatura(caminho_ou_bytes) -> dict:
    """Retorna {cartao_nome, vencimento, total_fatura, itens: [...]}.
    `itens` = [{"data": "YYYY-MM-DD", "descricao": str, "valor": float,
    "tipo": "DEBIT"|"CREDIT"}], sempre em ordem de aparição na fatura."""
    with pdfplumber.open(caminho_ou_bytes) as pdf:
        texto_pagina1 = pdf.pages[0].extract_text() or ""

        match_cabecalho = RE_TOTAL_VENCIMENTO.search(texto_pagina1)
        if not match_cabecalho:
            raise ValueError("Não encontrei 'Total da fatura'/'Vencimento' no PDF -- formato inesperado.")
        total_fatura = _valor_para_float(match_cabecalho.group(1))
        dia_v, mes_v, ano_v = match_cabecalho.group(2).split("/")
        vencimento = f"{ano_v}-{mes_v}-{dia_v}"

        linhas_lancamentos: list[str] = []
        for page in pdf.pages:
            for tabela in page.extract_tables():
                for linha in tabela:
                    for celula in linha:
                        if celula:
                            linhas_lancamentos.extend(celula.split("\n"))

    itens = []
    for linha in linhas_lancamentos:
        m = RE_LANCAMENTO.match(linha.strip())
        if not m:
            continue
        mes_dia, descricao, valor_str, credito = m.groups()
        ano = _ano_do_lancamento(mes_dia, int(ano_v), int(mes_v))
        dia, mes = mes_dia.split("/")
        itens.append({
            "data": f"{ano:04d}-{mes}-{dia}",
            "descricao": descricao.strip(),
            "valor": _valor_para_float(valor_str),
            "tipo": "CREDIT" if credito else "DEBIT",
        })

    return {
        "cartao_nome": None,  # preenchido por quem chama, via texto_pagina1 + nomes conhecidos
        "vencimento": vencimento,
        "total_fatura": total_fatura,
        "itens": itens,
        "_texto_pagina1": texto_pagina1,
    }


def identificar_cartao(texto_pagina1: str, nomes_conhecidos: list[str]) -> str | None:
    """Qual conta CREDIT já cadastrada (`contas.account_name`) aparece
    nessa fatura -- evita tentar extrair o nome do cartão de uma posição
    fixa no layout (não é confiável, ver docstring do módulo)."""
    for nome in nomes_conhecidos:
        if nome in texto_pagina1:
            return nome
    return None
