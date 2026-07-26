"""Agrupa as ~50 categorias traduzidas (normalizacao.CATEGORIAS_PT) em
"grandes categorias" pra exibição no painel Fixas/Variáveis -- proposto
pelo agente e confirmado pelo usuário em 2026-07-24 (Saúde e Família
agrupadas juntas), separadas de novo em 2026-07-26 a pedido do usuário.
Categoria sem mapeamento cai em "Outros".

"PerMax" (empresa do usuário) não vem de nenhuma categoria da Pluggy --
existe só pra classificação manual (edição de transação, "→ fixo",
gasto fixo/variável adicionado à mão), por isso o set fica vazio.
"""

GRANDES_CATEGORIAS = {
    "Mercado": {"Mercado", "Alimentação e bebidas", "Delivery de comida"},
    "Combustível": {"Posto de gasolina", "Pedágio", "Estacionamento", "Manutenção do veículo"},
    "Casa": {"Moradia", "Casa e decoração", "Água", "Energia elétrica", "Serviços"},
    "Família": {
        "Escola", "Escolinha/Creche", "Faculdade", "Brinquedos e infantil",
        "Pet shop e veterinário", "Educação",
    },
    "Saúde": {"Saúde", "Farmácia", "Óptica", "Bem-estar e academia"},
    "Assinaturas": {
        "Serviços digitais", "Telecomunicações", "Internet", "Cursos online",
        "Tarifas bancárias", "Tarifas de cartão",
    },
    "Lazer": {
        "Lazer", "Restaurante", "Ingressos", "Jogos", "Viagem",
        "Aeroporto e passagens", "Hospedagem",
    },
    "Transporte": {"Táxi/App de transporte", "Aluguel de carro", "Bicicleta", "Automotivo"},
    # Adicionadas em 2026-07-26: sem elas, R$ 139 mil de despesa histórica
    # (a MAIOR fatia de todas) caía em "Outros" e a rosca do painel virava
    # um bloco cinza sem informação nenhuma. Ver diretiva.
    "Compras": {
        "Compras", "Compras online", "Livraria", "Eletrônicos", "Vestuário",
        "Material de escritório", "Artigos esportivos",
    },
    "Impostos e seguros": {"Impostos", "IOF", "Juros e multas", "Seguro"},
    "PerMax": set(),
}

_CATEGORIA_PARA_GRANDE = {
    categoria: grande
    for grande, categorias in GRANDES_CATEGORIAS.items()
    for categoria in categorias
}


def grande_categoria(categoria_pt: str) -> str:
    return _CATEGORIA_PARA_GRANDE.get(categoria_pt, "Outros")
