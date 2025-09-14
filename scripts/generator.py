from dotenv import load_dotenv
import os
import json
import psycopg2
import uuid
from datetime import datetime
import logging
import time
import signal
import sys
from prometheus_client import Counter, Gauge, Histogram, start_http_server

# ==================== ЗАГРУЗКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ====================
load_dotenv('/opt/analyzer/.env')

# ==================== НАСТРОЙКА ЛОГГИРОВАНИЯ ====================
logger = logging.getLogger('generator')
logger.setLevel(logging.INFO)

# Создаем директорию для логов если не существует
os.makedirs('/opt/analyzer/logs', exist_ok=True)

# Форматирование логов
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# File handler
file_handler = logging.FileHandler('/opt/analyzer/logs/generator.log')
file_handler.setFormatter(formatter)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)

# Добавляем handlers к логгеру
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# ==================== НАСТРОЙКА PROMETHEUS METRICS ====================
# Запускаем HTTP сервер для метрик на порту 8001
start_http_server(8001)

# Метрики Prometheus
TASKS_CREATED = Counter('generator_tasks_created', 'Total tasks created')
TASKS_FAILED = Counter('generator_tasks_failed', 'Total tasks failed')
DB_ERRORS = Counter('generator_db_errors', 'Total database errors')
PROCESSING_TIME = Histogram('generator_processing_time', 'Time spent processing')
ACTIVE_TASKS = Gauge('generator_active_tasks', 'Current active tasks being processed')
GENERATOR_ITERATIONS = Counter('generator_iterations', 'Total generator iterations')

# ==================== КОНФИГУРАЦИЯ БАЗЫ ДАННЫХ ====================
DB_CONFIG = {
    'host': os.getenv('DB_HOST', '192.168.1.6'),
    'database': 'whisper_db',
    'user': 'whisper_user',
    'password': os.getenv('DB_PASSWORD'),
    'port': 5432
}

# Флаг для graceful shutdown
shutdown_flag = False

def signal_handler(sig, frame):
    """Обработчик сигналов для graceful shutdown"""
    global shutdown_flag
    logger.info("Received shutdown signal")
    shutdown_flag = True

def safe_file_write(filepath, content):
    """Безопасная запись файла с проверкой существования"""
    if os.path.exists(filepath):
        logger.warning(f"File already exists: {filepath}")
        return False
    try:
        with open(filepath, 'x', encoding='utf-8') as f:
            json.dump(content, f, ensure_ascii=False, indent=2)
        return True
    except FileExistsError:
        logger.warning(f"File created concurrently: {filepath}")
        return False
    except Exception as e:
        logger.error(f"Error writing file {filepath}: {e}")
        return False

def process_tasks():
    """Основная функция обработки задач"""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()

        # Получаем количество задач для обработки - ИСПРАВЛЕННЫЙ ЗАПРОС
        cursor.execute("""
            SELECT COUNT(*)
            FROM transcriptions
            WHERE processed = FALSE
            AND NOT EXISTS (
                SELECT 1 FROM transcription_analysis ta
                WHERE ta.transcription_id = transcriptions.id
            )
        """)
        pending_count = cursor.fetchone()[0]

        ACTIVE_TASKS.set(pending_count)
        logger.info(f"Found {pending_count} pending tasks for processing")

        # Если нет задач, просто возвращаемся
        if pending_count == 0:
            logger.info("No tasks to process, waiting for next iteration")
            cursor.close()
            conn.close()
            return

        # Получаем задачи для обработки - ИСПРАВЛЕННЫЙ ЗАПРОС
        cursor.execute("""
            SELECT id, transcription_text
            FROM transcriptions
            WHERE processed = FALSE
            AND NOT EXISTS (
                SELECT 1 FROM transcription_analysis ta
                WHERE ta.transcription_id = transcriptions.id
            )
            LIMIT 50
        """)

        processed_count = 0
        failed_count = 0

        for call_id, text in cursor.fetchall():
            # Проверяем флаг shutdown перед обработкой каждой задачи
            if shutdown_flag:
                logger.info("Shutdown requested, stopping task processing")
                break

            try:
                # Генерируем уникальный идентификатор задачи
                task_uuid = str(uuid.uuid4())
                task = {
                    "id": call_id,
                    "text": text,
                    "task_id": task_uuid,
                    "created_at": datetime.now().isoformat()
                }

                # Используем уникальное имя файла
                filename = f"task_{call_id}_{task_uuid}.json"
                filepath = f"/opt/shared/pending/{filename}"

                # Создаем директорию если не существует
                os.makedirs(os.path.dirname(filepath), exist_ok=True)

                if safe_file_write(filepath, task):
                    logger.info(f"Created task file: {filename}")
                    # Обновляем запись в БД - устанавливаем processed = TRUE
                    cursor.execute("UPDATE transcriptions SET processed = TRUE WHERE id = %s", (call_id,))
                    conn.commit()
                    TASKS_CREATED.inc()
                    processed_count += 1
                else:
                    logger.warning(f"Skipped duplicate task for call_id: {call_id}")
                    failed_count += 1
                    TASKS_FAILED.inc()

            except Exception as e:
                logger.error(f"Error processing task {call_id}: {e}")
                failed_count += 1
                TASKS_FAILED.inc()
                # Откатываем транзакцию для этой задачи, но продолжаем обработку
                conn.rollback()

        logger.info(f"Processing completed. Successful: {processed_count}, Failed: {failed_count}")
        cursor.close()
        conn.close()

    except psycopg2.Error as e:
        logger.error(f"Database connection error: {e}")
        DB_ERRORS.inc()
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        DB_ERRORS.inc()

def main():
    logger.info("Starting generator process in continuous mode")

    # Регистрируем обработчики сигналов для graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Бесконечный цикл обработки
    while not shutdown_flag:
        try:
            with PROCESSING_TIME.time():
                GENERATOR_ITERATIONS.inc()
                process_tasks()

            # Пауза между итерациями (30 секунд)
            for _ in range(30):
                if shutdown_flag:
                    break
                time.sleep(1)

        except Exception as e:
            logger.error(f"Unexpected error in main loop: {e}")
            # Пауза при ошибке (60 секунд)
            for _ in range(60):
                if shutdown_flag:
                    break
                time.sleep(1)

    logger.info("Generator process finished gracefully")

if __name__ == "__main__":
    main()
