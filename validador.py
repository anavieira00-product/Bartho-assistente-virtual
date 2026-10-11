# ==========================================
# VALIDADOR DO BARTHO
# ==========================================


# ==========================================
# VALIDAR INTENÇÃO
# ==========================================

def validar_intencao(mensagem, intencao_llm):

    texto = mensagem.lower().strip()

    # --------------------------------------
    # REMARCAR
    # --------------------------------------

    expressoes_remarcar = [
        "remarcar",
        "remarcação",
        "remarcacao",
        "mudar minha consulta",
        "alterar minha consulta",
        "trocar minha consulta",
        "mudar o horário",
        "mudar o horario",
        "trocar o horário",
        "trocar o horario"
    ]

    for expressao in expressoes_remarcar:

        if expressao in texto:
            return "remarcar"

    # --------------------------------------
    # CANCELAR
    # --------------------------------------

    expressoes_cancelar = [
        "cancelar",
        "cancelamento",
        "desmarcar",
        "desmarcar minha consulta"
    ]

    for expressao in expressoes_cancelar:

        if expressao in texto:
            return "cancelar"

    # Se não houver uma expressão explícita,
    # mantemos a interpretação do LLM.
    return intencao_llm


# ==========================================
# VALIDAR HORÁRIO
# ==========================================

def validar_horario(mensagem, horario_llm):

    texto = mensagem.lower().strip()

    # Se o LLM já encontrou um horário,
    # mantemos a interpretação dele.
    if horario_llm != "nao_informado":
        return horario_llm

    # --------------------------------------
    # MANHÃ
    # --------------------------------------

    expressoes_manha = [
        "de manhã",
        "pela manhã",
        "de manha",
        "pela manha",
        "período da manhã",
        "periodo da manha",
        "parte da manhã",
        "parte da manha"
    ]

    for expressao in expressoes_manha:

        if expressao in texto:
            return "manha"

    # --------------------------------------
    # TARDE
    # --------------------------------------

    expressoes_tarde = [
        "à tarde",
        "a tarde",
        "pela tarde",
        "período da tarde",
        "periodo da tarde",
        "parte da tarde",
        "depois do almoço",
        "depois do almoco",
        "após o almoço",
        "apos o almoco",
        "depois de almoçar",
        "depois de almocar"
    ]

    for expressao in expressoes_tarde:

        if expressao in texto:
            return "tarde"

    return "nao_informado"


# ==========================================
# TESTE MANUAL
# ==========================================

if __name__ == "__main__":

    mensagem = "Quero remarcar minha consulta"

    intencao = validar_intencao(
        mensagem,
        "cancelar"
    )

    print("Intenção validada:", intencao)

    horario = validar_horario(
        "Tem cardiologista sexta à tarde?",
        "nao_informado"
    )

    print("Horário validado:", horario)