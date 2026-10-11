from pathlib import Path
import re
import unicodedata


ARQUIVO_BASE = "base_conhecimento.txt"


def normalizar_texto(texto):
    texto = texto.lower()

    texto = unicodedata.normalize(
        "NFD",
        texto
    )

    texto = "".join(
        caractere
        for caractere in texto
        if unicodedata.category(caractere) != "Mn"
    )

    texto = re.sub(
        r"[^a-z0-9\s]",
        " ",
        texto
    )

    return " ".join(texto.split())


def carregar_base():
    caminho = Path(ARQUIVO_BASE)

    if not caminho.exists():
        return ""

    return caminho.read_text(
        encoding="utf-8"
    )


def dividir_em_blocos(texto):
    blocos = []
    bloco_atual = []

    for linha in texto.splitlines():

        linha = linha.strip()

        if not linha:
            continue

        if (
            linha.startswith("[")
            and linha.endswith("]")
        ):
            if bloco_atual:
                blocos.append(
                    "\n".join(bloco_atual)
                )

            bloco_atual = [linha]

        else:
            bloco_atual.append(linha)

    if bloco_atual:
        blocos.append(
            "\n".join(bloco_atual)
        )

    return blocos


def calcular_relevancia(pergunta, bloco):
    pergunta_normalizada = normalizar_texto(
        pergunta
    )

    bloco_normalizado = normalizar_texto(
        bloco
    )

    palavras_pergunta = set(
        pergunta_normalizada.split()
    )

    palavras_bloco = set(
        bloco_normalizado.split()
    )

    palavras_ignoradas = {
        "a",
        "o",
        "as",
        "os",
        "de",
        "da",
        "do",
        "das",
        "dos",
        "e",
        "em",
        "um",
        "uma",
        "para",
        "por",
        "que",
        "qual",
        "quais",
        "como",
        "vocês",
        "voces",
        "tem",
        "têm"
    }

    palavras_importantes = (
        palavras_pergunta
        - palavras_ignoradas
    )

    palavras_encontradas = (
        palavras_importantes
        & palavras_bloco
    )

    return len(palavras_encontradas)


def buscar_contexto(pergunta, limite=2):
    texto = carregar_base()

    if not texto:
        return []

    blocos = dividir_em_blocos(texto)

    resultados = []

    for bloco in blocos:
        relevancia = calcular_relevancia(
            pergunta,
            bloco
        )

        if relevancia > 0:
            resultados.append(
                (
                    relevancia,
                    bloco
                )
            )

    resultados.sort(
        key=lambda item: item[0],
        reverse=True
    )

    return [
        bloco
        for _, bloco in resultados[:limite]
    ]


if __name__ == "__main__":

    pergunta = input(
        "Pergunta: "
    ).strip()

    resultados = buscar_contexto(
        pergunta
    )

    if not resultados:
        print(
            "\nNenhuma informação relevante encontrada."
        )

    else:
        print(
            "\n=== CONTEXTO RECUPERADO ==="
        )

        for resultado in resultados:
            print()
            print(resultado)