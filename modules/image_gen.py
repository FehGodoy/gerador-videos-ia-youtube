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
"""
from __future__ import annotations

import logging
import time

import requests

logger = logging.getLogger(__name__)

FAL_MODEL_URL = "https://fal.run/fal-ai/flux/schnell"
_TIMEOUT_SECONDS = 60
_MAX_ATTEMPTS = 2
_RETRY_DELAY_SECONDS = 3


def generate_image(prompt: str, image_size: str = "landscape_16_9") -> tuple[bytes, str]:
    """Gera uma imagem via fal.ai (FLUX schnell) a partir de `prompt` e
    devolve `(bytes_da_imagem, extensao)`. `image_size` aceita os presets
    do fal.ai (landscape_16_9 combina com o formato 1920x1080 do projeto —
    o Remotion recorta com object-fit:cover, não precisa bater pixel a
    pixel).

    Levanta RuntimeError com mensagem clara (chave ausente, erro da API)
    em vez de deixar a exceção genérica do requests vazar — quem chama
    (webapp/server.py) repassa essa mensagem direto pro usuário.

    2 tentativas com pausa curta entre elas — mesmo princípio do reforço
    de retry já aplicado em modules/timeline.py::generate_slot_hints:
    falha transitória de rede/API não devia exigir clique manual de novo.
    """
    import os

    fal_key = os.environ.get("FAL_KEY")
    if not fal_key:
        raise RuntimeError(
            "FAL_KEY não definida no .env — configure sua chave do fal.ai "
            "(https://fal.ai/dashboard/keys) antes de gerar imagens por IA."
        )

    last_error: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        if attempt > 0:
            time.sleep(_RETRY_DELAY_SECONDS)
        try:
            resp = requests.post(
                FAL_MODEL_URL,
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
            data = resp.json()
            image = data["images"][0]
            image_url = image["url"]

            img_resp = requests.get(image_url, timeout=_TIMEOUT_SECONDS)
            img_resp.raise_for_status()
            ext = ".png" if "png" in image.get("content_type", "") else ".jpg"
            return img_resp.content, ext
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Geração de imagem via fal.ai falhou (tentativa %d/%d): %s",
                attempt + 1, _MAX_ATTEMPTS, exc, exc_info=True,
            )

    raise RuntimeError(f"Falha ao gerar imagem via fal.ai: {last_error}") from last_error
