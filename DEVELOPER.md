# Руководство разработчика

## Публикация изменений на GitHub

```bash
git status
git add .
git commit -m "Описание изменений"
git push origin main
```

---

## Обновление на сервере

### Вариант 1: Через git pull (рекомендуется)

```bash
cd /app/project
git pull origin main
sudo docker-compose restart
```

### Вариант 2: Полная пересборка

```bash
cd /app/project
git pull origin main
sudo docker-compose down
sudo docker-compose build --no-cache
sudo docker-compose up -d
```

---

## Управление контейнером

```bash
# Логи
sudo docker-compose logs -f

# Остановка
sudo docker-compose down

# Запуск
sudo docker-compose up -d

# Перезапуск
sudo docker-compose restart

# Статус
sudo docker-compose ps
```

---

## Структура проекта

```
3xui-shopbot/
├── src/shop_bot/
│   ├── bot/                  # Обработчики бота (handlers, keyboards, middlewares)
│   ├── support_bot/          # Бот поддержки
│   ├── data_manager/         # БД, scheduler, speedtest, backup
│   ├── webhook_server/       # Flask (веб-панель, вебхуки)
│   ├── modules/              # 3x-ui API
│   └── bot_controller.py     # Контроллер запуска/остановки
├── docker-compose.yml
├── Dockerfile
├── install.sh
├── README.md                 # Документация пользователя
└── DEVELOPER.md              # Этот файл
```

---

## База данных

### Расположение
- **Путь в контейнере:** `/app/project/data/users.db`
- **Docker volume:** `shopbot-db`
- **Бэкапы:** `/app/project/data/backups/`

### Проверка
```bash
docker volume ls | grep shopbot-db
docker volume inspect shopbot-db
docker exec 3xui-shopbot du -h /app/project/data/users.db
```

### Ручной бэкап
```bash
docker cp 3xui-shopbot:/app/project/data/users.db ./users.db.backup
```

### Восстановление
```bash
docker-compose down
docker cp ./users.db.backup 3xui-shopbot:/app/project/data/users.db
docker-compose up -d
```

> ⚠️ **Не удаляйте volume `shopbot-db`!**

---

## Переменные окружения

| Переменная | Описание | По умолчанию |
|------------|----------|--------------|
| `SHOPBOT_DB_PATH` | Путь к БД | `/app/project/data/users.db` |
| `AUTO_START_BOT` | Автозапуск ботов | `false` |

### AUTO_START_BOT
- `true` — автозапуск обоих ботов (Production)
- `false` — ручной запуск (Development)

> Настройка в панели имеет приоритет над переменной.

---

## Настройки в базе данных

### Основные
- `telegram_bot_token` — токен бота
- `telegram_bot_username` — username бота
- `admin_telegram_id` — ID администратора
- `panel_login` / `panel_password` — вход в панель

### Платежи
- `yookassa_shop_id` / `yookassa_secret_key`
- `cryptobot_token` — API токен CryptoBot
- `heleket_merchant_id` / `heleket_api_key`
- `ton_wallet_address` / `tonapi_key`

### Системные
- `auto_start_bot` — режим Production
- `backup_interval_days` — автобэкап (дни)
- `speedtest_interval_minutes` — speedtest (минуты)
- `force_subscription` — подписка на канал
- `trial_enabled` / `trial_duration_days` — триал

### Рефералы
- `enable_referrals` — включено/нет
- `referral_reward_type` — тип (percent/fixed/start)
- `referral_percentage` — процент
- `referral_discount` — скидка
- `minimum_withdrawal` — мин. вывод

### Документы
- `terms_url` — URL условий (по умолчанию `/terms`)
- `privacy_url` — URL политики (по умолчанию `/privacy`)

---

## Отладка

### Проверка синтаксиса
```bash
python3 -m py_compile src/shop_bot/__main__.py
python3 -m py_compile src/shop_bot/webhook_server/app.py
```

### Логи бота
```bash
sudo docker logs 3xui-shopbot --tail 100 -f
```

### Поиск ошибок
```bash
sudo docker logs 3xui-shopbot 2>&1 | grep ERROR | tail -20
```

### Сброс кэша Flask
```bash
sudo docker-compose restart
```

---

## Внесение изменений

### Новая настройка
1. Добавить ключ в `ALL_SETTINGS_KEYS` (app.py)
2. Добавить значение по умолчанию (database.py)
3. Добавить UI в шаблон (settings.html)
4. Если чекбокс — добавить в `checkbox_keys`

### Новый маршрут Flask
```python
@flask_app.route('/my-route', methods=['GET', 'POST'])
@login_required
def my_route():
    if request.method == 'POST':
        # Обработка
        pass
    return render_template('template.html')
```

### Обработчик бота
```python
from aiogram import Router, F
from aiogram.types import Message

@user_router.message(F.text == "/command")
async def command_handler(message: Message):
    await message.answer("Hello!")
```

---

## Production Checklist

- [ ] Включить Режим Production (автозапуск)
- [ ] Настроить автобэкап БД
- [ ] Проверить SSL сертификат
- [ ] Настроить мониторинг логов
- [ ] Протестировать все платежи
- [ ] Проверить support-бота
- [ ] Проверить реферальную систему
- [ ] Запустить speedtest для всех хостов
- [ ] Сменить пароль администратора

---

## Полезные команды

```bash
# Размер БД
docker exec 3xui-shopbot du -h /app/project/data/users.db

# Список файлов бэкапа
docker exec 3xui-shopbot ls -lh /app/project/data/backups/

# Статус бота
docker exec 3xui-shopbot python3 -c "from shop_bot.data_manager import database; print(database.get_setting('auto_start_bot'))"

# Перезагрузка без пересборки
docker-compose restart

# Полная пересборка
docker-compose down && docker-compose build --no-cache && docker-compose up -d
```

# 5. Запустите контейнеры
docker-compose up -d
```

---

## Управление контейнером

### Просмотр логов

```bash
docker-compose logs -f
```

### Остановка контейнеров

```bash
docker-compose down
```

### Запуск в фоне

```bash
docker-compose up -d
```

### Перезапуск контейнера

```bash
docker-compose restart
```

---

## Структура проекта

```
3xui-shopbot/
├── src/shop_bot/
│   ├── bot/                      # Обработчики бота (handlers, keyboards, middlewares)
│   ├── data_manager/             # Работа с БД, scheduler, speedtest, backup
│   ├── modules/                  # Интеграция с 3x-ui (xui_api.py)
│   ├── support_bot/              # Бот поддержки
│   ├── webhook_server/           # Flask-сервер (веб-панель, вебхуки платежей)
│   ├── bot_controller.py         # Контроллер запуска/остановки бота
│   └── __main__.py               # Точка входа приложения
├── docker-compose.yml
├── Dockerfile
├── install.sh                    # Скрипт установки
├── README.md                     # Документация для пользователей
└── DEVELOPER.md                  # Документация для разработчиков (этот файл)
```

---

## База данных

### Расположение

- **Путь в контейнере:** `/app/project/data/users.db`
- **Docker volume:** `shopbot-db`
- **Бэкапы:** `/app/project/data/backups/`

### Проверка состояния

```bash
# Проверка тома
docker volume ls | grep shopbot-db

# Информация о томе
docker volume inspect shopbot-db

# Размер БД
docker exec 3xui-shopbot du -h /app/project/data/users.db
```

### Ручной бэкап

```bash
# Копирование БД из контейнера
docker cp 3xui-shopbot:/app/project/data/users.db ./users.db.backup
```

### Восстановление из бэкапа

```bash
# Остановка бота
docker-compose down

# Копирование бэкапа в том
docker cp ./users.db.backup 3xui-shopbot:/app/project/data/users.db

# Запуск бота
docker-compose up -d
```

> ⚠️ **Важно:** Не удаляйте Docker volume `shopbot-db` — это приведёт к потере всех данных!

---

## Переменные окружения

| Переменная | Описание | Значение по умолчанию |
|------------|----------|----------------------|
| `SHOPBOT_DB_PATH` | Путь к файлу базы данных | `/app/project/data/users.db` |
| `AUTO_START_BOT` | Автозапуск ботов при старте контейнера | `false` |

### AUTO_START_BOT

- `true` — оба бота (основной и support) запускаются автоматически (Режим Production)
- `false` — боты запускаются вручную через веб-панель (Development режим)

> Настройка в веб-панели (**Настройки → Настройки панели → Режим Production**) имеет приоритет над переменной окружения.

---

## Отладка

### Проверка синтаксиса Python

```bash
python3 -m py_compile src/shop_bot/__main__.py
python3 -m py_compile src/shop_bot/webhook_server/app.py
```

### Тестирование изменений

1. Внесите изменения в код
2. Проверьте синтаксис
3. Перезапустите контейнер: `docker-compose restart`
4. Проверьте логи: `docker-compose logs -f`

### Сброс кэша шаблонов Flask

Flask кэширует шаблоны. Для принудительного обновления:

```bash
# Перезапуск контейнера
docker-compose restart

# Или полная пересборка
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

---

## Внесение изменений в код

### Добавление новой настройки

1. Добавьте ключ в `ALL_SETTINGS_KEYS` (webhook_server/app.py)
2. Добавьте значение по умолчанию в `default_settings` (data_manager/database.py)
3. Добавьте UI элемент в шаблон (webhook_server/templates/settings.html)
4. Если это чекбокс — добавьте в `checkbox_keys` в обработчике `settings_page`

### Добавление нового маршрута Flask

```python
@flask_app.route('/my-new-route', methods=['GET', 'POST'])
@login_required
def my_new_route():
    if request.method == 'POST':
        # Обработка POST
        pass
    return render_template('my_template.html')
```

### Добавление обработчика бота

```python
from aiogram import Router, F
from aiogram.types import Message

@user_router.message(F.text == "/mycommand")
async def my_command_handler(message: Message):
    await message.answer("Hello!")
```

---

## Production Checklist

Перед развёртыванием в production:

- [ ] Включить **Режим Production** в веб-панели (Настройки → Настройки панели)
- [ ] Настроить автобэкапы БД (ежедневно)
- [ ] Проверить SSL сертификат (Let's Encrypt)
- [ ] Настроить мониторинг логов
- [ ] Проверить все платежи (test + live режим)
- [ ] Проверить работу support-бота
- [ ] Проверить реферальную систему
- [ ] Проверить speedtest для всех хостов
