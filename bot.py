import ccxt
import numpy as np
import pandas as pd
import asyncio
import logging
import re
import os
from telegram import Bot
from telegram.ext import Application, CommandHandler, ContextTypes
from datetime import datetime, timedelta

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Импорты для технических индикаторов
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands

# Настройки из переменных окружения
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
if not TELEGRAM_TOKEN:
    TELEGRAM_TOKEN = "8225140718:AAFcS16jDoGghYGj0_ued-zz1CURAyOcCCo"  # Для локального тестирования

EXCHANGE = ccxt.binance()
TIMEFRAME = '1h'

def escape_markdown(text):
    """Экранирует специальные символы для Markdown"""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(r'([%s])' % re.escape(escape_chars), r'\\\1', text)

def get_signals(hours_back=4):
    """Возвращает сигналы RSI + BB за прошедшие hours_back часов."""
    signals = []
    signal_cache = set()
    
    try:
        logger.info(f"Загружаю рынки... Анализ за последние {hours_back} часа(ов)")
        markets = EXCHANGE.load_markets()
        symbols = [s for s in markets if s.endswith('/USDT') and '/UP/' not in s and '/DOWN/' not in s]
        logger.info(f"Найдено {len(symbols)} USDT пар")
        
        # Создаем список пар с объемами для сортировки
        symbols_with_volume = []
        for symbol in symbols:
            try:
                ticker = EXCHANGE.fetch_ticker(symbol)
                quote_volume = ticker.get('quoteVolume')
                if quote_volume is not None:
                    symbols_with_volume.append((symbol, quote_volume))
            except Exception as e:
                continue
        
        # Сортируем по объему (по убыванию) и берем первые 50
        symbols_with_volume.sort(key=lambda x: x[1], reverse=True)
        top_50_symbols = [symbol for symbol, volume in symbols_with_volume[:50]]
        
        logger.info(f"Анализирую топ 50 пар по объему за последние {hours_back} часа(ов)")
        
        # Анализируем топ 50 пар
        for i, symbol in enumerate(top_50_symbols):
            try:
                if i % 10 == 0:
                    logger.info(f"Анализ {i+1}/{len(top_50_symbols)}: {symbol}")
                    
                candles_needed = max(hours_back + 50, 150)
                candles = EXCHANGE.fetch_ohlcv(symbol, TIMEFRAME, limit=candles_needed)
                
                if len(candles) < max(hours_back + 20, 50):
                    continue
                
                timestamps = [c[0] for c in candles]
                opens = [c[1] for c in candles]
                highs = [c[2] for c in candles]
                lows = [c[3] for c in candles]
                closes = [c[4] for c in candles]
                
                close_series = pd.Series(closes)
                
                rsi_indicator = RSIIndicator(close=close_series, window=14)
                rsi_series = rsi_indicator.rsi()
                
                bb = BollingerBands(close=close_series, window=20, window_dev=2)
                upper_series = bb.bollinger_hband()
                lower_series = bb.bollinger_lband()
                
                if len(rsi_series) < 10 or len(upper_series) < 10 or len(lower_series) < 10:
                    continue
                
                current_time = timestamps[-1]
                start_analysis_time = current_time - (hours_back * 60 * 60 * 1000)
                
                start_index = max(30, len(candles) - hours_back - 20)
                end_index = len(candles) - 1
                
                for idx in range(start_index, end_index + 1):
                    if timestamps[idx] < start_analysis_time:
                        continue
                    
                    current_open = opens[idx]
                    current_high = highs[idx]
                    current_low = lows[idx]
                    current_close = closes[idx]
                    current_timestamp = timestamps[idx]
                    
                    if (idx >= len(rsi_series) or idx >= len(upper_series) or idx >= len(lower_series) or
                        pd.isna(rsi_series.iloc[idx]) or pd.isna(upper_series.iloc[idx]) or pd.isna(lower_series.iloc[idx])):
                        continue
                    
                    current_rsi = float(rsi_series.iloc[idx])
                    current_upper = float(upper_series.iloc[idx])
                    current_lower = float(lower_series.iloc[idx])
                    
                    is_oversold = (current_rsi < 30) and (current_low <= current_lower) and (current_close > current_lower)
                    is_overbought = (current_rsi > 70) and (current_high >= current_upper) and (current_close < current_upper)
                    
                    if is_oversold or is_overbought:
                        signal_key = f"{symbol}_{current_timestamp}_{ 'oversold' if is_oversold else 'overbought' }"
                        if signal_key in signal_cache:
                            continue
                        signal_cache.add(signal_key)
                        
                        signal_type = "📉 Перепроданность (касание тенью)" if is_oversold else "📈 Перекупленность (касание тенью)"
                        signal_time = datetime.fromtimestamp(current_timestamp/1000).strftime('%m-%d %H:%M')
                        
                        escaped_symbol = escape_markdown(symbol)
                        
                        signals.append(
                            f"{signal_type}: *{escaped_symbol}*\n"
                            f"- RSI: {current_rsi:.2f}\n"
                            f"- Цена: {current_close:.4f}\n"
                            f"- Время: {signal_time}\n"
                        )
                        
                        logger.info(f"Найден сигнал: {symbol} - {signal_type} - RSI: {current_rsi:.2f}")

            except Exception as e:
                logger.warning(f"Ошибка анализа {symbol}: {e}")
                continue

    except Exception as e:
        logger.error(f"Критическая ошибка в get_signals: {e}")

    logger.info(f"Анализ за {hours_back} часов завершен. Найдено уникальных сигналов: {len(signals)}")
    return signals

async def start_command(update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start."""
    user_name = update.effective_user.username or update.effective_user.first_name
    logger.info(f"Получена команда /start от {user_name}")
    await update.message.reply_text(
        "Привет! Я торговый бот с различными периодами анализа:\n"
        "/scan4 - сигналы за последние 4 часа\n"
        "/scan10 - сигналы за последние 10 часов\n"
        "/scan24 - сигналы за последние 24 часа"
    )

async def scan_specific_command(update, context: ContextTypes.DEFAULT_TYPE, hours=4):
    """Общий обработчик для команд сканирования."""
    user_name = update.effective_user.username or update.effective_user.first_name
    command = update.message.text.lower()
    
    logger.info(f"Получена команда {command} от {user_name}")
    
    try:
        await update.message.reply_text(f"🔍 Ищу торговые сигналы с касанием тенью в топ 50 пар по объему за последние {hours} часа(ов), подождите...")
        
        signals = get_signals(hours)
        
        if signals:
            display_signals = signals[:20]
            message = (
                f"🔔 *Найдено {len(signals)} сигналов за последние {hours} часа(ов)* (показаны первые {len(display_signals)}):\n\n" +
                "\n".join(display_signals)
            )
        else:
            message = f"❎ Сигналов с касанием тенью не найдено за последние {hours} часа(ов). Попробуйте позже."
        
        await update.message.reply_text(message, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Ошибка в scan_command: {e}")
        await update.message.reply_text("❌ Произошла ошибка при сканировании. Попробуйте позже.")

async def scan4_command(update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /scan4."""
    await scan_specific_command(update, context, 4)

async def scan10_command(update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /scan10."""
    await scan_specific_command(update, context, 10)

async def scan24_command(update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /scan24."""
    await scan_specific_command(update, context, 24)

def main():
    try:
        application = Application.builder().token(TELEGRAM_TOKEN).build()
        
        # Добавляем обработчики для разных команд
        application.add_handler(CommandHandler('start', start_command))
        application.add_handler(CommandHandler('scan4', scan4_command))
        application.add_handler(CommandHandler('scan10', scan10_command))
        application.add_handler(CommandHandler('scan24', scan24_command))
        
        # Также добавляем обработчик для команды /scan (по умолчанию 4 часа)
        application.add_handler(CommandHandler('scan', scan4_command))
        
        logger.info("🚀 Бот успешно запущен! Доступные команды: /scan4, /scan10, /scan24")
        
        # Используем правильные параметры для run_polling
        application.run_polling(allowed_updates=None)
        
    except Exception as e:
        logger.error(f"Критическая ошибка при запуске бота: {e}")

if __name__ == "__main__":
    main()import ccxt
import numpy as np
import pandas as pd
import asyncio
import logging
import re
import os
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Bot
from telegram.ext import Application, CommandHandler, ContextTypes
from datetime import datetime, timedelta

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Импорты для технических индикаторов
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands

# Настройки из переменных окружения
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
if not TELEGRAM_TOKEN:
    TELEGRAM_TOKEN = "8225140718:AAFcS16jDoGghYGj0_ued-zz1CURAyOcCCo"  # Для локального тестирования

EXCHANGE = ccxt.binance()
TIMEFRAME = '1h'

# Фиктивный веб-сервер для Render
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()
        self.wfile.write(b'Bot is running! Trading signals bot for Telegram.')

def start_health_server():
    """Запуск фиктивного веб-сервера для Render"""
    try:
        port = int(os.environ.get('PORT', 10000))
        server = HTTPServer(('0.0.0.0', port), HealthHandler)
        server_thread = threading.Thread(target=server.serve_forever)
        server_thread.daemon = True
        server_thread.start()
        logger.info(f"Health server started on port {port}")
        return server
    except Exception as e:
        logger.error(f"Failed to start health server: {e}")
        return None

def escape_markdown(text):
    """Экранирует специальные символы для Markdown"""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(r'([%s])' % re.escape(escape_chars), r'\\\1', text)

def get_signals(hours_back=4):
    """Возвращает сигналы RSI + BB за прошедшие hours_back часов."""
    signals = []
    signal_cache = set()
    
    try:
        logger.info(f"Загружаю рынки... Анализ за последние {hours_back} часа(ов)")
        markets = EXCHANGE.load_markets()
        symbols = [s for s in markets if s.endswith('/USDT') and '/UP/' not in s and '/DOWN/' not in s]
        logger.info(f"Найдено {len(symbols)} USDT пар")
        
        # Создаем список пар с объемами для сортировки
        symbols_with_volume = []
        for symbol in symbols:
            try:
                ticker = EXCHANGE.fetch_ticker(symbol)
                quote_volume = ticker.get('quoteVolume')
                if quote_volume is not None:
                    symbols_with_volume.append((symbol, quote_volume))
            except Exception as e:
                continue
        
        # Сортируем по объему (по убыванию) и берем первые 50
        symbols_with_volume.sort(key=lambda x: x[1], reverse=True)
        top_50_symbols = [symbol for symbol, volume in symbols_with_volume[:50]]
        
        logger.info(f"Анализирую топ 50 пар по объему за последние {hours_back} часа(ов)")
        
        # Анализируем топ 50 пар
        for i, symbol in enumerate(top_50_symbols):
            try:
                if i % 10 == 0:
                    logger.info(f"Анализ {i+1}/{len(top_50_symbols)}: {symbol}")
                    
                candles_needed = max(hours_back + 50, 150)
                candles = EXCHANGE.fetch_ohlcv(symbol, TIMEFRAME, limit=candles_needed)
                
                if len(candles) < max(hours_back + 20, 50):
                    continue
                
                timestamps = [c[0] for c in candles]
                opens = [c[1] for c in candles]
                highs = [c[2] for c in candles]
                lows = [c[3] for c in candles]
                closes = [c[4] for c in candles]
                
                close_series = pd.Series(closes)
                
                rsi_indicator = RSIIndicator(close=close_series, window=14)
                rsi_series = rsi_indicator.rsi()
                
                bb = BollingerBands(close=close_series, window=20, window_dev=2)
                upper_series = bb.bollinger_hband()
                lower_series = bb.bollinger_lband()
                
                if len(rsi_series) < 10 or len(upper_series) < 10 or len(lower_series) < 10:
                    continue
                
                current_time = timestamps[-1]
                start_analysis_time = current_time - (hours_back * 60 * 60 * 1000)
                
                start_index = max(30, len(candles) - hours_back - 20)
                end_index = len(candles) - 1
                
                for idx in range(start_index, end_index + 1):
                    if timestamps[idx] < start_analysis_time:
                        continue
                    
                    current_open = opens[idx]
                    current_high = highs[idx]
                    current_low = lows[idx]
                    current_close = closes[idx]
                    current_timestamp = timestamps[idx]
                    
                    if (idx >= len(rsi_series) or idx >= len(upper_series) or idx >= len(lower_series) or
                        pd.isna(rsi_series.iloc[idx]) or pd.isna(upper_series.iloc[idx]) or pd.isna(lower_series.iloc[idx])):
                        continue
                    
                    current_rsi = float(rsi_series.iloc[idx])
                    current_upper = float(upper_series.iloc[idx])
                    current_lower = float(lower_series.iloc[idx])
                    
                    is_oversold = (current_rsi < 30) and (current_low <= current_lower) and (current_close > current_lower)
                    is_overbought = (current_rsi > 70) and (current_high >= current_upper) and (current_close < current_upper)
                    
                    if is_oversold or is_overbought:
                        signal_key = f"{symbol}_{current_timestamp}_{ 'oversold' if is_oversold else 'overbought' }"
                        if signal_key in signal_cache:
                            continue
                        signal_cache.add(signal_key)
                        
                        signal_type = "📉 Перепроданность (касание тенью)" if is_oversold else "📈 Перекупленность (касание тенью)"
                        signal_time = datetime.fromtimestamp(current_timestamp/1000).strftime('%m-%d %H:%M')
                        
                        escaped_symbol = escape_markdown(symbol)
                        
                        signals.append(
                            f"{signal_type}: *{escaped_symbol}*\n"
                            f"- RSI: {current_rsi:.2f}\n"
                            f"- Цена: {current_close:.4f}\n"
                            f"- Время: {signal_time}\n"
                        )
                        
                        logger.info(f"Найден сигнал: {symbol} - {signal_type} - RSI: {current_rsi:.2f}")

            except Exception as e:
                logger.warning(f"Ошибка анализа {symbol}: {e}")
                continue

    except Exception as e:
        logger.error(f"Критическая ошибка в get_signals: {e}")

    logger.info(f"Анализ за {hours_back} часов завершен. Найдено уникальных сигналов: {len(signals)}")
    return signals

async def start_command(update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start."""
    user_name = update.effective_user.username or update.effective_user.first_name
    logger.info(f"Получена команда /start от {user_name}")
    await update.message.reply_text(
        "Привет! Я торговый бот с различными периодами анализа:\n"
        "/scan4 - сигналы за последние 4 часа\n"
        "/scan10 - сигналы за последние 10 часов\n"
        "/scan24 - сигналы за последние 24 часа"
    )

async def scan_specific_command(update, context: ContextTypes.DEFAULT_TYPE, hours=4):
    """Общий обработчик для команд сканирования."""
    user_name = update.effective_user.username or update.effective_user.first_name
    command = update.message.text.lower()
    
    logger.info(f"Получена команда {command} от {user_name}")
    
    try:
        await update.message.reply_text(f"🔍 Ищу торговые сигналы с касанием тенью в топ 50 пар по объему за последние {hours} часа(ов), подождите...")
        
        signals = get_signals(hours)
        
        if signals:
            display_signals = signals[:20]
            message = (
                f"🔔 *Найдено {len(signals)} сигналов за последние {hours} часа(ов)* (показаны первые {len(display_signals)}):\n\n" +
                "\n".join(display_signals)
            )
        else:
            message = f"❎ Сигналов с касанием тенью не найдено за последние {hours} часа(ов). Попробуйте позже."
        
        await update.message.reply_text(message, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Ошибка в scan_command: {e}")
        await update.message.reply_text("❌ Произошла ошибка при сканировании. Попробуйте позже.")

async def scan4_command(update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /scan4."""
    await scan_specific_command(update, context, 4)

async def scan10_command(update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /scan10."""
    await scan_specific_command(update, context, 10)

async def scan24_command(update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /scan24."""
    await scan_specific_command(update, context, 24)

def run_bot():
    """Запуск Telegram бота"""
    try:
        application = Application.builder().token(TELEGRAM_TOKEN).build()
        
        # Добавляем обработчики для разных команд
        application.add_handler(CommandHandler('start', start_command))
        application.add_handler(CommandHandler('scan4', scan4_command))
        application.add_handler(CommandHandler('scan10', scan10_command))
        application.add_handler(CommandHandler('scan24', scan24_command))
        
        # Также добавляем обработчик для команды /scan (по умолчанию 4 часа)
        application.add_handler(CommandHandler('scan', scan4_command))
        
        logger.info("🚀 Бот успешно запущен! Доступные команды: /scan4, /scan10, /scan24")
        
        # Запуск бота
        application.run_polling(allowed_updates=None)
        
    except Exception as e:
        logger.error(f"Критическая ошибка при запуске бота: {e}")

def main():
    # Запуск фиктивного веб-сервера для Render
    health_server = start_health_server()
    
    # Запуск бота в отдельном потоке
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.daemon = True
    bot_thread.start()
    
    logger.info("Приложение запущено. Веб-сервер и бот работают.")
    
    # Держим основной поток активным
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Приложение остановлено")

if __name__ == "__main__":
    main()