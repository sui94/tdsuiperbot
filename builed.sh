#!/bin/bash
# Обновляем pip
pip install --upgrade pip

# Устанавливаем пакеты по одному для лучшей совместимости
pip install numpy==1.26.4
pip install pandas==2.2.2
pip install ccxt==4.4.15
pip install ta==0.11.0
pip install httpx==0.27.0
pip install python-telegram-bot==22.3

echo "Все зависимости установлены успешно!"