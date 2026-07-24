"""Agrupa as ~50 categorias traduzidas (normalizacao.CATEGORIAS_PT) em
"grandes categorias" pra exibição no painel Fixas/Variáveis -- proposto
pelo agente e confirmado pelo usuário em 2026-07-24 (Saúde e Família
agrupadas juntas). Categoria sem mapeamento cai em "Outros".
"""

GRANDES_CATEGORIAS = {
    "Mercado": {"Mercado", "Alimentação e bebidas", "Delivery de comida"},
    "Combustível": {"Posto de gasolina", "Pedágio", "Estacionamento", "Manutenção do veículo"},
    "Casa": {"Moradia", "Casa e decoração", "Água", "Energia elétrica", "Serviços"},
    "Família e Saúde": {
        "Escola", "Escolinha/Creche", "Faculdade", "Brinquedos e infantil",
        "Pet shop e veterinário", "Saúde", "Farmácia", "Óptica", "Bem-estar e academia",
        "Educação",
    },
    "Assinaturas": {
        "Serviços digitais", "Telecomunicações", "Internet", "Cursos online",
        "Tarifas bancárias", "Tarifas de cartão",
    },
    "Lazer": {
        "Lazer", "Restaurante", "Ingressos", "Jogos", "Viagem",
        "Aeroporto e passagens", "Hospedagem",
    },
    "Transporte": {"Táxi/App de transporte", "Aluguel de carro", "Bicicleta"},
}

_CATEGORIA_PARA_GRANDE = {
    categoria: grande
    for grande, categorias in GRANDES_CATEGORIAS.items()
    for categoria in categorias
}


def grande_categoria(categoria_pt: str) -> str:
    return _CATEGORIA_PARA_GRANDE.get(categoria_pt, "Outros")
