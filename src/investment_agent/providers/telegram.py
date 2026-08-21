from __future__ import annotations

import httpx


class TelegramDeliveryProvider:
    name = "Telegram"

    def __init__(self, client: httpx.AsyncClient, bot_token: str, chat_id: str) -> None:
        self.client = client
        self.bot_token = bot_token
        self.chat_id = chat_id

    async def deliver(self, text: str) -> None:
        response = await self.client.post(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            json={
                "chat_id": self.chat_id,
                "text": text[:4096],
                "disable_web_page_preview": True,
            },
        )
        if response.is_error:
            # The request URL contains the bot token; never expose the HTTP exception URL.
            raise RuntimeError(f"Telegram HTTP yanıtı: {response.status_code}")
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError("Telegram API isteği başarısız")
