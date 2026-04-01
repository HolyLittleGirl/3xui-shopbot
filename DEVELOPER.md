# Руководство разработчика

## 📦 Публикация изменений на GitHub

```bash
git status
git add .
git commit -m "Описание изменений"
git push origin main
```

---

## 🔄 Обновление на сервере

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

## 🐳 Управление контейнером

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

## 📁 Структура проекта

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

## 💾 База данных

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

## 🔧 Переменные окружения

| Переменная | Описание | По умолчанию |
|------------|----------|--------------|
| `SHOPBOT_DB_PATH` | Путь к БД | `/app/project/data/users.db` |
| `AUTO_START_BOT` | Автозапуск ботов | `false` |

### AUTO_START_BOT
- `true` — автозапуск обоих ботов (Production)
- `false` — ручной запуск (Development)

> Настройка в панели имеет приоритет над переменной.

---

## ⚙️ Настройки в базе данных

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

## 🐛 Отладка

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

## ✏️ Внесение изменений

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

## ✅ Production Checklist

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

## 🔧 Полезные команды

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

---

## 🗺️ Roadmap

### В разработке
- [ ] Интеграция с реестром запрещённых ресурсов (РКН)
- [ ] Ручное управление блокировками (вкл/выкл)
- [ ] Статус блокировок в веб-панели

### Запланировано
- [ ] Уведомления об обновлении списков блокировок
- [ ] Экспорт логов блокировок
- [ ] Расширенная статистика по платежам

---

## 📝 История изменений

См. [GitHub Releases](https://github.com/HolyLittleGirl/3xui-shopbot/releases)
