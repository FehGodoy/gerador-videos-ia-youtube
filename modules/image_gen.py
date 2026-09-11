"""
Geração de imagem por IA a partir do image_prompt de um trecho do editor de
timeline manual (modules/timeline.py) — substitui o fluxo de copiar o
prompt, colar num gerador externo (ChatGPT web etc.) e baixar o arquivo pra
uma pasta observada (webapp/folder_sync.py). Chamando a API diretamente,
cada imagem já nasce ligada ao trecho exato que a gerou — nenhum risco do
desalinhamento por ordem de chegada que motivou boa parte do trabalho em
folder_sync.py (aviso por tempo anômalo, ferramenta de realinhamento).

Provedor: fal.ai, modelo FLUX.1 [schnell] — testado ao vivo com prompts
reais do usuário (pessoas, marca/modelo de carro específico) antes de
integrar: resultado bom o bastante pra esse caso de uso, bem mais barato
que gerar via API do GPT Image (~$0,003/megapixel). Constante no código
(não config.yaml) porque é uma integração de fornecedor único, mesmo
padrão de outras integrações do projeto (ex.: Cartesia em narration.py).

Exceção: quando o prompt pede texto legível NA CENA (infográfico, placa,
capa, documento — ver REGRA CRÍTICA DE IDIOMA em modules/timeline.py), o
FLUX schnell renderiza mal (letras "tremidas"/incompletas — limitação
conhecida de modelos rápidos/destilados, que "adivinham" a forma da letra
em vez de desenhar com precisão). Nesse caso, usa Ideogram v3 (fal.ai)
em vez do FLUX — especialista em texto legível (~90% de precisão),
~10x mais caro (Turbo, $0,03/imagem vs ~$0,003 do schnell), mas usado só
nas poucas imagens de cada vídeo que realmente pedem texto, então o
impacto no custo total é pequeno. Detecção: o próprio prompt de dica
(_HINTS_PROMPT_TEMPLATE) já instrui a IA a escrever o texto exato ENTRE
ASPAS dentro do image_prompt quando a cena pede texto — reaproveita esse
sinal existente em vez de pedir um campo novo (ver _prompt_wants_text_in_image).
"""
from __future__ import annotations

import logging
import re
import time

import requests

logger = logging.getLogger(__name__)

FAL_FLUX_SCHNELL_URL = "https://fal.run/fal-ai/flux/schnell"
FAL_IDEOGRAM_URL = "https://fal.run/fal-ai/ideogram/v3"
_TIMEOUT_SECONDS = 60
_MAX_ATTEMPTS = 2
_RETRY_DELAY_SECONDS = 3

# Sinal de que o image_prompt pede texto legível na cena: a REGRA CRÍTICA DE
# IDIOMA (modules/timeline.py::_HINTS_PROMPT_TEMPLATE) instrui a IA a
# escrever a palavra exata entre aspas quando a cena tem texto — ver exemplo
# "CERTO" no prompt. Aspas simples ou duplas, 2+ caracteres dentro.
_TEXT_IN_IMAGE_RE = re.compile(r"['\"][^'\"]{2,}['\"]")


def _prompt_wants_text_in_image(prompt: str) -> bool:
    return bool(_TEXT_IN_IMAGE_RE.search(prompt))


def _get_fal_key() -> str:
    import os

    fal_key = os.environ.get("FAL_KEY")
    if not fal_key:
        raise RuntimeError(
            "FAL_KEY não definida no .env — configure sua chave do fal.ai "
            "(https://fal.ai/dashboard/keys) antes de gerar imagens por IA."
        )
    return fal_key


def _download(image: dict) -> tuple[bytes, str]:
    img_resp = requests.get(image["url"], timeout=_TIMEOUT_SECONDS)
    img_resp.raise_for_status()
    ext = ".png" if "png" in image.get("content_type", "") else ".jpg"
    return img_resp.content, ext


def generate_image(
    prompt: str, image_size: str = "landscape_16_9", allow_ideogram: bool = True
) -> tuple[bytes, str, str]:
    """Gera uma imagem via fal.ai a partir de `prompt` e devolve
    `(bytes_da_imagem, extensao, modelo_usado)` — `modelo_usado` é
    "ideogram" ou "flux_schnell", pra quem chama persistir e contar (ver
    modules/timeline.py::count_text_image_generations, usado pra aplicar o
    teto `image_gen.max_text_images_per_draft` do config.yaml).

    `allow_ideogram=False` força FLUX schnell mesmo quando o prompt pede
    texto na cena — usado quando o rascunho já bateu o teto de imagens
    caras: a imagem sai com texto pior (mesma limitação de sempre do
    schnell), mas o pipeline nunca quebra nem para de gerar imagem por
    causa de orçamento.

    `image_size` aceita os presets do FLUX (landscape_16_9 combina com o
    formato 1920x1080 do projeto — o Remotion recorta com
    object-fit:cover, não precisa bater pixel a pixel); o Ideogram usa o
    MESMO enum de preset (confirmado testando ao vivo contra a API real).

    Levanta RuntimeError com mensagem clara (chave ausente, erro da API)
    em vez de deixar a exceção genérica do requests vazar — quem chama
    (webapp/server.py) repassa essa mensagem direto pro usuário.

    2 tentativas com pausa curta entre elas — mesmo princípio do reforço
    de retry já aplicado em modules/timeline.py::generate_slot_hints:
    falha transitória de rede/API não devia exigir clique manual de novo.
    """
    fal_key = _get_fal_key()
    use_ideogram = allow_ideogram and _prompt_wants_text_in_image(prompt)

    last_error: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        if attempt > 0:
            time.sleep(_RETRY_DELAY_SECONDS)
        try:
            if use_ideogram:
                resp = requests.post(
                    FAL_IDEOGRAM_URL,
                    headers={"Authorization": f"Key {fal_key}", "Content-Type": "application/json"},
                    json={
                        "prompt": prompt,
                        # mesmo enum de preset do FLUX (confirmado testando ao
                        # vivo contra a API real: um 422 revelou que a doc
                        # pesquisada sugerindo "16:9" estava desatualizada —
                        # o schema real aceita 'landscape_16_9' etc., igual FLUX).
                        "image_size": image_size,
                        "rendering_speed": "TURBO",
                        "num_images": 1,
                    },
                    timeout=_TIMEOUT_SECONDS,
                )
            else:
                resp = requests.post(
                    FAL_FLUX_SCHNELL_URL,
                    headers={"Authorization": f"Key {fal_key}", "Content-Type": "application/json"},
                    json={
                        "prompt": prompt,
                        "image_size": image_size,
                        "num_inference_steps": 4,
                        "num_images": 1,
                        "enable_safety_checker": True,
                    },
                    timeout=_TIMEOUT_SECONDS,
                )
            resp.raise_for_status()
            img_bytes, ext = _download(resp.json()["images"][0])
            return img_bytes, ext, ("ideogram" if use_ideogram else "flux_schnell")
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Geração de imagem via fal.ai (%s) falhou (tentativa %d/%d): %s",
                "Ideogram v3" if use_ideogram else "FLUX schnell",
                attempt + 1, _MAX_ATTEMPTS, exc, exc_info=True,
            )

    raise RuntimeError(f"Falha ao gerar imagem via fal.ai: {last_error}") from last_error
