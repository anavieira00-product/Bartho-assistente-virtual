import os
import sqlite3
from datetime import datetime


# ==========================================
# CONFIGURAÇÃO
# ==========================================

BANCO = os.environ.get("BARTHO_DB_PATH", "bartho.db")

HORARIOS = [
    "08:00",
    "09:00",
    "10:00",
    "11:00",
    "12:00",
    "13:00",
    "14:00",
    "15:00",
    "16:00",
    "17:00"
]


# ==========================================
# CONECTAR AO BANCO
# ==========================================

def conectar():
    return sqlite3.connect(BANCO)


# ==========================================
# CRIAR BANCO
# ==========================================

def criar_banco():

    conexao = conectar()
    cursor = conexao.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS medicos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        especialidade TEXT NOT NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS dias_atendimento (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        medico_id INTEGER NOT NULL,
        dia_semana INTEGER NOT NULL,
        FOREIGN KEY (medico_id) REFERENCES medicos(id)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS agendamentos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        paciente TEXT NOT NULL,
        medico_id INTEGER NOT NULL,
        data TEXT NOT NULL,
        horario TEXT NOT NULL,
        FOREIGN KEY (medico_id) REFERENCES medicos(id)
    )
    """)

    cursor.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS horario_unico
    ON agendamentos (medico_id, data, horario)
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS atendimentos_humanos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mensagem TEXT NOT NULL,
        motivo TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'aguardando_atendente',
        criado_em TEXT NOT NULL
    )
    """)

    conexao.commit()
    conexao.close()


# ==========================================
# CADASTRAR MÉDICOS
# ==========================================

def cadastrar_medicos():

    conexao = conectar()
    cursor = conexao.cursor()

    medicos = [
        ("Dr. Lucas", "cardiologia"),
        ("Dra. Marina", "dermatologia"),
        ("Dra. Camila", "clinica_geral")
    ]

    for nome, especialidade in medicos:

        cursor.execute(
            """
            SELECT id
            FROM medicos
            WHERE nome = ?
            """,
            (nome,)
        )

        if cursor.fetchone() is None:

            cursor.execute(
                """
                INSERT INTO medicos
                (nome, especialidade)
                VALUES (?, ?)
                """,
                (nome, especialidade)
            )

    conexao.commit()
    conexao.close()


# ==========================================
# CADASTRAR DIAS DE ATENDIMENTO
# ==========================================

def cadastrar_dias_atendimento():

    conexao = conectar()
    cursor = conexao.cursor()

    # 0 = segunda
    # 1 = terça
    # 2 = quarta
    # 3 = quinta
    # 4 = sexta

    agenda_medicos = {
        "Dr. Lucas": [0, 2, 4],
        "Dra. Marina": [1, 3],
        "Dra. Camila": [0, 1, 2, 3, 4]
    }

    for nome_medico, dias in agenda_medicos.items():

        cursor.execute(
            """
            SELECT id
            FROM medicos
            WHERE nome = ?
            """,
            (nome_medico,)
        )

        resultado = cursor.fetchone()

        if resultado is None:
            continue

        medico_id = resultado[0]

        for dia in dias:

            cursor.execute(
                """
                SELECT id
                FROM dias_atendimento
                WHERE medico_id = ?
                AND dia_semana = ?
                """,
                (medico_id, dia)
            )

            if cursor.fetchone() is None:

                cursor.execute(
                    """
                    INSERT INTO dias_atendimento
                    (medico_id, dia_semana)
                    VALUES (?, ?)
                    """,
                    (medico_id, dia)
                )

    conexao.commit()
    conexao.close()


# ==========================================
# VERIFICAR ATENDIMENTO
# ==========================================

def verificar_atendimento(especialidade, dia_semana):

    conexao = conectar()
    cursor = conexao.cursor()

    cursor.execute(
        """
        SELECT medicos.nome
        FROM medicos

        JOIN dias_atendimento
        ON medicos.id = dias_atendimento.medico_id

        WHERE medicos.especialidade = ?
        AND dias_atendimento.dia_semana = ?
        """,
        (especialidade, dia_semana)
    )

    medicos_disponiveis = cursor.fetchall()

    conexao.close()

    return medicos_disponiveis


# ==========================================
# CRIAR AGENDAMENTO
# ==========================================

def criar_agendamento(
    paciente,
    medico,
    data,
    horario
):

    conexao = conectar()
    cursor = conexao.cursor()

    cursor.execute(
        """
        SELECT id
        FROM medicos
        WHERE nome = ?
        """,
        (medico,)
    )

    resultado = cursor.fetchone()

    if resultado is None:
        conexao.close()
        return False

    medico_id = resultado[0]

    try:

        cursor.execute(
            """
            INSERT INTO agendamentos
            (paciente, medico_id, data, horario)
            VALUES (?, ?, ?, ?)
            """,
            (
                paciente,
                medico_id,
                data,
                horario
            )
        )

        conexao.commit()

        return True

    except sqlite3.IntegrityError:

        return False

    finally:

        conexao.close()


# ==========================================
# CONSULTAR HORÁRIOS LIVRES
# ==========================================

def consultar_horarios_livres(
    medico,
    data
):

    conexao = conectar()
    cursor = conexao.cursor()

    cursor.execute(
        """
        SELECT id
        FROM medicos
        WHERE nome = ?
        """,
        (medico,)
    )

    resultado = cursor.fetchone()

    if resultado is None:

        conexao.close()

        return []

    medico_id = resultado[0]

    cursor.execute(
        """
        SELECT horario
        FROM agendamentos
        WHERE medico_id = ?
        AND data = ?
        """,
        (
            medico_id,
            data
        )
    )

    ocupados = [
        linha[0]
        for linha in cursor.fetchall()
    ]

    conexao.close()

    livres = [
        horario
        for horario in HORARIOS
        if horario not in ocupados
    ]

    return livres


# ==========================================
# BUSCAR AGENDAMENTOS DO PACIENTE
# ==========================================

def buscar_agendamentos(paciente):

    conexao = conectar()
    cursor = conexao.cursor()

    cursor.execute(
        """
        SELECT
            agendamentos.id,
            medicos.nome,
            medicos.especialidade,
            agendamentos.data,
            agendamentos.horario

        FROM agendamentos

        JOIN medicos
        ON agendamentos.medico_id = medicos.id

        WHERE LOWER(agendamentos.paciente)
        = LOWER(?)

        ORDER BY
            agendamentos.data,
            agendamentos.horario
        """,
        (paciente,)
    )

    consultas = cursor.fetchall()

    conexao.close()

    return consultas


# ==========================================
# CANCELAR AGENDAMENTO
# ==========================================

def cancelar_agendamento(
    agendamento_id
):

    conexao = conectar()
    cursor = conexao.cursor()

    cursor.execute(
        """
        DELETE FROM agendamentos
        WHERE id = ?
        """,
        (agendamento_id,)
    )

    cancelado = cursor.rowcount > 0

    conexao.commit()
    conexao.close()

    return cancelado


# ==========================================
# REMARCAR AGENDAMENTO
# ==========================================

def remarcar_agendamento(
    agendamento_id,
    nova_data,
    novo_horario
):

    conexao = conectar()
    cursor = conexao.cursor()

    # Descobrir o médico da consulta
    cursor.execute(
        """
        SELECT medico_id
        FROM agendamentos
        WHERE id = ?
        """,
        (agendamento_id,)
    )

    resultado = cursor.fetchone()

    if resultado is None:

        conexao.close()

        return False

    medico_id = resultado[0]

    # Verificar se o novo horário
    # está ocupado
    cursor.execute(
        """
        SELECT id
        FROM agendamentos

        WHERE medico_id = ?
        AND data = ?
        AND horario = ?
        AND id != ?
        """,
        (
            medico_id,
            nova_data,
            novo_horario,
            agendamento_id
        )
    )

    conflito = cursor.fetchone()

    if conflito is not None:

        conexao.close()

        return False

    # Atualizar consulta
    cursor.execute(
        """
        UPDATE agendamentos

        SET
            data = ?,
            horario = ?

        WHERE id = ?
        """,
        (
            nova_data,
            novo_horario,
            agendamento_id
        )
    )

    alterado = cursor.rowcount > 0

    conexao.commit()
    conexao.close()

    return alterado


# ==========================================
# CONSULTAR DIAS DO MÉDICO
# ==========================================

def consultar_dias_medico(medico):

    conexao = conectar()
    cursor = conexao.cursor()

    cursor.execute(
        """
        SELECT dias_atendimento.dia_semana

        FROM dias_atendimento

        JOIN medicos
        ON dias_atendimento.medico_id = medicos.id

        WHERE medicos.nome = ?

        ORDER BY dias_atendimento.dia_semana
        """,
        (medico,)
    )

    dias = [
        linha[0]
        for linha in cursor.fetchall()
    ]

    conexao.close()

    return dias

def consultar_especialidades():
    conexao = conectar()
    cursor = conexao.cursor()

    cursor.execute("""
        SELECT DISTINCT especialidade
        FROM medicos
        ORDER BY especialidade
    """)

    resultados = cursor.fetchall()

    conexao.close()

    return [resultado[0] for resultado in resultados]


def consultar_medicos_por_especialidade(especialidade):
    conexao = conectar()
    cursor = conexao.cursor()

    cursor.execute("""
        SELECT nome
        FROM medicos
        WHERE LOWER(especialidade) = LOWER(?)
        ORDER BY nome
    """, (especialidade,))

    resultados = cursor.fetchall()

    conexao.close()

    return [resultado[0] for resultado in resultados]

# ==========================================
# REGISTRAR ATENDIMENTO HUMANO
# ==========================================

def registrar_atendimento_humano(
    mensagem,
    motivo="assunto_fora_do_escopo"
):
    mensagem = (mensagem or "").strip()
    motivo = (motivo or "assunto_fora_do_escopo").strip()

    if not mensagem:
        return False

    conexao = conectar()
    cursor = conexao.cursor()

    try:
        cursor.execute(
            """
            INSERT INTO atendimentos_humanos
            (mensagem, motivo, status, criado_em)
            VALUES (?, ?, ?, ?)
            """,
            (
                mensagem,
                motivo,
                "aguardando_atendente",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            )
        )

        conexao.commit()
        return True

    except sqlite3.Error:
        return False

    finally:
        conexao.close()


# ==========================================
# INICIAR AGENDA
# ==========================================

if __name__ == "__main__":

    criar_banco()
    cadastrar_medicos()
    cadastrar_dias_atendimento()

    print(
        "Banco do Bartho configurado "
        "com sucesso."
    )