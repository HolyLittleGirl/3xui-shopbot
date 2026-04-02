# 🛡️ RKN Blocker — Блокировка запрещённых ресурсов РФ

**RKN Blocker** — модуль автоматической блокировки запрещённых в Российской Федерации ресурсов через доменную фильтрацию в 3x-ui routing.

## 📋 Оглавление

- [Описание](#описание)
- [Архитектура](#архитектура)
- [Установка](#установка)
- [Управление](#управление)
- [Интеграция с 3xui-shopbot](#интеграция-с-3xui-shopbot)
- [API](#api)
- [Troubleshooting](#troubleshooting)

---

## 📖 Описание

Модуль предназначен для автоматической блокировки доступа к ресурсам, внесённым в реестр запрещённых в РФ (Роскомнадзор).

### Возможности

- ✅ **Автоматическая загрузка** ~43,000 доменов из GitHub
- ✅ **Блокировка на уровне Xray routing** через 3x-ui
- ✅ **Автообновление** при включении/выключении
- ✅ **HTTP API** для интеграции с внешними системами
- ✅ **Telegram бот** и веб-панель для управления
- ✅ **Whitelist** IP серверов для корректной работы

### Источники блокировок

Используется список доменов из GitHub:
- **1andrevich/Re-filter-lists** — ~43,000 доменов запрещённых ресурсов
- Формат: JSON с правилами маршрутизации
- Автоматическая загрузка при включении RKN

---

## 🏗️ Архитектура

```
┌─────────────────────────────────────────────────────────┐
│  Хост-сервер (Ubuntu/Debian)                            │
│  ┌─────────────────────────────────────────────────┐    │
│  │  /opt/rkn-blocker/                              │    │
│  │  ├── block_ips.py       (блокировка IP)         │    │
│  │  ├── rkn_api.py         (HTTP API сервер)       │    │
│  │  └── venv/              (Python окружение)      │    │
│  └─────────────────────────────────────────────────┘    │
│                          ↑                               │
│  ┌───────────────────────┴───────────────────────────┐  │
│  │  systemd сервисы:                                 │  │
│  │  • rkn-blocker.service  (блокировка)              │  │
│  │  • rkn-blocker.timer    (автообновление)          │  │
│  │  • rkn-api.service      (API сервер)              │  │
│  └───────────────────────────────────────────────────┘  │
│                          ↑                               │
│  ┌───────────────────────┴───────────────────────────┐  │
│  │  Docker Container (3xui-shopbot)                  │  │
│  │  ├── Веб-панель: Настройки → РКН                  │  │
│  │  ├── Telegram Bot: Админ-меню → РКН Блокировка    │  │
│  │  └── rkn_client.py (HTTP клиент)                  │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### Компоненты

| Компонент | Описание | Путь |
|-----------|----------|------|
| **block_ips.py** | Скрипт блокировки IP | `/opt/rkn-blocker/block_ips.py` |
| **rkn_api.py** | HTTP API сервер | `/opt/rkn-blocker/rkn_api.py` |
| **rkn-block** | Консольная утилита | `/usr/local/bin/rkn-block` |
| **systemd сервисы** | Автозапуск и планировщик | `/etc/systemd/system/` |
| **Конфигурация** | API токен и настройки | `/etc/rkn-blocker.env` |

---

## 🚀 Установка

### Требования

- Ubuntu/Debian сервер с root доступом
- Python 3.8+
- iptables, ipset
- 3xui-shopbot (опционально, для интеграции)

### Пошаговая установка

#### 1. Скопируйте файлы блокировщика

```bash
# Если используете backup версию:
cp -r /home/foxxx/3xui-shop/release-no-rkn/rkn-blocker /root/3xui-shopbot/

# Или из git репозитория:
cd /root/3xui-shopbot
git pull origin feature/rkn-module
```

#### 2. Запустите установочный скрипт

```bash
cd /root/3xui-shopbot/rkn-blocker
sudo bash install_rkn.sh
```

#### 3. Сохраните API токен

После установки вы увидите:

```
========================================
ВАЖНО: Сохраните ваш API токен!
========================================
API Token: abc123def456...
Файл с токеном: /etc/rkn-blocker.env
========================================
```

**⚠️ Сохраните токен!** Он понадобится для интеграции с веб-панелью и ботом.

#### 4. Проверьте установку

```bash
# Проверка статуса
rkn-block status

# Проверка сервисов
systemctl status rkn-api.service
systemctl status rkn-blocker.timer
```

---

## 🎛️ Управление

### Консольная утилита `rkn-block`

```bash
# Показать статус
rkn-block status

# Включить блокировку
rkn-block enable

# Выключить блокировку
rkn-block disable

# Обновить списки
rkn-block update

# Переключить состояние
rkn-block toggle

# Показать логи
rkn-block logs
```

### systemd команды

```bash
# Перезапуск API сервера
sudo systemctl restart rkn-api.service

# Просмотр логов
sudo journalctl -u rkn-blocker.service -f
sudo journalctl -u rkn-api.service -f

# Остановка блокировки
sudo systemctl stop rkn-blocker.service
sudo systemctl disable rkn-blocker.timer
```

### Прямой вызов скрипта

```bash
# Включить блокировку
sudo /opt/rkn-blocker/venv/bin/python3 /opt/rkn-blocker/block_ips.py enable

# Получить статус в JSON
sudo /opt/rkn-blocker/venv/bin/python3 /opt/rkn-blocker/block_ips.py status --json
```

---

## 🔗 Интеграция с 3xui-shopbot

### Настройка в веб-панели

1. Откройте веб-панель: `https://your-domain.com/settings`
2. Перейдите в раздел **РКН** (жёлтая ссылка в навигации)
3. Заполните поля:
   - **RKN API URL**: `http://127.0.0.1:8765`
   - **RKN API Token**: токен из `/etc/rkn-blocker.env`
4. Нажмите **Сохранить**
5. Используйте кнопки для управления:
   - ✅ Включить блокировку
   - ⏹️ Выключить блокировку
   - ⬇️ Обновить списки

### Управление через Telegram бота

1. Откройте бота
2. Нажмите **Админка** → **🛡️ РКН Блокировка**
3. Доступные действия:
   - Включить/Выключить блокировку
   - Обновить списки
   - Просмотр настроек

### Настройка токена в базе данных

Если нужно установить токен напрямую в БД:

```bash
sqlite3 /root/3xui-shopbot/data/users.db "UPDATE bot_settings SET value='YOUR_TOKEN' WHERE key='rkn_api_token';"
```

---

## 🌐 API

RKN Blocker предоставляет HTTP API для интеграции.

### Базовый URL

```
http://127.0.0.1:8765
```

### Авторизация

Все запросы требуют заголовок:
```
X-RKN-Token: your-api-token
```

### Эндпоинты

#### GET /status
Получить статус блокировщика.

**Ответ:**
```json
{
  "enabled": true,
  "blocked_count": 14048,
  "last_update": "2026-04-01T12:00:00",
  "ipset_exists": true,
  "iptables_exists": true
}
```

#### POST /enable
Включить блокировку.

**Запрос:**
```json
{"token": "your-api-token"}
```

**Ответ:**
```json
{
  "success": true,
  "blocked_count": 14048,
  "last_update": "2026-04-01T12:00:00"
}
```

#### POST /disable
Выключить блокировку.

#### POST /update
Обновить списки блокировки.

#### POST /toggle
Переключить состояние (вкл/выкл).

#### GET /health
Health check эндпоинт (без авторизации).

### Примеры curl

```bash
# Статус
curl -H "X-RKN-Token: YOUR_TOKEN" http://127.0.0.1:8765/status

# Включить
curl -X POST \
  -H "X-RKN-Token: YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"token":"YOUR_TOKEN"}' \
  http://127.0.0.1:8765/enable

# Выключить
curl -X POST \
  -H "X-RKN-Token: YOUR_TOKEN" \
  http://127.0.0.1:8765/disable
```

---

## 🔧 Troubleshooting

### Блокировка не работает

1. Проверьте статус сервисов:
```bash
systemctl status rkn-api.service
systemctl status rkn-blocker.timer
```

2. Проверьте iptables:
```bash
sudo iptables -L OUTPUT -n -v | grep rkn_blocked
```

3. Проверьте ipset:
```bash
sudo ipset list rkn_blocked
```

### Ошибка авторизации API

1. Проверьте токен:
```bash
cat /etc/rkn-blocker.env
```

2. Перезапустите API:
```bash
sudo systemctl restart rkn-api.service
```

### Проблемы с обновлением списков

1. Проверьте логи:
```bash
sudo journalctl -u rkn-blocker.service -n 50
cat /var/log/rkn-blocker/block_ips.log
```

2. Проверьте доступность antifilter.download:
```bash
curl -I https://antifilter.download/list/allyouneed.lst
```

### Ошибки iptables/ipset

```bash
# Пересоздать ipset
sudo ipset destroy rkn_blocked
sudo /opt/rkn-blocker/venv/bin/python3 /opt/rkn-blocker/block_ips.py enable

# Проверить правила
sudo iptables -L OUTPUT -n -v
```

### Сброс блокировки

Для полного отключения блокировки:

```bash
# Через утилиту
rkn-block disable

# Вручную
sudo iptables -D OUTPUT -m set --match-set rkn_blocked dst -j DROP 2>/dev/null || true
sudo ipset destroy rkn_blocked 2>/dev/null || true
sudo systemctl stop rkn-blocker.service
```

---

## 📊 Мониторинг

### Логи

| Лог | Путь |
|-----|------|
| Блокировка IP | `/var/log/rkn-blocker/block_ips.log` |
| API сервер | `/var/log/rkn-blocker/api.log` |
| Установка | `/var/log/rkn-blocker/install.log` |
| systemd | `journalctl -u rkn-blocker.service` |

### Метрики для мониторинга

- `enabled` — статус блокировки (bool)
- `blocked_count` — количество заблокированных IP (int)
- `last_update` — время последнего обновления (ISO 8601)
- API health — `/health` эндпоинт

---

## 🔒 Безопасность

- API токен хранится в `/etc/rkn-blocker.env` с правами `600`
- API сервер слушает только на `127.0.0.1` (не доступен извне)
- iptables правила применяются только к исходящему трафику (OUTPUT chain)
- Для работы требуются права root

---

## 📚 Источники

- [antifilter.download](https://antifilter.download/) — списки запрещённых ресурсов
- [3xui-shopbot](https://github.com/HolyLittleGirl/3xui-shopbot) — основной проект
- [WhiteVPN](https://github.com/HolyLittleGirl/whitevpn) — референс реализации

---

## 📞 Поддержка

- GitHub Issues: [создать issue](https://github.com/HolyLittleGirl/3xui-shopbot/issues)
- Документация: `/root/3xui-shopbot/docs/RKN_SETUP.md`

---

**Версия:** 1.0.0  
**Дата:** 2026-04-01
