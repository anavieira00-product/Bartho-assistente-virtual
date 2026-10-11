from datetime import datetime, timedelta


# ==========================================
# REGRAS DO CONSULTÓRIO
# ==========================================

HORA_ABERTURA = 8
HORA_FECHAMENTO = 18
ULTIMO_HORARIO = 17

# Python considera:
# segunda = 0
# terça   = 1
# quarta  = 2
# quinta  = 3
# sexta   = 4
# sábado  = 5
# domingo = 6

DIAS_ATENDIMENTO = [0, 1, 2, 3, 4]


# ==========================================
# RESOLVER DATA
# ==========================================

def resolver_data(data_texto, data_atual=None):

    if data_atual is None:
        data_atual = datetime.now()

    if not isinstance(data_texto, str) or not data_texto.strip():
        return None

    texto = data_texto.lower().strip()

    if texto == "hoje":
        return data_atual

    if texto == "amanhã" or texto == "amanha":
        return data_atual + timedelta(days=1)

    dias_semana = {
        "segunda": 0,
        "segunda-feira": 0,
        "terça": 1,
        "terca": 1,
        "terça-feira": 1,
        "terca-feira": 1,
        "quarta": 2,
        "quarta-feira": 2,
        "quinta": 3,
        "quinta-feira": 3,
        "sexta": 4,
        "sexta-feira": 4,
        "sábado": 5,
        "sabado": 5,
        "domingo": 6
    }

    if texto in dias_semana:

        dia_desejado = dias_semana[texto]
        dia_atual = data_atual.weekday()

        diferenca = (dia_desejado - dia_atual) % 7

        return data_atual + timedelta(days=diferenca)

    return None


# ==========================================
# VERIFICAR FUNCIONAMENTO
# ==========================================

def consultorio_aberto(data):

    return data.weekday() in DIAS_ATENDIMENTO

# ==========================================
# VERIFICAR HORÁRIO
# ==========================================

def horario_permitido(horario_texto):

    try:
        horario = datetime.strptime(
            horario_texto,
            "%H:%M"
        )

        hora = horario.hour
        minuto = horario.minute

        # Por enquanto, nossa POC trabalha
        # apenas com consultas de hora em hora.
        if minuto != 0:
            return False

        return HORA_ABERTURA <= hora <= ULTIMO_HORARIO

    except ValueError:
        return False

# ==========================================
# TESTE
# ==========================================

if __name__ == "__main__":

    entrada = input("Informe uma data: ")

    data_resolvida = resolver_data(entrada)

    if data_resolvida is None:

        print("Não consegui interpretar essa data.")

    else:

        print(
            "Data resolvida:",
            data_resolvida.strftime("%d/%m/%Y")
        )

        if consultorio_aberto(data_resolvida):

            print("Consultório aberto ✅")
            print("Funcionamento: 08:00 às 18:00")
            print("Último horário para consulta: 17:00")

            horario = input(
                "Informe um horário (HH:MM): "
            )

            if horario_permitido(horario):

                print("Horário permitido ✅")

            else:

                print("Horário não permitido ❌")

                print(
                    "As consultas podem iniciar "
                    "entre 08:00 e 17:00."
                )

        else:

            print("Consultório fechado ❌")

            print(
                "Atendimento somente "
                "de segunda a sexta."
            )