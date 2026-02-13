import asyncio
import os
import sys
import signal
import urllib.request
import urllib.parse
import json
from datetime import datetime
from meshtastic.tcp_interface import TCPInterface
from pubsub import pub
from telegram import Bot
from telegram.error import TimedOut, NetworkError
import logging
import time
import random
from logging.handlers import RotatingFileHandler

# ===== НАСТРОЙКИ =====
# Заполните своими данными!
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"  # Токен Telegram бота
CHAT_IDS = [123456789]  # ID чатов Telegram
MESH_HOST = "192.168.1.1"  # IP адрес Mesh-ноды
CHECK_INTERVAL = 0.5
MAX_MESH_BYTES = 200
MESH_SEND_DELAY = 5.0

# 🔑 API КЛЮЧИ - получите самостоятельно!
WEATHERAPI_KEY = "YOUR_WEATHERAPI_KEY_HERE"  # https://www.weatherapi.com
GITHUB_TOKEN = "YOUR_GITHUB_TOKEN_HERE"  # https://github.com/settings/tokens

# ===== ПРИНУДИТЕЛЬНЫЕ ИМЕНА НОД =====
# Формат: "!id_ноды": "Имя"
FORCE_NODE_NAMES = {}
# ===================================

# ===== НАСТРОЙКИ ЛОГОВ =====
LOG_MAX_SIZE = 2 * 1024 * 1024  # 2 МБ
LOG_BACKUP_COUNT = 2  # Хранить 2 старых лога
# ============================

# Настройка логирования с ротацией
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Формат логов
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

# Rotating File Handler - автоматическая ротация по размеру
if sys.platform == "win32":
    log_file = 'meshbot.log'
else:
    log_file = 'meshbot.log'  # Для Linux/Synology измените путь

file_handler = RotatingFileHandler(
    log_file,
    maxBytes=LOG_MAX_SIZE,
    backupCount=LOG_BACKUP_COUNT,
    encoding='utf-8'
)
file_handler.setFormatter(formatter)

# Вывод в консоль
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)

# Добавляем обработчики
logger.addHandler(file_handler)
logger.addHandler(console_handler)

bot = Bot(token=BOT_TOKEN)
iface = None
loop = None
running = True
last_mesh_send = 0

def signal_handler():
    global running
    logger.info("Shutdown signal received")
    running = False

def get_node_name(interface, node_id):
    """Получение имени узла с принудительным переопределением"""
    if node_id in FORCE_NODE_NAMES:
        return FORCE_NODE_NAMES[node_id]
    try:
        if node_id in interface.nodes:
            user = interface.nodes[node_id].get("user", {})
            long_name = user.get("longName", "").strip()
            if long_name:
                return long_name
            short_name = user.get("shortName", "").strip()
            if short_name:
                return short_name
        return node_id
    except:
        return node_id

def byte_truncate(text, max_bytes=MAX_MESH_BYTES):
    """Обрезает текст по байтам для Mesh"""
    if not text:
        return ""
    text = ' '.join(text.split())
    encoded = text.encode('utf-8')
    if len(encoded) <= max_bytes:
        return text
    truncated_bytes = encoded[:max_bytes-2]
    try:
        result = truncated_bytes.decode('utf-8')
    except UnicodeDecodeError:
        truncated_bytes = truncated_bytes[:-1]
        result = truncated_bytes.decode('utf-8', errors='ignore')
    last_space = result.rfind(' ')
    if last_space > len(result) * 0.5:
        result = result[:last_space]
    return result + ".."

async def send_to_mesh(text):
    """Отправка сообщения в Mesh с задержкой"""
    global last_mesh_send
    if not iface or not running:
        return False
    
    current_time = time.time()
    time_since_last = current_time - last_mesh_send
    if time_since_last < MESH_SEND_DELAY:
        await asyncio.sleep(MESH_SEND_DELAY - time_since_last)
    
    try:
        iface.sendText(byte_truncate(text))
        last_mesh_send = time.time()
        logger.info(f"📤 Mesh: {text[:30]}...")
        return True
    except Exception as e:
        logger.error(f"❌ Mesh error: {e}")
        return False

# ---------- АНЕКДОТЫ ----------
JOKES = [
    "Вовочка, почему ты опоздал в школу? - Учительница, я видел сон, что путешествую по Африке, а потом заснул и опоздал!",
    "— Доктор, я постоянно теряю память! — С какого времени? — С какого времени?",
    "Встречаются два хакера: — Ты слышал, Google купил Intel? — Да ладно! — Ага, теперь у них будет Googlе Inside.",
    "— Почему программисты любят зиму? — Потому что в холода кэш не сбрасывается.",
    "Штирлиц сидел в кресле и ел суп. Кресло было мягкое, а суп жидкий.",
    "— Алло, это служба поддержки? У меня компьютер не включается! — А вы вилку в розетку воткнули? — А её вынимать надо было?",
    "Колобок повесился. Следствие показало - у него была утечка памяти.",
    "— Дорогой, ты меня любишь? — Конечно! — А докажи! — А ты компилятор?",
    "Вовочка на уроке: — Марья Ивановна, а вы верите в любовь с первого взгляда? — Верю, Вовочка. Особенно когда вижу твой дневник!",
    "— Ты где так накололся? — В одноклассниках. — Там же дети! — А у меня дрель!"
]

def get_joke():
    """Случайный анекдот"""
    return f"😄 {random.choice(JOKES)}"

# ---------- ПОГОДА ----------
def get_weather(city):
    """Получение погоды через WeatherAPI.com"""
    try:
        if not WEATHERAPI_KEY or WEATHERAPI_KEY == "YOUR_WEATHERAPI_KEY_HERE":
            return "❌ Укажите WEATHERAPI_KEY в настройках"
        
        city_encoded = urllib.parse.quote(city)
        url = f"http://api.weatherapi.com/v1/current.json?key={WEATHERAPI_KEY}&q={city_encoded}&lang=ru"
        
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            
            if "error" not in data:
                location = data["location"]["name"]
                country = data["location"]["country"]
                temp = round(data["current"]["temp_c"])
                feels_like = round(data["current"]["feelslike_c"])
                condition = data["current"]["condition"]["text"]
                wind = round(data["current"]["wind_kph"] * 0.277778)
                humidity = data["current"]["humidity"]
                
                emoji = "☀️"
                if "дождь" in condition.lower():
                    emoji = "🌧"
                elif "снег" in condition.lower():
                    emoji = "❄️"
                elif "облач" in condition.lower() or "пасмур" in condition.lower():
                    emoji = "☁️"
                
                return (
                    f"{emoji} Погода в {location}, {country}:\n"
                    f"🌡 {temp}°C (ош.{feels_like}°C)\n"
                    f"☁️ {condition}\n"
                    f"💧 {humidity}% 💨 {wind}м/с"
                )
            else:
                return f"❌ Город '{city}' не найден"
    except Exception as e:
        logger.error(f"Weather error: {e}")
        return "❌ Не удалось получить погоду"

# ---------- КАЛЬКУЛЯТОР ----------
def calculate(expression):
    """Простой калькулятор"""
    try:
        expression = expression.strip().replace(',', '.')
        allowed_chars = "0123456789+-*/(). "
        for char in expression:
            if char not in allowed_chars:
                return "❌ Только цифры и + - * / ( )"
        result = eval(expression)
        if isinstance(result, float):
            result = round(result, 2)
        return f"🧮 {expression} = {result}"
    except ZeroDivisionError:
        return "❌ Деление на ноль"
    except Exception:
        return "❌ Ошибка в выражении"

# ---------- ПЕРЕВОДЧИК ----------
def translate_text(text):
    """Перевод через бесплатное API"""
    try:
        def is_russian(t):
            return any('а' <= c.lower() <= 'я' for c in t)
        
        text_encoded = urllib.parse.quote(text)
        
        if is_russian(text):
            url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=ru&tl=en&dt=t&q={text_encoded}"
        else:
            url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=ru&dt=t&q={text_encoded}"
        
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        
        with urllib.request.urlopen(req, timeout=10) as response:
            result = json.loads(response.read().decode('utf-8'))
            translated = result[0][0][0]
            
            if is_russian(text):
                return f"🇷🇺 → 🇬🇧: {translated}"
            else:
                return f"🇬🇧 → 🇷🇺: {translated}"
    except Exception as e:
        logger.error(f"Translate error: {e}")
        return "❌ Ошибка перевода"

# ---------- НЕЙРОСЕТЬ ----------
def ask_ai(prompt):
    """Запрос к GitHub Models"""
    try:
        if not GITHUB_TOKEN or GITHUB_TOKEN == "YOUR_GITHUB_TOKEN_HERE":
            return "❌ Укажите GITHUB_TOKEN в настройках"
        
        url = "https://models.inference.ai.azure.com/chat/completions"
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {GITHUB_TOKEN}"
        }
        
        data = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "Ты ассистент в Mesh сети. Отвечай максимум 1 предложение, 5-10 слов."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 30
        }
        
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode('utf-8'),
            headers=headers,
            method='POST'
        )
        
        with urllib.request.urlopen(req, timeout=10) as response:
            result = json.loads(response.read().decode('utf-8'))
            answer = result['choices'][0]['message']['content']
            answer = answer.replace('*', '').replace('#', '').replace('`', '')
            answer = answer.replace('\n', ' ').replace('  ', ' ')
            answer = answer.strip()
            return byte_truncate(answer)
    except Exception as e:
        logger.error(f"AI error: {e}")
        return "❌ Ошибка нейросети"

# ---------- Mesh → Telegram ----------
def on_mesh_receive(packet, interface):
    if "decoded" not in packet:
        return

    decoded = packet["decoded"]
    if decoded.get("portnum") != "TEXT_MESSAGE_APP":
        return

    text = decoded.get("text")
    if not text:
        return

    node_id = packet.get("fromId", "unknown")
    node_name = get_node_name(interface, node_id)

    # ===== ОБРАБОТКА КОМАНД =====
    
    if text.startswith("/test"):
        hop_count = 0
        rx_snr = packet.get("rxSnr", 0)
        rx_rssi = packet.get("rxRssi", 0)
        hop_limit = packet.get("hopLimit", 0)
        hop_start = packet.get("hopStart", 0)
        
        if hop_start > 0 and hop_limit > 0:
            hop_count = hop_start - hop_limit
        
        response = f"Тест {node_name}: прыжков {hop_count} SNR {rx_snr:.1f} RSSI {rx_rssi}"
        
        if loop and running:
            asyncio.run_coroutine_threadsafe(
                send_to_mesh(response),
                loop
            )
        return
    
    # ===== АНЕКДОТ =====
    elif text.startswith("/happy"):
        response = get_joke()
        if loop and running:
            asyncio.run_coroutine_threadsafe(
                send_to_mesh(response),
                loop
            )
        return
    
    # ===== ВРЕМЯ И ДАТА =====
    elif text.startswith("/time"):
        now = datetime.now()
        response = f"🕐 {now.strftime('%H:%M:%S')}\n📅 {now.strftime('%d.%m.%Y')}"
        if loop and running:
            asyncio.run_coroutine_threadsafe(
                send_to_mesh(response),
                loop
            )
        return
    
    # ===== КАЛЬКУЛЯТОР =====
    elif text.startswith("/calc"):
        expr = text[6:].strip()
        if not expr:
            response = "🧮 Пример: /calc 2+2*3"
        else:
            response = calculate(expr)
        if loop and running:
            asyncio.run_coroutine_threadsafe(
                send_to_mesh(response),
                loop
            )
        return
    
    # ===== ПЕРЕВОДЧИК =====
    elif text.startswith("/translate"):
        txt = text[11:].strip()
        if not txt:
            response = "🌍 Пример: /translate Hello world"
        else:
            response = translate_text(txt)
        if loop and running:
            asyncio.run_coroutine_threadsafe(
                send_to_mesh(response),
                loop
            )
        return
    
    # ===== ПОГОДА =====
    elif text.startswith("/weather"):
        if len(text) > 9:
            city = text[9:].strip()
        else:
            city = "Барнаул"
        
        logger.info(f"☀️ Погода от {node_name}: {city}")
        
        async def process_weather():
            await send_to_mesh(f"☀️ Ищу погоду в {city}...")
            weather = get_weather(city)
            await asyncio.sleep(MESH_SEND_DELAY)
            await send_to_mesh(weather)
        
        if loop and running:
            asyncio.run_coroutine_threadsafe(
                process_weather(),
                loop
            )
        return
    
    # ===== AI =====
    elif text.startswith("/ai"):
        if len(text) > 4:
            prompt = text[4:].strip()
        else:
            prompt = ""
        
        if not prompt:
            asyncio.run_coroutine_threadsafe(
                send_to_mesh("Напиши вопрос после /ai"),
                loop
            )
            return
        
        logger.info(f"🤖 AI от {node_name}: {prompt[:30]}...")
        
        async def process_ai():
            await send_to_mesh("Думаю...")
            answer = ask_ai(prompt)
            await asyncio.sleep(MESH_SEND_DELAY)
            await send_to_mesh(f"🤖 {answer}")
        
        if loop and running:
            asyncio.run_coroutine_threadsafe(
                process_ai(),
                loop
            )
        return
    
    # ===== HELP =====
    elif text.startswith("/help"):
        help_text = (
            "📋 Доступные команды:\n"
            "/test - тест связи\n"
            "/time - дата и время\n"
            "/happy - случайный анекдот\n"
            "/calc 2+2 - калькулятор\n"
            "/translate текст - перевод\n"
            "/weather город - погода\n"
            "/ai вопрос - нейросеть\n"
            "/help - помощь"
        )
        if loop and running:
            asyncio.run_coroutine_threadsafe(
                send_to_mesh(help_text),
                loop
            )
        return

    # ===== ОБЫЧНЫЕ СООБЩЕНИЯ =====
    msg = f"📡 <b>{node_name}</b>: {text}"
    logger.info(f"Mesh → TG: {node_name}: {text[:30]}...")
    
    if loop is not None and running:
        asyncio.run_coroutine_threadsafe(
            send_telegram_message(msg),
            loop
        )

async def send_telegram_message(msg):
    for chat_id in CHAT_IDS:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=msg,
                parse_mode="HTML",
                read_timeout=10,
                write_timeout=10,
                connect_timeout=10
            )
            logger.info(f"✅ TG: {chat_id}")
        except Exception as e:
            logger.error(f"❌ TG error {chat_id}: {e}")
        await asyncio.sleep(0.1)

# ---------- Telegram → Mesh ----------
async def telegram_loop():
    global loop
    loop = asyncio.get_running_loop()
    
    last_update = 0
    error_count = 0
    logger.info(f"👂 Слушаю чаты: {CHAT_IDS}")
    
    while running:
        try:
            updates = await bot.get_updates(
                offset=last_update, 
                timeout=30
            )
            
            for u in updates:
                last_update = u.update_id + 1
                if not u.message or not u.message.text:
                    continue
                
                if u.message.chat_id not in CHAT_IDS:
                    continue

                text = u.message.text
                logger.info(f"📨 TG -> Mesh: {text[:30]}...")

                # Отправляем ТОЛЬКО когда есть сообщение из Telegram!
                if iface and running:
                    await send_to_mesh(text)
            
            error_count = 0
            
        except asyncio.CancelledError:
            break
        except Exception as e:
            error_count += 1
            if error_count > 10:
                logger.error(f"⚠️ TG error: {e}")
                await asyncio.sleep(30)
            else:
                logger.error(f"⚠️ TG error: {e}")
        
        await asyncio.sleep(CHECK_INTERVAL)
    
    logger.info("Telegram loop stopped")

# ---------- MAIN ----------
async def main():
    global iface, loop, running
    
    if sys.platform != "win32":
        signal.signal(signal.SIGINT, lambda s, f: signal_handler())
        signal.signal(signal.SIGTERM, lambda s, f: signal_handler())
    
    logger.info("🔌 Подключение к Meshtastic...")
    
    if WEATHERAPI_KEY and WEATHERAPI_KEY != "YOUR_WEATHERAPI_KEY_HERE":
        logger.info("☀️ WeatherAPI готов")
    if GITHUB_TOKEN and GITHUB_TOKEN != "YOUR_GITHUB_TOKEN_HERE":
        logger.info("🤖 GitHub AI готов")
    if FORCE_NODE_NAMES:
        logger.info(f"📝 Принудительные имена: {FORCE_NODE_NAMES}")
    logger.info(f"📋 Логи: 2 МБ, {LOG_BACKUP_COUNT} бэкапа")
    
    for attempt in range(5):
        try:
            iface = TCPInterface(hostname=MESH_HOST)
            logger.info("✅ Mesh connected")
            break
        except Exception as e:
            logger.error(f"❌ Попытка {attempt + 1}: {e}")
            if attempt < 4:
                await asyncio.sleep(5)
            else:
                logger.critical("💀 Не удалось подключиться")
                return

    pub.subscribe(on_mesh_receive, "meshtastic.receive")
    
    logger.info("🚀 Бот запущен")
    logger.info("📋 Команды: /test, /time, /happy, /calc, /translate, /weather, /ai, /help")
    logger.info(f"⏱ Задержка: {MESH_SEND_DELAY} сек")
    
    try:
        await telegram_loop()
    finally:
        if iface:
            iface.close()
            logger.info("🔌 Mesh disconnected")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Бот остановлен")
    except Exception as e:
        logger.critical(f"💥 Ошибка: {e}")
        sys.exit(1)
