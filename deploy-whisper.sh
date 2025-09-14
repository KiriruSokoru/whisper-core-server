#!/bin/bash

# =============================================================================
# Whisper Core Server - Скрипт автоматического развертывания
# =============================================================================

set -e  # Прерывание выполнения при любой ошибке

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Переменные конфигурации
REPO_URL="git@github.com:KiriruSokoru/whisper-core-server.git"
DEPLOY_DIR="/opt/whisper-core-server"
APP_DATA_DIR="/opt/whisper-app-data"
SHARED_DIR="/opt/shared"
DOCKER_NETWORK="whisper-network"

# Функции для логирования
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Проверка прав доступа
check_permissions() {
    if [[ $EUID -eq 0 ]]; then
        log_error "Этот скрипт не должен запускаться от root. Запустите от обычного пользователя с sudo правами."
        exit 1
    fi
    
    if ! sudo -n true 2>/dev/null; then
        log_error "У пользователя нет sudo прав"
        exit 1
    fi
}

# Установка зависимостей
install_dependencies() {
    log_info "Установка системных зависимостей..."
    
    # Обновление пакетов
    sudo apt update
    
    # Установка Docker и Docker Compose
    if ! command -v docker &> /dev/null; then
        log_info "Установка Docker..."
        sudo apt install -y docker.io
        sudo systemctl enable docker
        sudo systemctl start docker
    fi
    
    if ! command -v docker-compose &> /dev/null; then
        log_info "Установка Docker Compose..."
        sudo apt install -y docker-compose-plugin
    fi
    
    # Добавление текущего пользователя в группу docker
    if ! groups $USER | grep -q '\bdocker\b'; then
        sudo usermod -aG docker $USER
        log_warning "Пользователь добавлен в группу docker. Необходимо перелогиниться или выполнить 'newgrp docker'"
    fi
    
    # Установка Git
    if ! command -v git &> /dev/null; then
        sudo apt install -y git
    fi
    
    sudo apt install -y jq  # Для обработки JSON
}

# Настройка структуры директорий
setup_directories() {
    log_info "Создание структуры директорий..."
    
    # Создаем основные директории
    sudo mkdir -p $DEPLOY_DIR $APP_DATA_DIR $SHARED_DIR
    sudo mkdir -p $APP_DATA_DIR/{in,out,processed}
    sudo mkdir -p $SHARED_DIR/{pending,processing,completed,failed}
    
    # Устанавливаем правильные права
    sudo chown -R $USER:$USER $DEPLOY_DIR $APP_DATA_DIR $SHARED_DIR
    sudo chmod -R 755 $DEPLOY_DIR $APP_DATA_DIR $SHARED_DIR
}

# Клонирование или обновление репозитория
setup_repository() {
    log_info "Настройка репозитория..."
    
    if [ -d "$DEPLOY_DIR/.git" ]; then
        log_info "Обновление существующего репозитория..."
        cd $DEPLOY_DIR
        git pull origin main
    else
        log_info "Клонирование репозитория..."
        git clone $REPO_URL $DEPLOY_DIR
        cd $DEPLOY_DIR
    fi
}

# Настройка переменных окружения
setup_environment() {
    log_info "Настройка переменных окружения..."
    
    cd $DEPLOY_DIR
    
    if [ ! -f .env ]; then
        log_info "Создание файла .env из примера..."
        if [ -f .env.example ]; then
            cp .env.example .env
        else
            log_error "Файл .env.example не найден!"
            exit 1
        fi
    fi
    
    # Запрос критических параметров у пользователя
    if grep -q "<ВАШ_СЛОЖНЫЙ_ПАРОЛЬ_БД>" .env; then
        echo -e "${YELLOW}Необходимо настроить критически важные параметры:${NC}"
        
        # Пароль для PostgreSQL
        read -sp "Введите пароль для PostgreSQL БД: " db_password
        echo
        sed -i "s/<ВАШ_СЛОЖНЫЙ_ПАРОЛЬ_БД>/$db_password/" .env
        
        # Пароль для Grafana
        read -sp "Введите пароль для Grafana: " grafana_password
        echo
        sed -i "s/<ВАШ_ПАРОЛЬ_GRAFANA>/$grafana_password/" .env
        
        # Остальные настройки
        read -p "Введите IP адрес сервера [192.168.1.6]: " server_ip
        server_ip=${server_ip:-192.168.1.6}
        sed -i "s/SERVER_IP=.*/SERVER_IP=$server_ip/" .env
        
        read -p "Введите IP адрес Windows-станции [192.168.1.3]: " client_ip
        client_ip=${client_ip:-192.168.1.3}
        sed -i "s/CLIENT_IP=.*/CLIENT_IP=$client_ip/" .env
    else
        log_info "Файл .env уже настроен, пропускаем запрос параметров"
    fi
}

# Настройка Docker сети
setup_docker_network() {
    log_info "Настройка Docker сети..."
    
    if ! docker network inspect $DOCKER_NETWORK >/dev/null 2>&1; then
        docker network create --subnet=172.20.0.0/24 $DOCKER_NETWORK
        log_success "Docker сеть $DOCKER_NETWORK создана"
    else
        log_info "Docker сеть $DOCKER_NETWORK уже существует"
    fi
}

# Сборка и запуск контейнеров
start_containers() {
    log_info "Запуск контейнеров..."
    
    cd $DEPLOY_DIR
    
    # Проверка существования docker-compose файлов
    if [ ! -f docker-compose.yml ]; then
        log_error "Файл docker-compose.yml не найден!"
        exit 1
    fi
    
    # Сборка и запуск основных сервисов
    log_info "Запуск основных сервисов..."
    docker-compose up -d --build
    
    # Проверка наличия файла мониторинга
    if [ -f docker-compose-monitoring.yml ]; then
        log_info "Запуск сервисов мониторинга..."
        docker-compose -f docker-compose-monitoring.yml up -d
    else
        log_warning "Файл docker-compose-monitoring.yml не найден, мониторинг не будет запущен"
    fi
    
    # Ожидание инициализации БД
    log_info "Ожидание инициализации PostgreSQL..."
    sleep 10
    
    # Проверка статуса контейнеров
    log_info "Проверка статуса контейнеров..."
    docker-compose ps
}

# Настройка брандмауэра
setup_firewall() {
    log_info "Настройка брандмауэра..."
    
    if command -v ufw &> /dev/null && sudo ufw status | grep -q "Status: active"; then
        log_info "Настройка правил UFW..."
        sudo ufw allow ssh
        sudo ufw allow from 192.168.1.0/24 to any port 5432 comment "PostgreSQL for internal network"
        sudo ufw allow from 192.168.1.3 to any port 3000 comment "Grafana for Windows station"
        log_success "Правила UFW настроены"
    else
        log_info "UFW не активен, пропускаем настройку правил"
    fi
}

# Проверка работоспособности
check_health() {
    log_info "Проверка работоспособности системы..."
    
    # Проверка контейнеров
    if docker ps | grep -q "whisper-postgres"; then
        log_success "Контейнер PostgreSQL запущен"
    else
        log_error "Контейнер PostgreSQL не запущен!"
    fi
    
    # Проверка доступности БД
    if docker exec whisper-postgres pg_isready -U whisper_user -d whisper_db; then
        log_success "База данных доступна"
    else
        log_error "База данных недоступна!"
    fi
    
    # Проверка мониторинга
    if curl -s http://localhost:3000 >/dev/null; then
        log_success "Grafana доступна по http://localhost:3000"
    else
        log_warning "Grafana недоступна"
    fi
    
    if curl -s http://localhost:9090 >/dev/null; then
        log_success "Prometheus доступен по http://localhost:9090"
    else
        log_warning "Prometheus недоступен"
    fi
}

# Отображение итоговой информации
show_summary() {
    echo -e "${GREEN}"
    echo "===================================================================="
    echo " Whisper Core Server успешно развернут!"
    echo "===================================================================="
    echo -e "${NC}"
    
    echo "Сервисы:"
    echo "  - PostgreSQL: доступен на localhost:5432"
    echo "  - Grafana:    http://localhost:3000"
    echo "  - Prometheus: http://localhost:9090"
    echo "  - Loki:       http://localhost:3100"
    echo ""
    
    echo "Данные:"
    echo "  - Исходные аудиофайлы: $APP_DATA_DIR/in/"
    echo "  - Транскрипции:        $APP_DATA_DIR/out/"
    echo "  - Задачи анализа:      $SHARED_DIR/pending/"
    echo ""
    
    echo "Дашборды Grafana:"
    echo "  - Логин: admin"
    echo "  - Пароль: (указан в файле .env)"
    echo ""
    
    echo "Следующие шаги:"
    echo "  1. Настройте LM Studio на Windows-станции (192.168.1.3)"
    echo "  2. Разместите аудиофайлы в $APP_DATA_DIR/in/"
    echo "  3. Мониторьте процесс через Grafana: http://localhost:3000"
    echo ""
    
    echo -e "${YELLOW}Важно: Не забудьте перелогиниться или выполнить 'newgrp docker'${NC}"
    echo -e "${YELLOW}для применения изменений групп пользователя!${NC}"
    echo ""
}

# Основная функция
main() {
    echo -e "${GREEN}"
    echo "===================================================================="
    echo " Автоматическое развертывание Whisper Core Server"
    echo "===================================================================="
    echo -e "${NC}"
    
    # Выполняем шаги развертывания
    check_permissions
    install_dependencies
    setup_directories
    setup_repository
    setup_environment
    setup_docker_network
    start_containers
    setup_firewall
    check_health
    show_summary
}

# Обработка прерывания
trap 'log_error "Прервано пользователем"; exit 1' INT

# Запуск основной функции
main
