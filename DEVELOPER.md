# Руководство разработчика

## Публикация изменений на GitHub

```bash
# Проверка статуса
git status

# Добавление изменений
git add .

# Коммит с сообщением
git commit -m "Описание изменений"

# Пуш в main
git push origin main
```

---

## Обновление проекта на сервере

### Вариант 1: Если код монтируется в контейнер (рекомендуется)

```bash
# 1. Перейдите в директорию проекта
cd /app/project

# 2. Обновите код из репозитория
git pull origin main

# 3. Перезапустите контейнер
docker-compose restart
```

### Вариант 2: Если требуется пересборка образа

```bash
# 1. Перейдите в директорию проекта
cd /app/project

# 2. Остановите контейнеры
docker-compose down

# 3. Обновите код из репозитория
git pull origin main

# 4. Пересоберите образ
docker-compose build --no-cache

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
| `AUTO_START_BOT` | Автозапуск бота при старте контейнера | `false` |

### AUTO_START_BOT

- `true` — бот запускается автоматически (Production режим)
- `false` — бот запускается вручную через веб-панель (Development режим)

> Настройка в веб-панели (**Настройки → Настройка бота → Production режим**) имеет приоритет над переменной окружения.

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

- [ ] Включить **Production режим** в веб-панели
- [ ] Настроить автобэкапы БД (ежедневно)
- [ ] Проверить SSL сертификат (Let's Encrypt)
- [ ] Настроить мониторинг логов
- [ ] Убедиться что все платежи работают (test + live режим)
- [ ] Проверить работу support-бота
- [ ] Протестировать реферальную систему
- [ ] Проверить speedtest для всех хостов
