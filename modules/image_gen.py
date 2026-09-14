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

Exceção 1: quando o prompt pede texto legível NA CENA (infográfico, placa,
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

Exceção 2: quando o trecho tem pessoa em destaque (`has_person`, ver
modules/timeline.py), o schnell erra mais em rosto/mão/anatomia — usa
FLUX.1 [dev] em vez do schnell nesses casos (~$0,025/imagem, ~8x mais
caro, mesma família/estilo do FLUX então não muda a "cara" do vídeo,
só a coerência). Diferente do texto (detectável no PRÓPRIO prompt), esse
sinal vem de fora (`prefer_dev`, decidido por quem chama a partir do
`has_person` do trecho e do teto `image_gen.max_dev_images_per_draft` do
config.yaml) — pedido explícito do usuário pra não gastar rápido demais
os créditos do fal.ai testando o modelo mais caro (ver
modules/timeline.py::count_dev_image_generations).

Camada de custo pro schnell: antes de gastar crédito do fal.ai, tenta
gerar via Cloudflare Workers AI (mesmo modelo FLUX.1 [schnell] em cima
de uma conta com 10.000 Neurons grátis por dia, ~173 imagens grátis/dia
por conta) -- pedido do usuário pra não gastar fal.ai à toa quando a
cota diária da Cloudflare ainda cobre. Cadeia: conta Cloudflare 1 →
conta Cloudflare 2 (se configurada) → fal.ai schnell, cada uma só
tentada se a anterior falhar por QUALQUER motivo (cota do dia esgotada,
conta não configurada, erro de rede) — nunca trava o pipeline esperando
uma camada específica funcionar. Só dev e Ideogram continuam sempre no
fal.ai (Cloudflare não tem um equivalente confirmado funcionando pra
esses dois nessa conta). `width`/`height` NÃO são aceitos pela Cloudflare
nessa conta (testado ao vivo, erro 400 quando enviados) -- a imagem sai
sempre quadrada, sem problema real porque o Remotion já recorta com
object-fit:cover de qualquer jeito.
"""
from __future__ import annotations

import base64
import logging
import os
import re
import time

import requests

logger = logging.getLogger(__name__)

FAL_FLUX_SCHNELL_URL = "https://fal.run/fal-ai/flux/schnell"
FAL_FLUX_DEV_URL = "https://fal.run/fal-ai/flux/dev"
FAL_IDEOGRAM_URL = "https://fal.run/fal-ai/ideogram/v3"
_TIMEOUT_SECONDS = 60
_MAX_ATTEMPTS = 2
_RETRY_DELAY_SECONDS = 3

_CLOUDFLARE_MODEL = "@cf/black-forest-labs/flux-1-schnell"


def _get_cloudflare_accounts() -> list[tuple[str, str]]:
    """Lê até 2 contas Cloudflare do .env (CLOUDFLARE_ACCOUNT_ID[_2] +
    CLOUDFLARE_API_TOKEN[_2]) -- cada uma tem cota diária própria de
    Neurons grátis, por isso 2 contas configuradas dobram o volume grátis
    disponível antes de cair pro fal.ai. Conta sem as DUAS variáveis
    definidas é ignorada silenciosamente (não é erro -- é opcional)."""
    accounts = []
    for suffix in ("", "_2"):
        account_id = os.environ.get(f"CLOUDFLARE_ACCOUNT_ID{suffix}")
        token = os.environ.get(f"CLOUDFLARE_API_TOKEN{suffix}")
        if account_id and token:
            accounts.append((account_id, token))
    return accounts


def _generate_via_cloudflare(prompt: str, account_id: str, token: str) -> bytes:
    """Gera via Cloudflare Workers AI (mesmo FLUX.1 [schnell]). Só aceita
    `prompt`, `seed` e `steps` nessa conta -- `width`/`height` derrubam a
    chamada com 400 (testado ao vivo, contradiz a documentação pesquisada;
    provavelmente uma diferença de schema entre planos/versões da API).
    Devolve os bytes já decodificados de base64 (resposta vem como JPEG
    embutido em JSON, formato diferente do fal.ai que devolve uma URL)."""
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{_CLOUDFLARE_MODEL}"
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}"},
        json={"prompt": prompt, "steps": 4},
        timeout=_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success", True) or "result" not in data or "image" not in data.get("result", {}):
        raise RuntimeError(f"Resposta inesperada da Cloudflare: {data}")
    return base64.b64decode(data["result"]["image"])

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
    prompt: str,
    image_size: str = "landscape_16_9",
    allow_ideogram: bool = True,
    prefer_dev: bool = False,
) -> tuple[bytes, str, str]:
    """Gera uma imagem via fal.ai a partir de `prompt` e devolve
    `(bytes_da_imagem, extensao, modelo_usado)` — `modelo_usado` é
    "ideogram", "flux_dev" ou "flux_schnell", pra quem chama persistir e
    contar (ver modules/timeline.py::count_text_image_generations e
    count_dev_image_generations, usados pra aplicar os tetos
    `image_gen.max_text_images_per_draft`/`max_dev_images_per_draft` do
    config.yaml).

    `allow_ideogram=False` força FLUX schnell/dev mesmo quando o prompt pede
    texto na cena — usado quando o rascunho já bateu o teto de imagens
    caras de texto: a imagem sai com texto pior (mesma limitação de sempre
    do schnell/dev), mas o pipeline nunca quebra nem para de gerar imagem
    por causa de orçamento.

    `prefer_dev=True` pede FLUX.1 [dev] em vez do schnell (texto ainda tem
    prioridade — Ideogram vence se `allow_ideogram` também mandar). Quem
    chama decide isso a partir do `has_person` do trecho E do teto já
    verificado (`count_dev_image_generations(slug) < max_dev_images_per_draft`)
    — esta função não sabe nada de slug/rascunho, só executa a preferência
    que já chegou pronta.

    `image_size` aceita os presets do FLUX (landscape_16_9 combina com o
    formato 1920x1080 do projeto — o Remotion recorta com
    object-fit:cover, não precisa bater pixel a pixel); Ideogram e FLUX dev
    usam o MESMO enum de preset do schnell (confirmado testando ao vivo
    contra a API real).

    Levanta RuntimeError com mensagem clara (chave ausente, erro da API)
    em vez de deixar a exceção genérica do requests vazar — quem chama
    (webapp/server.py) repassa essa mensagem direto pro usuário.

    2 tentativas com pausa curta entre elas — mesmo princípio do reforço
    de retry já aplicado em modules/timeline.py::generate_slot_hints:
    falha transitória de rede/API não devia exigir clique manual de novo.
    """
    fal_key = _get_fal_key()
    use_ideogram = allow_ideogram and _prompt_wants_text_in_image(prompt)
    use_dev = not use_ideogram and prefer_dev
    if use_ideogram:
        model_name, url = "ideogram", FAL_IDEOGRAM_URL
    elif use_dev:
        model_name, url = "flux_dev", FAL_FLUX_DEV_URL
    else:
        model_name, url = "flux_schnell", FAL_FLUX_SCHNELL_URL

    # Só o schnell tem camada Cloudflare antes do fal.ai (ver docstring do
    # módulo) -- dev e Ideogram vão direto pro fal.ai, sem tentativa
    # prévia. Cada conta configurada é tentada uma vez; QUALQUER falha
    # (cota diária esgotada, rede, conta não configurada) passa pra
    # próxima sem travar o pipeline. model_name distingue a origem
    # ("flux_schnell_cloudflare" vs "flux_schnell") só pra auditoria de
    # custo -- não entra em nenhum teto/contagem existente.
    if not use_ideogram and not use_dev:
        for account_id, token in _get_cloudflare_accounts():
            try:
                img_bytes = _generate_via_cloudflare(prompt, account_id, token)
                return img_bytes, ".jpg", "flux_schnell_cloudflare"
            except Exception as exc:
                logger.warning(
                    "Geração de imagem via Cloudflare Workers AI falhou (conta %s...): %s",
                    account_id[:8], exc, exc_info=True,
                )

    last_error: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        if attempt > 0:
            time.sleep(_RETRY_DELAY_SECONDS)
        try:
            if use_ideogram:
                resp = requests.post(
                    url,
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
            elif use_dev:
                resp = requests.post(
                    url,
                    headers={"Authorization": f"Key {fal_key}", "Content-Type": "application/json"},
                    json={
                        "prompt": prompt,
                        "image_size": image_size,
                        "num_images": 1,
                        "enable_safety_checker": True,
                        # sem num_inference_steps -- deixa o padrão do [dev]
                        # (mais alto que os 4 passos do schnell), é isso que
                        # compra a qualidade extra.
                    },
                    timeout=_TIMEOUT_SECONDS,
                )
            else:
                resp = requests.post(
                    url,
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
            return img_bytes, ext, model_name
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Geração de imagem via fal.ai (%s) falhou (tentativa %d/%d): %s",
                model_name, attempt + 1, _MAX_ATTEMPTS, exc, exc_info=True,
            )

    raise RuntimeError(f"Falha ao gerar imagem via fal.ai: {last_error}") from last_error
