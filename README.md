# 📋 Whisper Core Server - Полное руководство по развертыванию

## 🎯 Обзор системы

**Whisper Core Server** — это комплексная система транскрибации и анализа телефонных звонков, построенная по гибридной архитектуре с разделением на Linux-сервер (обработка и хранение) и Windows-станцию (анализ с помощью LM Studio).

### 🔧 Ключевые компоненты
- **Транскрибация**: OpenAI Whisper для преобразования аудио в текст
- **Хранение данных**: PostgreSQL для структурированного хранения
- **Анализ**: LM Studio с моделью Mistral-7B для анализа контента
- **Мониторинг**: Prometheus, Grafana, Loki для полного наблюдения
- **Безопасность**: Изолированная сеть, принцип наименьших привилегий

---

## 🛠️ Предварительные требования

### 🔍 Аппаратные требования
- **Linux-сервер**: 4+ ядра CPU, 16+ GB RAM, 100+ GB SSD
- **Windows-станция**: 8+ ядер CPU, 32+ GB RAM, GPU (рекомендуется)
- **Сетевая инфраструктура**: Гигабитная локальная сеть между узлами

### 📦 Программные требования
**На Linux-сервере (192.168.1.6 - пример айпи):**
```bash
# Проверка и установка Docker
sudo apt update && sudo apt install -y docker.io docker-compose-plugin

# Проверка установки
docker --version && docker compose version

# Создание пользователя для приложения (Best Practice: не использовать root)
sudo useradd -m -s /bin/bash whisper
sudo usermod -aG docker whisper
```

**На Windows-станции (192.168.1.3 - пример айпи):**
- Windows 10/11 Pro или Server 2019+
- Python 3.11+
- LM Studio с настроенной моделью Mistral-7B-Instruct

---

## 📁 Подготовка структуры проекта

### 🗂️ Создание директорий (Linux-сервер)
```bash
# От имени администратора создаем базовую структуру
sudo mkdir -p /opt/{whisper-core-server,whisper-app-data,shared}
sudo chown -R whisper:whisper /opt/{whisper-core-server,whisper-app-data,shared}

# Создаем поддиректории для организации данных
sudo -u whisper mkdir -p /opt/whisper-core-server/{scripts,configs,loki,prometheus,grafana}
sudo -u whisper mkdir -p /opt/whisper-app-data/{in,out,processed}
sudo -u whisper mkdir -p /opt/shared/{pending,processing,completed,failed}
```

**Зачем это нужно**: Разделение данных и кода повышает безопасность и упрощает резервное копирование. Использование отдельного пользователя `whisper` следует принципу наименьших привилегий.

---

## 🔧 Конфигурация системы

### 📝 Настройка переменных окружения
Создайте файл `.env` в `/opt/whisper-core-server/`:

```bash
# От имени пользователя whisper
sudo -u whisper nano /opt/whisper-core-server/.env
```

```ini
# =============================================================================
# БАЗА ДАННЫХ POSTGRESQL
# =============================================================================
POSTGRES_DB=whisper_db
POSTGRES_USER=whisper_user
POSTGRES_PASSWORD=<ВАШ_СЛОЖНЫЙ_ПАРОЛЬ_БД>  # Минимум 16 символов, буквы, цифры, специальные символы
DATABASE_HOST=postgres
DATABASE_PORT=5432

# =============================================================================
# НАСТРОЙКИ СЕТИ И БЕЗОПАСНОСТИ
# =============================================================================
DOCKER_NETWORK=whisper-network
SERVER_IP=192.168.1.6
CLIENT_IP=192.168.1.3

# =============================================================================
# НАСТРОЙКИ ПУТЕЙ
# =============================================================================
WHISPER_APP_DATA=/opt/whisper-app-data
SHARED_DIR=/opt/shared

# =============================================================================
# НАСТРОЙКИ LM STUDIO (для Windows-станции)
# =============================================================================
LM_STUDIO_URL=http://localhost:1234/v1/chat/completions
LM_STUDIO_MODEL=mistral-7b-instruct-v0.3-q4_k_m.gguf

# =============================================================================
# НАСТРОЙКИ МОНИТОРИНГА
# =============================================================================
GF_SECURITY_ADMIN_PASSWORD=<ВАШ_ПАРОЛЬ_GRAFANA>  # Отличный от пароля БД
LOKI_PERSISTENT_DIR=/opt/loki-data
```

**Best Practice**: Хранение чувствительных данных в `.env` файле, который исключен из Git через `.gitignore`.

---

## 🐳 Docker Compose конфигурация

### 📋 Основной docker-compose.yml
```yaml
version: '3.8'

services:
  # PostgreSQL database
  postgres:
    image: postgres:15-alpine  # Best Practice: использование конкретной версии и alpine для уменьшения размера
    container_name: whisper-postgres
    env_file: .env
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./scripts/init.sql:/docker-entrypoint-initdb.d/init.sql
    networks:
      - whisper-network
    restart: unless-stopped  # Best Practice: автоматический перезапуск при падении
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 30s
      timeout: 10s
      retries: 3

  # DB Loader service
  db-loader:
    build:
      context: .
      dockerfile: Dockerfile.db-loader
    container_name: whisper-db-loader
    env_file: .env
    volumes:
      - ${WHISPER_APP_DATA}/out:/app/out:ro  # Best Practice: монтирование только для чтения
    networks:
      - whisper-network
    depends_on:
      postgres:
        condition: service_healthy
    restart: unless-stopped
    user: "1000:1000"  # Best Practice: запуск от не-root пользователя

networks:
  whisper-network:
    driver: bridge
    ipam:
      config:
        - subnet: 172.20.0.0/24  # Best Practice: использование фиксированной подсети

volumes:
  postgres_data:
    name: whisper_postgres_data  # Best Practice: явное именование томов
```

**Зачем такие настройки**: 
- `restart: unless-stopped` обеспечивает автоматическое восстановление после перезагрузки
- `healthcheck` гарантирует, что сервисы запускаются в правильном порядке
- Явное именование сетей и томов предотвращает конфликты

---

## 📊 Мониторинг и логирование

### 📈 Конфигурация Prometheus
```yaml
# configs/prometheus/prometheus.yml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'node-exporter'
    static_configs:
      - targets: ['node-exporter:9100']

  - job_name: 'cadvisor'
    static_configs:
      - targets: ['cadvisor:8080']

  - job_name: 'whisper-services'
    metrics_path: /metrics
    static_configs:
      - targets: ['db-loader:8000', 'generator:8001']
```

### 📝 Конфигурация Loki
```yaml
# configs/loki/loki-local-config.yaml
auth_enabled: false

server:
  http_listen_port: 3100

common:
  ring:
    instance_addr: 127.0.0.1
    kvstore:
      store: inmemory
  replication_factor: 1
  path_prefix: /tmp/loki

schema_config:
  configs:
    - from: 2020-10-24
      store: boltdb-shipper
      object_store: filesystem
      schema: v11
      index:
        prefix: index_
        period: 24h
```

---

## 🚀 Запуск системы

### 📋 Пошаговый процесс развертывания

1. **Инициализация репозитория**
```bash
cd /opt/whisper-core-server
sudo -u whisper git init
sudo -u whisper git remote add origin git@github.com:KiriruSokoru/whisper-core-server.git
```

2. **Настройка прав доступа**
```bash
# Best Practice: рекурсивное изменение владельца для избежания проблем с правами
sudo chown -R whisper:whisper /opt/whisper-core-server
```

3. **Запуск основных сервисов**
```bash
sudo -u whisper docker-compose up -d --build

# Проверка статуса контейнеров
sudo -u whisper docker-compose ps
sudo -u whisper docker-compose logs -f
```

4. **Запуск мониторинга**
```bash
sudo -u whisper docker-compose -f docker-compose-monitoring.yml up -d

# Проверка работы мониторинга
curl http://localhost:9090/targets  # Prometheus targets
curl http://localhost:3000          # Grafana
```

5. **Настройка Windows-станции**
```powershell
# Установка Python и зависимостей
winget install Python.Python.3.11
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt

# Настройка LM Studio (вручную через GUI)
# Запуск модели Mistral-7B-Instruct на порту 1234
```

---

## ✅ Проверка работоспособности

### 🔍 Тестирование компонентов

**Проверка базы данных:**
```bash
sudo -u whisper docker exec whisper-postgres psql -U whisper_user -d whisper_db -c "SELECT version(); SELECT COUNT(*) FROM transcriptions;"
```

**Проверка генератора задач:**
```bash
# Создание тестового файла транскрипции
echo "Тестовая транскрипция" | sudo -u whisper tee /opt/whisper-app-data/out/test_transcription.txt

# Проверка обработки через 2 минуты
watch -n 2 'sudo -u whisper docker exec whisper-postgres psql -U whisper_user -d whisper_db -c "SELECT * FROM transcriptions;"'
```

**Проверка мониторинга:**
1. Откройте Grafana: http://192.168.1.6:3000 (ваш айпи основного сервера с транскрипциями)
2. Логин: admin / пароль из .env файла
3. Добавьте источники данных: Prometheus (http://prometheus:9090) и Loki (http://loki:3100)

---

## 🔒 Безопасность

### 🛡️ Рекомендуемые меры безопасности

1. **Настройка брандмауэра**
```bash
# Разрешаем только необходимые порты
sudo ufw allow ssh
sudo ufw allow from 192.168.1.0/24 to any port 5432  # Только внутренняя сеть к PostgreSQL
sudo ufw allow from 192.168.1.3 to any port 3000     # Grafana только для Windows-станции
sudo ufw enable
```

2. **Регулярное обновление**
```bash
# Добавление автоматических обновлений безопасности
sudo apt install unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

3. **Резервное копирование**
```bash
# Ежедневное резервное копирование БД
0 2 * * * sudo -u whisper docker exec whisper-postgres pg_dump -U whisper_user whisper_db > /backups/whisper_db_$(date +\%Y\%m\%d).sql
```

---

## 📝 Дополнительные настройки

### 🔧 Настройка LM Studio на Windows

1. Скачайте и установите LM Studio
2. Загрузите модель: `mistral-7b-instruct-v0.3-q4_k_m.gguf`
3. Настройки сервера:
   - Port: 1234
   - API: OpenAI compatible
   - Context length: 8192
4. Запустите модель и убедитесь, что она доступна по http://localhost:1234

### 📊 Настройка дашбордов Grafana

1. Импортируйте дашборды из репозитория
2. Настройте алертинг на email/telegram
3. Настройте регулярные отчеты

---

## 🆘 Устранение неполадок

### 🔍 Распространенные проблемы и решения

**Проблема**: Контейнеры не запускаются
```bash
# Проверка логов
sudo -u whisper docker-compose logs [service_name]

# Проверка использования портов
sudo netstat -tulpn | grep :5432
```

**Проблема**: Нет соединения с Windows-станцией
```bash
# Проверка сетевой связности
ping 192.168.1.3 (ваш айпи)
telnet 192.168.1.3(ваш айпи) 1234

# Проверка общего доступа Samba
smbclient -L //192.168.1.6/(ваш айпи) -U whisper
```

**Проблема**: Высокая загрузка CPU/RAM
```bash
# Мониторинг ресурсов
sudo -u whisper docker stats

# Лимитирование ресурсов в docker-compose.yml
deploy:
  resources:
    limits:
      cpus: '2.0'
      memory: 4G
```

---

## 📞 Поддержка

### 🐛 Сообщение о проблемах

1. Проверьте существующие Issues на GitHub
2. Соберите информацию для отладки:
```bash
# Сбор логов и информации о системе
sudo -u whisper docker-compose logs --tail=100 > debug_logs.txt
sudo -u whisper docker info > docker_info.txt
uname -a > system_info.txt
```

3. Создайте новое Issue с собранной информацией

### 🔄 Обновление системы

```bash
# Обновление кода
sudo -u whisper git pull origin main

# Пересборка и перезапуск
sudo -u whisper docker-compose up -d --build

# Миграция базы данных (если необходимо)
sudo -u whisper docker exec whisper-postgres psql -U whisper_user -d whisper_db -f /migrations/migration_script.sql
```

---

## 📜 Лицензия

Проект распространяется под лицензией MIT. Подробности см. в файле `LICENSE`.

