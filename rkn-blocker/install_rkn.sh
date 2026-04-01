#!/bin/bash
#
# RKN Blocker Installation Script
# Установка блокировщика запрещённых ресурсов РФ
#
# Usage:
#   sudo bash install_rkn.sh [--uninstall]
#

set -e

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Логирование
log() { echo -e "${BLUE}[INFO]${NC} $1" | tee -a /var/log/rkn-blocker/install.log; }
success() { echo -e "${GREEN}[OK]${NC} $1" | tee -a /var/log/rkn-blocker/install.log; }
error() { echo -e "${RED}[ERROR]${NC} $1" | tee -a /var/log/rkn-blocker/install.log; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1" | tee -a /var/log/rkn-blocker/install.log; }

# Пути
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/rkn-blocker"
LOG_DIR="/var/log/rkn-blocker"
SYSTEMD_DIR="/etc/systemd/system"
ENV_FILE="/etc/rkn-blocker.env"
VENV_DIR="$INSTALL_DIR/venv"

# Генерация случайного токена
generate_token() {
    openssl rand -hex 16
}

# Проверка прав root
check_root() {
    if [[ $EUID -ne 0 ]]; then
        error "Этот скрипт должен быть запущен от root (sudo)"
        exit 1
    fi
}

# Создание директорий
create_directories() {
    log "Создание директорий..."
    mkdir -p "$INSTALL_DIR"
    mkdir -p "$LOG_DIR"
    chmod 755 "$INSTALL_DIR"
    chmod 755 "$LOG_DIR"
    success "Директории созданы"
}

# Копирование файлов
copy_files() {
    log "Копирование файлов..."
    cp "$SCRIPT_DIR/block_ips.py" "$INSTALL_DIR/"
    cp "$SCRIPT_DIR/rkn_api.py" "$INSTALL_DIR/"
    cp "$SCRIPT_DIR/systemd/rkn-blocker.service" "$SYSTEMD_DIR/"
    cp "$SCRIPT_DIR/systemd/rkn-blocker.timer" "$SYSTEMD_DIR/"
    cp "$SCRIPT_DIR/systemd/rkn-api.service" "$SYSTEMD_DIR/"
    chmod +x "$INSTALL_DIR/block_ips.py"
    chmod +x "$INSTALL_DIR/rkn_api.py"
    success "Файлы скопированы"
}

# Установка зависимостей
install_dependencies() {
    log "Проверка версий Python..."
    PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}' | cut -d'.' -f1,2)
    
    if [[ -z "$PYTHON_VERSION" ]]; then
        error "Не удалось определить версию Python"
        exit 1
    fi
    
    log "Обнаружена версия Python: $PYTHON_VERSION"
    
    log "Установка системных пакетов..."
    apt-get update -qq
    apt-get install -y -qq python3 "python${PYTHON_VERSION}-venv" iptables ipset openssl curl >> /var/log/rkn-blocker/install.log 2>&1
    
    if [[ $? -ne 0 ]]; then
        error "Не удалось установить системные пакеты"
        exit 1
    fi
    
    success "Системные пакеты установлены"
}

# Создание виртуального окружения
create_venv() {
    log "Создание виртуального окружения..."
    
    if [[ -d "$VENV_DIR" ]]; then
        warn "Виртуальное окружение уже существует, пересоздаю..."
        rm -rf "$VENV_DIR"
    fi
    
    python3 -m venv "$VENV_DIR"
    
    log "Установка Python зависимостей..."
    "$VENV_DIR/bin/pip" install --upgrade pip >> /var/log/rkn-blocker/install.log 2>&1
    "$VENV_DIR/bin/pip" install requests flask >> /var/log/rkn-blocker/install.log 2>&1
    
    success "Виртуальное окружение создано"
}

# Настройка окружения
setup_environment() {
    log "Настройка окружения..."
    
    # Генерация токена
    API_TOKEN=$(generate_token)
    
    # Сохранение токена
    cat > "$ENV_FILE" << EOF
# RKN Blocker Environment
# Generated: $(date)
RKN_API_TOKEN=$API_TOKEN
INSTALL_DIR=$INSTALL_DIR
LOG_DIR=$LOG_DIR
EOF
    
    chmod 600 "$ENV_FILE"
    
    success "Окружение настроено"
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${YELLOW}ВАЖНО: Сохраните ваш API токен!${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo -e "API Token: ${YELLOW}$API_TOKEN${NC}"
    echo -e "Файл с токеном: ${YELLOW}$ENV_FILE${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
}

# Настройка systemd
setup_systemd() {
    log "Настройка systemd сервисов..."
    
    # Перезагрузка демонов systemd
    systemctl daemon-reload
    
    # Включение сервисов
    systemctl enable rkn-api.service
    systemctl enable rkn-blocker.timer
    
    # Запуск API сервера
    systemctl start rkn-api.service
    
    # Проверка статуса
    if systemctl is-active --quiet rkn-api.service; then
        success "RKN API сервер запущен"
    else
        error "Не удалось запустить RKN API сервер"
        systemctl status rkn-api.service --no-pager
        exit 1
    fi
    
    # Проверка таймера
    if systemctl is-active --quiet rkn-blocker.timer; then
        success "RKN таймер активирован"
    else
        warn "Таймер не активен (запустится при включении блокировки)"
    fi
    
    success "Systemd сервисы настроены"
}

# Показ инструкции
show_instructions() {
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}✅ Установка RKN блокировщика завершена!${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    echo -e "${BLUE}Команды управления:${NC}"
    echo "  rkn-block enable    - Включить блокировку"
    echo "  rkn-block disable   - Выключить блокировку"
    echo "  rkn-block status    - Показать статус"
    echo "  rkn-block update    - Обновить списки"
    echo ""
    echo -e "${BLUE}API эндпоинты:${NC}"
    echo "  GET  http://127.0.0.1:8765/status  - Статус"
    echo "  POST http://127.0.0.1:8765/enable  - Включить"
    echo "  POST http://127.0.0.1:8765/disable - Выключить"
    echo "  POST http://127.0.0.1:8765/update  - Обновить"
    echo ""
    echo -e "${BLUE}Логирование:${NC}"
    echo "  journalctl -u rkn-blocker.service -f  - Логи блокировщика"
    echo "  journalctl -u rkn-api.service -f      - Логи API"
    echo "  /var/log/rkn-blocker/block_ips.log    - Лог блокировок"
    echo ""
    echo -e "${BLUE}Интеграция с 3xui-shopbot:${NC}"
    echo "  1. Скопируйте API токен из $ENV_FILE"
    echo "  2. В веб-панели shopbot перейдите в Настройки → РКН"
    echo "  3. Введите токен и включите блокировку"
    echo ""
}

# Деинсталляция
uninstall() {
    echo ""
    warn "Запуск деинсталляции RKN блокировщика..."
    echo ""
    
    # Остановка сервисов
    log "Остановка сервисов..."
    systemctl stop rkn-api.service 2>/dev/null || true
    systemctl stop rkn-blocker.service 2>/dev/null || true
    systemctl disable rkn-api.service 2>/dev/null || true
    systemctl disable rkn-blocker.timer 2>/dev/null || true
    
    # Отключение блокировки
    log "Отключение блокировки (iptables, ipset)..."
    iptables -D OUTPUT -m set --match-set rkn_blocked dst -j DROP 2>/dev/null || true
    ipset destroy rkn_blocked 2>/dev/null || true
    
    # Удаление файлов
    log "Удаление файлов..."
    rm -rf "$INSTALL_DIR"
    rm -rf "$LOG_DIR"
    rm -f "$SYSTEMD_DIR/rkn-blocker.service"
    rm -f "$SYSTEMD_DIR/rkn-blocker.timer"
    rm -f "$SYSTEMD_DIR/rkn-api.service"
    rm -f "$ENV_FILE"
    
    # Перезагрузка systemd
    systemctl daemon-reload
    
    success "RKN блокировщик полностью удалён"
    echo ""
    echo "Примечание: Логи установки сохранены в /var/log/rkn-blocker/install.log"
}

# Главная функция
main() {
    echo ""
    echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║   RKN Blocker Installation Script      ║${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════╝${NC}"
    echo ""
    
    check_root
    
    if [[ "$1" == "--uninstall" ]]; then
        uninstall
        exit 0
    fi
    
    create_directories
    install_dependencies
    copy_files
    create_venv
    setup_environment
    setup_systemd
    show_instructions
}

# Запуск
main "$@"
