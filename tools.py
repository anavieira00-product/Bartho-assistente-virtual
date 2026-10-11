from agenda import (
    consultar_especialidades,
    consultar_medicos_por_especialidade,
    consultar_dias_medico,
    consultar_horarios_livres,
    criar_agendamento,
    buscar_agendamentos,
    cancelar_agendamento,
    remarcar_agendamento,
    registrar_atendimento_humano
)


# ==================================================
# TOOL: LISTAR ESPECIALIDADES
# ==================================================

def tool_listar_especialidades():
    return {
        "sucesso": True,
        "especialidades": consultar_especialidades()
    }


# ==================================================
# TOOL: CONSULTAR MÉDICOS
# ==================================================

def tool_consultar_medicos(especialidade):
    medicos = consultar_medicos_por_especialidade(
        especialidade
    )

    return {
        "sucesso": True,
        "especialidade": especialidade,
        "medicos": medicos
    }


# ==================================================
# TOOL: CONSULTAR DIAS DO MÉDICO
# ==================================================

def tool_consultar_dias_medico(medico):
    dias = consultar_dias_medico(
        medico
    )

    return {
        "sucesso": True,
        "medico": medico,
        "dias": dias
    }


# ==================================================
# TOOL: CONSULTAR HORÁRIOS
# ==================================================

def tool_consultar_horarios(medico, data):
    horarios = consultar_horarios_livres(
        medico,
        data
    )

    return {
        "sucesso": True,
        "medico": medico,
        "data": data,
        "horarios": horarios
    }


# ==================================================
# TOOL: CRIAR AGENDAMENTO
# ==================================================

def tool_criar_agendamento(
    paciente,
    medico,
    data,
    horario
):
    sucesso = criar_agendamento(
        paciente,
        medico,
        data,
        horario
    )

    return {
        "sucesso": sucesso,
        "paciente": paciente,
        "medico": medico,
        "data": data,
        "horario": horario
    }


# ==================================================
# TOOL: BUSCAR AGENDAMENTOS
# ==================================================

def tool_buscar_agendamentos(paciente):
    consultas = buscar_agendamentos(
        paciente
    )

    return {
        "sucesso": True,
        "paciente": paciente,
        "consultas": consultas
    }


# ==================================================
# TOOL: CANCELAR AGENDAMENTO
# ==================================================

def tool_cancelar_agendamento(agendamento_id):
    sucesso = cancelar_agendamento(
        agendamento_id
    )

    return {
        "sucesso": sucesso,
        "agendamento_id": agendamento_id
    }


# ==================================================
# TOOL: REMARCAR AGENDAMENTO
# ==================================================

def tool_remarcar_agendamento(
    agendamento_id,
    nova_data,
    novo_horario
):
    sucesso = remarcar_agendamento(
        agendamento_id,
        nova_data,
        novo_horario
    )

    return {
        "sucesso": sucesso,
        "agendamento_id": agendamento_id,
        "nova_data": nova_data,
        "novo_horario": novo_horario
    }


# ==================================================
# TOOL: ENCAMINHAR PARA HUMANO
# ==================================================

def tool_encaminhar_humano(
    mensagem,
    motivo="assunto_fora_do_escopo"
):
    sucesso = registrar_atendimento_humano(
        mensagem,
        motivo
    )

    return {
        "sucesso": sucesso,
        "mensagem": mensagem,
        "motivo": motivo
    }