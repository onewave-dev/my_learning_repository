import os
import logging
from fastapi import FastAPI, Request, HTTPException

app = FastAPI()

# Переменные окружения (добавим позже на Render и локально)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "jslkdji&8987812kjkj9989l_lki")  # временно

DEBUG = os.getenv("DEBUG", "false").lower() in {"1", "true", "yes", "on"}
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("app")

@app.get("/healthz")
async def healthz():
    log.debug("Health check requested")
    return {"status": "ok"}

# Заглушка вебхука (пока просто проверим секрет в URL)
@app.post("/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request):
    if secret != WEBHOOK_SECRET:
        log.warning("Webhook with wrong secret")
        raise HTTPException(status_code=403, detail="bad secret")
    # В следующем шаге добавим парсинг Update и передачу в PTB
    log.debug("Webhook stub OK")
    return {"ok": True}