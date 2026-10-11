class EstadoConversa:

    def __init__(self):
        self.limpar()

    def limpar(self):
        self.intencao = None
        self.especialidade = None
        self.especialidades = []
        self.data = None
        self.horario = None
        self.paciente = None

    def atualizar(self, interpretacao):

        intencao = interpretacao.get(
            "intencao"
        )

        especialidade = interpretacao.get(
            "especialidade"
        )

        especialidades = interpretacao.get(
            "especialidades",
            []
        )

        data = interpretacao.get(
            "data"
        )

        horario = interpretacao.get(
            "horario"
        )

        # ------------------------------------------
        # INTENÇÃO
        # ------------------------------------------

        if (
            intencao
            and intencao != "nao_informado"
        ):
            self.intencao = intencao

        # ------------------------------------------
        # ESPECIALIDADES
        # ------------------------------------------

        if isinstance(
            especialidades,
            list
        ):

            for item in especialidades:

                if (
                    item
                    and item != "nao_informado"
                    and item not in self.especialidades
                ):
                    self.especialidades.append(
                        item
                    )

        # Compatibilidade com o campo antigo.
        if (
            especialidade
            and especialidade != "nao_informado"
        ):

            if (
                especialidade
                not in self.especialidades
            ):
                self.especialidades.append(
                    especialidade
                )

        # A especialidade principal continua sendo
        # a primeira da lista.
        if self.especialidades:

            self.especialidade = (
                self.especialidades[0]
            )

        # ------------------------------------------
        # DATA
        # ------------------------------------------

        if (
            data
            and data != "nao_informado"
        ):
            self.data = data

        # ------------------------------------------
        # HORÁRIO
        # ------------------------------------------

        if (
            horario
            and horario != "nao_informado"
        ):
            self.horario = horario

    def definir_especialidade_atual(
        self,
        especialidade
    ):

        if (
            especialidade
            and especialidade != "nao_informado"
        ):

            self.especialidade = (
                especialidade
            )

            if (
                especialidade
                not in self.especialidades
            ):

                self.especialidades.append(
                    especialidade
                )

    def definir_paciente(
        self,
        paciente
    ):

        if paciente:

            self.paciente = (
                paciente.strip()
            )

    def dados(self):

        return {
            "intencao": self.intencao,
            "especialidade": self.especialidade,
            "especialidades": list(
                self.especialidades
            ),
            "data": self.data,
            "horario": self.horario,
            "paciente": self.paciente
        }

    def falta_para_agendar(self):

        if not self.especialidades:
            return "especialidade"

        if not self.data:
            return "data"

        if not self.horario:
            return "horario"

        if not self.paciente:
            return "paciente"

        return None