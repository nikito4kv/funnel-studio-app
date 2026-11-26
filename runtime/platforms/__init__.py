"""Платформы движка помимо Telegram.

TG-логика исторически живёт в sender.py / gateway.py / runner.py; MAX вынесен
сюда целиком, а в общий код воткнуты только точки вызова:

  max_api.py     клиент MAX Bot API (aiohttp, без прокси – MAX российский)
  max_html.py    HTML Telegram → HTML MAX
  max_sender.py  отправка шага в MAX (текст, картинка/альбом, видео, кнопки)
  max_steps.py   что шаг значит в MAX: контент (content.max), телефон, канал
  max_hub.py     MAX-боты, в т.ч. общий на несколько запусков: long-poll,
                 роутинг апдейта в запуск, передача в gateway
"""
