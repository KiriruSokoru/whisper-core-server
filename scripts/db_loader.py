import os
import time
import logging
import psycopg2
from datetime import datetime

# Настройка логирования для отслеживания работы системы
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/app/db_loader.log'),  # Лог-файл для долгосрочного хранения
        logging.StreamHandler()  # Вывод в консоль для мгновенного мониторинга
    ]
)

class DBLoader:
    def __init__(self):
        self.data_dir = "/data"  # Директория, куда Whisper сохраняет транскрипции
        self.processed_dir = os.path.join(self.data_dir, "processed")
        os.makedirs(self.processed_dir, exist_ok=True)  # Создаем директорию для обработанных файлов

        # Параметры подключения к БД (берутся из переменных окружения)
        self.db_params = {
            'host': os.getenv('DB_HOST', 'postgres'),
            'database': os.getenv('DB_NAME', 'whisper_db'),
            'user': os.getenv('DB_USER', 'whisper_user'),
            'password': os.getenv('DB_PASSWORD'),
            'port': os.getenv('DB_PORT', '5432')
        }

        self.connection = None
        self.connect()  # Устанавливаем соединение с БД
        logging.info("DBLoader инициализирован успешно")

    def connect(self, max_retries=5, retry_delay=5):
        """Установка соединения с БД с повторными попытками"""
        for attempt in range(max_retries):
            try:
                self.connection = psycopg2.connect(**self.db_params)
                self.connection.autocommit = False  # Для контроля транзакций
                logging.info("Успешное подключение к PostgreSQL")
                return True
            except Exception as e:
                logging.error(f"Попытка {attempt + 1}/{max_retries}: {e}")
                time.sleep(retry_delay)  # Пауза между попытками

        logging.critical("Не удалось установить соединение с БД")
        return False

    def parse_filename(self, filename):
        """
        Парсинг имени файла в формате: Фамилия_Имя_Отчество_ГГГГ-ММ-ДД_телефон.txt
        Возвращает словарь с компонентами или None при ошибке
        """
        try:
            if not filename.endswith('.txt'):
                return None

            base_name = filename[:-4]  # Убираем расширение .txt
            parts = base_name.split('_')

            if len(parts) < 5:
                raise ValueError(f"Недостаточно компонентов в имени файла: {filename}")

            # Извлекаем телефон и дату (последние два элемента)
            phone_number = parts[-1]
            date_str = parts[-2]

            # Проверяем и преобразуем дату
            try:
                call_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                raise ValueError(f"Неверный формат даты: {date_str}")

            # Остальное - ФИО (первые элементы)
            last_name = parts[0]
            first_name = parts[1]
            middle_name = '_'.join(parts[2:-2]) if len(parts) > 5 else None

            return {
                'last_name': last_name,
                'first_name': first_name,
                'middle_name': middle_name,
                'call_date': call_date,
                'phone_number': phone_number
            }

        except Exception as e:
            logging.error(f"Ошибка парсинга {filename}: {e}")
            return None

    def check_duplicate(self, filename):
        """Проверяет существует ли файл уже в БД"""
        try:
            check_query = "SELECT id FROM transcriptions WHERE file_name = %s"
            with self.connection.cursor() as cursor:
                cursor.execute(check_query, (filename,))
                return cursor.fetchone() is not None
        except Exception as e:
            logging.error(f"Ошибка проверки дубликата {filename}: {e}")
            return False

    def process_file(self, file_path):
        """Обработка одного файла: чтение, парсинг, сохранение в БД"""
        try:
            filename = os.path.basename(file_path)
            logging.info(f"Начало обработки: {filename}")

            # 🔍 ПРОВЕРКА ДУБЛИКАТОВ - проверяем существует ли файл уже в БД
            if self.check_duplicate(filename):
                logging.warning(f"Файл уже существует в БД: {filename}")
                # Перемещаем в processed даже если это дубликат
                processed_path = os.path.join(self.processed_dir, filename)
                os.rename(file_path, processed_path)
                logging.info(f"Дубликат перемещен: {filename} -> {processed_path}")
                return False

            # Парсинг имени файла
            file_info = self.parse_filename(filename)
            if not file_info:
                return False

            # Чтение содержимого файла
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()

            if not content:
                logging.warning(f"Пустой файл: {filename}")
                return False

            # SQL-запрос для вставки данных
            # ON CONFLICT обеспечивает обновление существующих записей
            query = """
                INSERT INTO transcriptions
                (last_name, first_name, middle_name, call_date, phone_number, transcription_text, file_name)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (file_name) DO UPDATE SET
                    transcription_text = EXCLUDED.transcription_text,
                    updated_at = CURRENT_TIMESTAMP
            """

            with self.connection.cursor() as cursor:
                cursor.execute(query, (
                    file_info['last_name'],
                    file_info['first_name'],
                    file_info['middle_name'],
                    file_info['call_date'],
                    file_info['phone_number'],
                    content,
                    filename
                ))
                self.connection.commit()  # Фиксируем транзакцию

            # Перемещение обработанного файла в архивную директорию
            processed_path = os.path.join(self.processed_dir, filename)
            os.rename(file_path, processed_path)

            logging.info(f"Файл обработан: {filename} -> {processed_path}")
            return True

        except Exception as e:
            logging.error(f"Ошибка обработки {file_path}: {e}")
            if self.connection:
                self.connection.rollback()  # Откатываем транзакцию при ошибке
            return False

    def monitor_directory(self):
        """Основной цикл мониторинга директории на наличие новых файлов"""
        logging.info(f"Запуск мониторинга директории: {self.data_dir}")

        while True:
            try:
                # Поиск текстовых файлов
                files = []
                for item in os.listdir(self.data_dir):
                    item_path = os.path.join(self.data_dir, item)
                    if os.path.isfile(item_path) and item.endswith('.txt'):
                        files.append(item)

                if files:
                    logging.info(f"Найдено файлов для обработки: {len(files)}")

                # Обработка каждого файла
                for file in files:
                    file_path = os.path.join(self.data_dir, file)
                    self.process_file(file_path)

                time.sleep(10)  # Пауза между проверками

            except Exception as e:
                logging.error(f"Ошибка в цикле мониторинга: {e}")
                time.sleep(60)  # Увеличенная пауза при ошибках

if __name__ == "__main__":
    loader = DBLoader()
    loader.monitor_directory()
