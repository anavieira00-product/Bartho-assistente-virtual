import os
import re
import uuid
import unicodedata
import json
import sqlite3
from datetime import datetime, timedelta, date
from difflib import SequenceMatcher
from pathlib import Path
from threading import RLock
from contextlib import redirect_stdout
from io import StringIO

from flask import Flask, request, jsonify
from flask_cors import CORS

os.environ["BARTHO_MODO_WEB"] = "1"

from bartho import processar_mensagem, PerguntaInterativaWeb
from agenda import (
    criar_banco, cadastrar_medicos, cadastrar_dias_atendimento,
    consultar_especialidades, consultar_medicos_por_especialidade,
    consultar_dias_medico, verificar_atendimento, consultar_horarios_livres,
    criar_agendamento,
)
from calendario import resolver_data, consultorio_aberto, horario_permitido

app = Flask(__name__)
app.secret_key = os.environ.get("BARTHO_SECRET_KEY") or os.urandom(32).hex()
origem_permitida = os.environ.get("BARTHO_ALLOWED_ORIGIN", "https://anavieira00-product.github.io")
CORS(app, resources={r"/*": {"origins": origem_permitida}})

# Estado separado por conversa e persistido em SQLite, em vez de ficar apenas na RAM.
# Em produção, configure BARTHO_SESSION_DB_PATH para um disco persistente do Render
# (por exemplo, /var/data/bartho_sessions.db).
_SESSION_DB = os.environ.get("BARTHO_SESSION_DB_PATH", "bartho_sessions.db")
_LOCK = RLock()


def _conectar_sessoes():
    caminho = Path(_SESSION_DB)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(caminho), timeout=10)
    con.execute("CREATE TABLE IF NOT EXISTS conversas_web (id TEXT PRIMARY KEY, estado TEXT NOT NULL, atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    con.commit()
    return con


def _salvar_sessao(conversa_id, estado):
    with _conectar_sessoes() as con:
        con.execute(
            "INSERT INTO conversas_web (id, estado, atualizado_em) VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(id) DO UPDATE SET estado=excluded.estado, atualizado_em=CURRENT_TIMESTAMP",
            (conversa_id, json.dumps(dict(estado), ensure_ascii=False)),
        )


def _carregar_sessao(conversa_id):
    with _conectar_sessoes() as con:
        linha = con.execute("SELECT estado FROM conversas_web WHERE id = ?", (conversa_id,)).fetchone()
    if not linha:
        return SessaoPersistente(conversa_id, {})
    try:
        estado = json.loads(linha[0])
        if not isinstance(estado, dict):
            estado = {}
    except (TypeError, json.JSONDecodeError):
        estado = {}
    return SessaoPersistente(conversa_id, estado)


class SessaoPersistente(dict):
    """Dicionário compatível com o fluxo atual que salva cada alteração no SQLite."""
    def __init__(self, conversa_id, estado=None):
        self.conversa_id = conversa_id
        super().__init__(estado or {})

    def _persistir(self):
        _salvar_sessao(self.conversa_id, self)

    def __setitem__(self, chave, valor):
        super().__setitem__(chave, valor)
        self._persistir()

    def update(self, *args, **kwargs):
        super().update(*args, **kwargs)
        self._persistir()

    def clear(self):
        super().clear()
        self._persistir()

    def pop(self, chave, *args):
        resultado = super().pop(chave, *args)
        self._persistir()
        return resultado

ESPECIALIDADES = {
    "cardiologia": ("cardiologia", "cardiologista", "coração", "coracao", "médico do coração", "medico do coracao"),
    "dermatologia": ("dermatologia", "dermatologista"),
    "clinica_geral": ("clínica geral", "clinica geral", "clínico geral", "clinico geral", "clínico gera", "clinico gera", "clinica gera", "clínico geral.", "medicina geral"),
}

def normalizar(texto):
    texto = unicodedata.normalize("NFD", (texto or "").lower())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9:/\s-]", " ", texto).split())

def extrair_especialidade(texto):
    t = normalizar(texto)
    # Correspondência direta para os nomes e sinônimos mais comuns.
    for canonica, aliases in ESPECIALIDADES.items():
        if any(re.search(r"\b" + re.escape(normalizar(a)) + r"\b", t) for a in aliases):
            return canonica
    # Tolerância a pequenos erros de digitação em respostas curtas, como "clinico gera".
    if len(t.split()) <= 4:
        candidatos = {
            "clinica_geral": ["clinico geral", "clinica geral", "clinico gera", "clinica gera"],
            "cardiologia": ["cardiologia", "cardiologista"],
            "dermatologia": ["dermatologia", "dermatologista"],
        }
        for canonica, aliases in candidatos.items():
            if any(SequenceMatcher(None, t, alias).ratio() >= 0.82 for alias in aliases):
                return canonica
    return None

def sim(texto):
    return normalizar(texto) in {"sim", "s", "claro", "quero", "pode", "pode ser", "isso", "confirmo", "ok", "okay"}

def nao(texto):
    return normalizar(texto) in {"nao", "n", "nao quero", "agora nao", "depois"}

DIAS_SEMANA = {
    "segunda-feira": 0, "segunda": 0,
    "terca-feira": 1, "terca": 1,
    "quarta-feira": 2, "quarta": 2,
    "quinta-feira": 3, "quinta": 3,
    "sexta-feira": 4, "sexta": 4,
    "sabado": 5, "domingo": 6,
}

def identificar_dia_semana(texto):
    """Encontra um dia da semana dentro de frases naturais, como 'e quarta?' ."""
    t = normalizar(texto)
    # Verifica os nomes longos primeiro para evitar correspondências parciais.
    for nome in sorted(DIAS_SEMANA, key=len, reverse=True):
        if re.search(r"\b" + re.escape(nome) + r"\b", t):
            return nome, DIAS_SEMANA[nome]
    return None, None

def pergunta_especialidades(texto):
    t = normalizar(texto)
    menciona_especialidade = "especialidade" in t or "especialidades" in t
    pergunta = any(x in t for x in (
        "quais", "qual", "que", "tem", "atende", "atendem", "oferece", "oferecem",
        "voces tem", "voces atendem", "a clinica tem"
    ))
    return menciona_especialidade and pergunta

def pergunta_horarios(texto):
    t = normalizar(texto)
    return any(x in t for x in (
        "quais horarios", "qual horario", "horarios disponiveis", "consultar horarios",
        "ver horarios", "tem horario", "tem horarios", "quero horario", "quero horarios"
    ))

def identificar_convenio(texto):
    """Identifica o convênio mencionado sem depender de uma frase exata."""
    t = normalizar(texto)
    if "unimed" in t:
        return "Unimed"
    if "bradesco" in t:
        return "Bradesco Saúde"
    if "sulamerica" in t:
        return "SulAmérica"
    return None

def resolver_data_flexivel(texto):
    """Resolve hoje/amanhã, dias da semana e datas como 16/10 ou 16/10/2026."""
    t = normalizar(texto)
    # Extrai data explícita mesmo quando vem dentro de uma frase ("quero dia 16/10/2026").
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", t)
    if m:
        dia, mes, ano = int(m.group(1)), int(m.group(2)), m.group(3)
        ano = int(ano) if ano else datetime.now().year
        if ano < 100:
            ano += 2000
        try:
            data = date(ano, mes, dia)
            hoje = date.today()
            if not m.group(3) and data < hoje:
                data = date(ano + 1, mes, dia)
            return datetime.combine(data, datetime.min.time())
        except ValueError:
            return None
    if "hoje" in t:
        return datetime.now()
    if "amanha" in t:
        return datetime.now() + timedelta(days=1)
    dia_nome, _ = identificar_dia_semana(texto)
    if dia_nome:
        try:
            return resolver_data(dia_nome)
        except Exception:
            return None
    try:
        return resolver_data(texto)
    except Exception:
        return None


def pedido_cancelamento(texto):
    t = normalizar(texto)
    return t in {"cancela", "cancelar", "cancela isso", "cancelar agendamento", "cancela consulta", "quero cancelar", "desisto", "desistir"}


def pedido_nova_data(texto):
    t = normalizar(texto)
    tem_data = bool(re.search(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b", t))
    dia, _ = identificar_dia_semana(texto)
    return tem_data or dia is not None or "hoje" in t or "amanha" in t


def pedido_novo_horario(texto):
    t = normalizar(texto)
    return bool(re.search(r"\b(?:[01]?\d|2[0-3])(?::[0-5]\d|h(?:[0-5]\d)?)?\b", t)) and any(x in t for x in (" as ", " horario", " as ", " para ", " as "))


def extrair_horario(texto):
    t = normalizar(texto)
    m = re.search(r"\b([01]?\d|2[0-3])(?::([0-5]\d)|h(?:([0-5]\d))?)\b", t)
    if not m:
        # Em frases, permite "às 18" / "as 18" sem confundir datas.
        m = re.search(r"\bas\s+([01]?\d|2[0-3])\b", t)
        if not m:
            return None
        hora, minuto = int(m.group(1)), 0
    else:
        hora, minuto = int(m.group(1)), int(m.group(2) or m.group(3) or 0)
    return f"{hora:02d}:{minuto:02d}"

def faq(texto, convenio=None):
    t = normalizar(texto)
    # Perguntas de cobertura precisam ser tratadas antes da FAQ genérica de convênios.
    # Não afirmamos cobertura garantida: ela depende das regras do plano do paciente.
    pergunta_cobertura = any(x in t for x in (
        "cobre consulta", "cobre consultas", "cobre a consulta", "cobertura da consulta",
        "meu plano cobre", "plano cobre consulta", "consulta e coberta",
        "atende consulta pelo convenio", "posso consultar pelo convenio",
        "cobre", "tem cobertura", "e coberto", "e coberta", "cobertura"
    )) and any(x in t for x in ("consulta", "plano", "convenio", "cobre", "cobertura", "coberto", "coberta"))
    if pergunta_cobertura:
        plano = convenio or identificar_convenio(texto)
        if plano:
            return (f"A Clínica Bem aceita {plano}, mas não consigo confirmar apenas por aqui se a sua consulta "
                    f"está coberta. Isso depende do produto contratado, da rede credenciada e das regras do plano. "
                    f"Confirme a cobertura da consulta diretamente com a {plano} antes do atendimento.")
        return ("A Clínica Bem aceita Unimed, Bradesco Saúde e SulAmérica, mas não consigo confirmar a cobertura "
                "sem saber qual é o seu plano. A cobertura depende do produto contratado e das regras da operadora.")
    if any(x in t for x in ("endereco", "onde fica", "localizacao", "local da clinica", "como chegar")):
        return "O endereço cadastrado da Clínica Bem é Avenida x, 1, Belo Horizonte/MG."
    if any(x in t for x in ("quanto custa", "qual o valor", "valor da consulta", "preco da consulta", "preço da consulta")):
        return "A consulta particular custa R$ 70,00."
    if any(x in t for x in ("unimed", "bradesco saude", "sulamerica", "convenio", "convênio", "planos aceitos")):
        return "A Clínica Bem aceita Unimed, Bradesco Saúde e SulAmérica. A cobertura depende das condições do plano contratado."
    if pergunta_especialidades(texto):
        return "A Clínica Bem atende Cardiologia, Dermatologia e Clínica Geral."
    if any(x in t for x in ("horario de funcionamento", "que horas abre", "que horas fecha", "funcionamento da clinica", "horario da clinica")):
        return "A Clínica Bem funciona de segunda a sexta-feira, das 08:00 às 18:00. O último horário de consulta começa às 17:00."
    if any(x in t for x in ("documentos", "o que levar", "preciso levar", "documento para consulta")):
        return "Leve um documento oficial com foto. Se utilizar convênio, leve também a carteirinha, quando aplicável. Menores de idade devem estar acompanhados por responsável."
    if any(x in t for x in ("atraso", "chegar atrasado", "tolerancia")):
        return "A tolerância para atrasos é de até 15 minutos."
    if any(x in t for x in ("cirurgia", "exame de sangue", "exames", "emergencia", "emergência")):
        if "emergencia" in t:
            return "A Clínica Bem não presta atendimento de emergência. Em caso de urgência, procure um serviço de urgência/emergência."
        return "A Clínica Bem realiza consultas médicas nas especialidades cadastradas, mas não realiza cirurgias nem exames de sangue."
    return None

def responder(texto):
    return jsonify({"resposta": texto, "aguardando_resposta": False})

def iniciar_agendamento(sessao, texto=""):
    """Inicia o agendamento aproveitando os dados já presentes na mensagem."""
    sessao.clear()
    especialidade = extrair_especialidade(texto)

    if especialidade:
        medicos = consultar_medicos_por_especialidade(especialidade)
        if medicos:
            medico = medicos[0]
            dias = consultar_dias_medico(medico)
            nomes_dias = [
                "segunda-feira", "terça-feira", "quarta-feira",
                "quinta-feira", "sexta-feira", "sábado", "domingo"
            ]
            dias_texto = ", ".join(
                nomes_dias[d] for d in dias if isinstance(d, int) and 0 <= d < 7
            )
            sessao.update({
                "etapa": "confirmar_horarios",
                "especialidade": especialidade,
                "medico": medico,
                "dias_atendimento": dias,
            })
            nome = {
                "cardiologia": "Cardiologia",
                "dermatologia": "Dermatologia",
                "clinica_geral": "Clínica Geral",
            }[especialidade]
            if dias_texto:
                return responder(
                    f"Temos atendimento de {nome} com {medico}. "
                    f"Os dias de atendimento são: {dias_texto}. "
                    "Quer consultar os horários disponíveis?"
                )
            return responder(
                f"Temos atendimento de {nome} com {medico}. "
                "Quer consultar os horários disponíveis?"
            )

    sessao["etapa"] = "especialidade"
    return responder(
        "Claro! Qual especialidade você procura: Cardiologia, Dermatologia ou Clínica Geral?"
    )

def processar_fluxo(texto, sessao):
    etapa = sessao.get("etapa")
    t = normalizar(texto)

    # Permite perguntas informativas durante uma jornada sem perder a etapa.
    resposta_faq = faq(texto, sessao.get("convenio_informado"))
    if resposta_faq:
        # Se a pessoa pergunta pelas especialidades durante outro fluxo, passamos
        # para a escolha de especialidade em vez de deixar uma etapa antiga ativa.
        if pergunta_especialidades(texto):
            sessao["etapa"] = "especialidade"
        return responder(resposta_faq)

    if etapa == "especialidade":
        esp = extrair_especialidade(texto)
        if not esp:
            return responder("Não identifiquei a especialidade. Você procura Cardiologia, Dermatologia ou Clínica Geral?")
        medicos = consultar_medicos_por_especialidade(esp)
        if not medicos:
            return responder("Não encontrei médico cadastrado para essa especialidade. Posso ajudar com outra especialidade?")
        sessao["especialidade"] = esp
        sessao["medico"] = medicos[0]
        sessao["etapa"] = "confirmar_horarios"
        nome = {"cardiologia":"Cardiologia","dermatologia":"Dermatologia","clinica_geral":"Clínica Geral"}[esp]
        return responder(f"Temos atendimento de {nome}. Quer consultar os horários disponíveis?")

    if etapa == "confirmar_horarios":
        if pergunta_horarios(texto):
            sessao["etapa"] = "data"
            dias = sessao.get("dias_atendimento") or consultar_dias_medico(sessao["medico"])
            nomes_dias = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
            dias_texto = ", ".join(nomes_dias[d] for d in dias if isinstance(d, int) and 0 <= d < 7)
            return responder(f"Claro! Para consultar a disponibilidade de {sessao.get('especialidade', '').replace('_', ' ').title()}, informe a data ou um dia de atendimento. {sessao['medico']} atende em {dias_texto}.")
        dia_nome, dia_numero = identificar_dia_semana(texto)
        if dia_nome:
            dias_medico = sessao.get("dias_atendimento") or consultar_dias_medico(sessao["medico"])
            if dia_numero not in dias_medico:
                sessao["etapa"] = "data"
                nomes_dias = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
                dias_texto = ", ".join(nomes_dias[d] for d in dias_medico if isinstance(d, int) and 0 <= d < 7)
                return responder(f"A Dra. {sessao.get('medico', '').replace('Dra. ', '')} não atende na {dia_nome}. Os dias de atendimento são: {dias_texto}. Qual desses dias você prefere?")
            # O usuário já indicou o dia desejado; não precisa responder sim/não.
            sessao["etapa"] = "data"
            return processar_fluxo(dia_nome, sessao)
        if nao(texto):
            sessao.clear()
            return responder("Tudo bem! Quando quiser, posso ajudar a consultar os horários ou agendar uma consulta.")
        if not sim(texto):
            return responder("Claro! Você pode me dizer diretamente o dia que prefere, como terça ou quinta, ou responder sim para consultar os horários disponíveis.")
        sessao["etapa"] = "data"
        dias = sessao.get("dias_atendimento") or consultar_dias_medico(sessao["medico"])
        nomes_dias = [
            "segunda-feira", "terça-feira", "quarta-feira",
            "quinta-feira", "sexta-feira", "sábado", "domingo"
        ]
        dias_texto = ", ".join(
            nomes_dias[d] for d in dias if isinstance(d, int) and 0 <= d < 7
        )
        if dias_texto:
            return responder(
                f"Qual data você prefere? {sessao['medico']} atende em {dias_texto}. "
                "Você pode informar uma data ou um desses dias."
            )
        return responder(
            "Para qual dia você gostaria de consultar os horários? "
            "Pode dizer hoje, amanhã ou um dia da semana."
        )

    if etapa == "data":
        dia_nome, _ = identificar_dia_semana(texto)
        data = resolver_data_flexivel(texto)
        if data is None:
            return responder("Não consegui identificar a data. Informe hoje, amanhã ou um dia da semana, como terça-feira.")
        if not consultorio_aberto(data):
            return responder("A clínica funciona de segunda a sexta-feira. Escolha um dia útil, por favor.")
        medico = sessao["medico"]
        dias = consultar_dias_medico(medico)
        if data.weekday() not in dias:
            dias_nomes = ["segunda-feira","terça-feira","quarta-feira","quinta-feira","sexta-feira","sábado","domingo"]
            disponiveis = ", ".join(dias_nomes[d] for d in dias)
            return responder(f"{medico} não atende nesse dia. Os dias de atendimento são: {disponiveis}. Qual desses dias prefere?")
        sessao["data"] = data.strftime("%Y-%m-%d")
        livres = consultar_horarios_livres(medico, sessao["data"])
        livres = [h for h in livres if horario_permitido(h)]
        if not livres:
            return responder("Não encontrei horários livres nessa data. Informe outro dia, por favor.")
        sessao["horarios_livres"] = livres
        sessao["etapa"] = "horario"
        solicitado = sessao.pop("horario_solicitado", None)
        if solicitado:
            if solicitado in livres:
                sessao["horario"] = solicitado
                sessao["etapa"] = "paciente"
                return responder(f"O horário das {solicitado} está disponível. Qual é o nome completo do paciente?")
            return responder(f"Às {solicitado}, não há horário disponível nessa data. Os horários disponíveis são: " + ", ".join(livres) + ". Qual horário você prefere?")
        return responder("Horários disponíveis: " + ", ".join(livres) + ". Qual horário você prefere?")

    if etapa == "horario":
        if pedido_nova_data(texto):
            horario_pedido = extrair_horario(texto)
            if horario_pedido:
                sessao["horario_solicitado"] = horario_pedido
            sessao.pop("data", None)
            sessao.pop("horario", None)
            sessao["etapa"] = "data"
            return processar_fluxo(texto, sessao)
        horario = t.replace("h", ":00") if re.fullmatch(r"\d{1,2}h", t) else t
        # Aceita "18"/"18h" e frases como "sexta às 18" sem perder o pedido de trocar a data.
        m_hora = re.search(r"\b([01]?\d|2[0-3])(?::([0-5]\d)|h(?:([0-5]\d))?)?\b", t)
        if m_hora and not re.fullmatch(r"\d{1,2}(?::\d{2})?", horario):
            hora = int(m_hora.group(1))
            minuto = int(m_hora.group(2) or m_hora.group(3) or 0)
            horario = f"{hora:02d}:{minuto:02d}"
        if re.fullmatch(r"\d{1,2}", horario):
            horario = horario.zfill(2) + ":00"
        if horario not in sessao.get("horarios_livres", []):
            return responder("Esse horário não está na lista de disponíveis. Escolha um destes: " + ", ".join(sessao.get("horarios_livres", [])) + ".")
        sessao["horario"] = horario
        sessao["etapa"] = "paciente"
        return responder("Perfeito. Qual é o nome completo do paciente?")

    if etapa == "paciente":
        nome = texto.strip()
        if len(nome) < 2 or nome.isdigit():
            return responder("Pode informar o nome do paciente, por favor?")
        sessao["paciente"] = nome
        data_br = sessao["data"]
        from datetime import datetime
        data_br = datetime.strptime(data_br, "%Y-%m-%d").strftime("%d/%m/%Y")
        sessao["etapa"] = "confirmacao"
        return responder(f"Confirme os dados: {sessao['especialidade'].replace('_',' ').title()}, {data_br} às {sessao['horario']}, paciente {nome}. Deseja confirmar?")

    if etapa == "confirmacao":
        if nao(texto):
            sessao.clear()
            return responder("Tudo bem, não fiz o agendamento. Se quiser, podemos começar novamente.")
        if not sim(texto):
            return responder("Para concluir, responda sim para confirmar ou não para cancelar.")
        sucesso = criar_agendamento(sessao["paciente"], sessao["medico"], sessao["data"], sessao["horario"])
        if sucesso:
            from datetime import datetime
            data_br = datetime.strptime(sessao["data"], "%Y-%m-%d").strftime("%d/%m/%Y")
            resultado = f"Consulta agendada com sucesso! {data_br} às {sessao['horario']}, com {sessao['medico']}."
        else:
            resultado = "Esse horário acabou de ficar indisponível. Vamos escolher outro horário."
        sessao.clear()
        return responder(resultado)

    return None

@app.get("/health")
def health():
    return jsonify({"assistente": "Bartho", "status": "ok"})

@app.post("/mensagem")
def mensagem():
    dados = request.get_json(silent=True) or {}
    texto = str(dados.get("mensagem", "")).strip()
    conversa_id = str(dados.get("conversa_id", "")).strip()
    if not texto:
        return jsonify({"erro": "Mensagem não informada."}), 400
    if not conversa_id:
        return jsonify({"erro": "Identificador da conversa ausente. Atualize a página e tente novamente."}), 400

    with _LOCK:
        sessao = _carregar_sessao(conversa_id)
        texto_normalizado = normalizar(texto)

        # Cancelamento pode ser solicitado em qualquer etapa, inclusive quando o sistema
        # está aguardando um horário ou uma confirmação.
        if pedido_cancelamento(texto):
            sessao.clear()
            return responder("Tudo bem, cancelei esta solicitação. Nenhum agendamento foi confirmado. Se quiser, podemos começar novamente.")

        if texto_normalizado in {"oi", "ola", "opa", "bom dia", "boa tarde", "boa noite", "oi tudo bem", "ola tudo bem"} and not sessao.get("etapa"):
            return responder("Olá! 👋 Sou o Bartho, assistente virtual da Clínica Bem. Como posso ajudar?")

        # Um pedido explícito novo inicia fluxo novo.
        if any(x in texto_normalizado for x in (
            "quero agendar", "agendar consulta", "marcar consulta", "quero marcar",
            "gostaria de agendar", "gostaria de marcar", "preciso agendar", "preciso marcar"
        )):
            return iniciar_agendamento(sessao, texto)

        # Se a pessoa acabou de perguntar quais especialidades existem e responde
        # apenas com uma delas (ex.: "Cardiologia"), aproveitamos o contexto para
        # iniciar o agendamento. Esse estado é salvo no SQLite junto com a sessão.
        if sessao.get("etapa") == "apos_lista_especialidades":
            especialidade = extrair_especialidade(texto)
            if especialidade:
                return iniciar_agendamento(sessao, texto)
            # Não prende a conversa a esse contexto se a próxima mensagem for outra coisa.
            sessao.clear()

        # Reconhece quando a pessoa informa o próprio convênio, sem repetir a FAQ
        # genérica como se ela tivesse perguntado novamente quais planos são aceitos.
        convenio_mencionado = identificar_convenio(texto)
        mensagem_informa_convenio = any(x in texto_normalizado for x in (
            "tenho", "uso", "sou cliente", "meu convenio", "meu plano", "atendo pelo"
        ))
        if convenio_mencionado and mensagem_informa_convenio and not any(
            x in texto_normalizado for x in ("quais convenios", "quais planos", "aceita convenio", "aceita convenios")
        ):
            sessao["convenio_informado"] = convenio_mencionado
            return responder(
                f"Entendi, você tem {convenio_mencionado}. A Clínica Bem aceita esse convênio. "
                "A cobertura de uma consulta depende do produto contratado e das regras da operadora. "
                "Você quer saber sobre a cobertura ou deseja agendar uma consulta?"
            )

        if sessao.get("etapa"):
            resultado = processar_fluxo(texto, sessao)
            if resultado is not None:
                return resultado

        resposta_faq = faq(texto, sessao.get("convenio_informado"))
        if resposta_faq:
            # Registra somente a pergunta sobre especialidades como contexto de
            # continuação. As demais perguntas frequentes continuam sem alterar o fluxo.
            if pergunta_especialidades(texto):
                # Uma resposta simples como "Dermatologia" na mensagem seguinte
                # inicia/continua a escolha de especialidade, sem cair no fluxo legado.
                sessao["etapa"] = "apos_lista_especialidades"
            return responder(resposta_faq)

        # Evita enviar perguntas simples de agenda para o fluxo legado de terminal,
        # que não consegue manter perguntas interativas entre requisições HTTP.
        if pergunta_horarios(texto):
            sessao["etapa"] = "especialidade"
            return responder("Claro! Para consultar os horários, primeiro me diga qual especialidade você procura: Cardiologia, Dermatologia ou Clínica Geral.")

    captura = StringIO()
    try:
        with redirect_stdout(captura):
            resultado = processar_mensagem(texto)
        saida = captura.getvalue().strip()
        resposta = ""
        for linha in reversed(saida.splitlines()):
            linha = linha.strip()
            if linha.startswith("Bartho:"):
                resposta = linha.split("Bartho:", 1)[1].strip()
                break
        if not resposta and isinstance(resultado, str):
            resposta = resultado.strip()
        if not resposta:
            resposta = "Como posso ajudar?"
        return jsonify({"resposta": resposta, "resultado": resultado})
    except PerguntaInterativaWeb as pergunta:
        # The legacy terminal flow cannot resume its Python call stack across HTTP requests.
        # Avoid pretending the interactive flow is resumable; return a safe explanation.
        app.logger.warning("Fluxo legado solicitou entrada web sem estado de continuação: %s", pergunta.pergunta)
        return jsonify({
            "resposta": "Posso ajudar com informações da clínica e iniciar um agendamento. Para continuar essa solicitação, vamos começar pelo botão de agendamento ou escreva 'Quero agendar uma consulta'.",
            "aguardando_resposta": False
        })
    except Exception:
        app.logger.exception("Erro ao processar mensagem do Bartho")
        return jsonify({"erro": "Ocorreu uma falha ao processar sua mensagem. Tente novamente."}), 500

# Inicializa tabelas e cadastros necessários para o MVP.
try:
    criar_banco()
    cadastrar_medicos()
    cadastrar_dias_atendimento()
except Exception:
    app.logger.exception("Não foi possível inicializar a agenda do Bartho")

if __name__ == "__main__":
    porta = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=porta, debug=False)
