# RKN Blocker

Блокировщик запрещённых в РФ ресурсов (Роскомнадзор) через iptables/ipset.

## 📁 Структура

```
rkn-blocker/
├── block_ips.py              # Скрипт блокировки IP
├── rkn_api.py                # HTTP API сервер
├── rkn-block                 # Консольная утилита управления
├── install_rkn.sh            # Установочный скрипт
└── systemd/
    ├── rkn-blocker.service   # systemd сервис блокировщика
    ├── rkn-blocker.timer     # systemd таймер (автообновление)
    └── rkn-api.service       # systemd API сервера
```

## 🚀 Быстрый старт

### Установка

```bash
cd /root/3xui-shopbot/rkn-blocker
sudo bash install_rkn.sh
```

### Управление

```bash
# Статус
rkn-block status

# Включить
rkn-block enable

# Выключить
rkn-block disable

# Обновить списки
rkn-block update
```

## 📚 Документация

Полная документация: `/root/3xui-shopbot/docs/RKN_SETUP.md`

## 🔗 API

- URL: `http://127.0.0.1:8765`
- Token: из `/etc/rkn-blocker.env`

### Эндпоинты

- `GET /status` — статус
- `POST /enable` — включить
- `POST /disable` — выключить
- `POST /update` — обновить
- `POST /toggle` — переключить

## 📊 Логи

- `/var/log/rkn-blocker/block_ips.log` — логи блокировки
- `/var/log/rkn-blocker/api.log` — логи API
- `journalctl -u rkn-blocker.service` — systemd логи
