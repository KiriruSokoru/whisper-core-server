import os
import time
import json
import re
import psutil
import requests
import psycopg2
from psycopg2 import sql
from datetime import datetime

# Загрузка переменных окружения
from dotenv import load_dotenv
load_dotenv()

# Конфигурация из переменных окружения. IP адрес нужно указать ваш, это будет адрес вашего сервера. Порты LM
# смотрите так же под ваш проект, порт 8080 в моем случае выбран для отсутствия конфликта, так как ранее в проекте
# присутствовал Суперсет
UNC_PATH = os.getenv('SMB_SHARE', r'\\192.168.1.6\transcription-queue') 
LM_BASE_URL = os.getenv('LM_STUDIO_URL', 'http://localhost:8080')

# Конфигурация базы данных
db_config = {
    'host': os.getenv('DB_HOST', '192.168.1.6'),
    'port': int(os.getenv('DB_PORT', '5432')),
    'database': os.getenv('DB_NAME', 'whisper_db'),
    'user': os.getenv('DB_USER', 'whisper_user'),
    'password': os.getenv('DB_PASSWORD')
}

# Название модели Mistral 7B - эта модель влезает в мою память, плюс хороша в тексте, как вариант можно попробовать
# Phi от Майкрософт, о ней тоже хорошие отзывы именно про работу с текстом, коим транскрипция и явялется
LM_MODEL_NAME = "Mistral-7B-Instruct-v0.3-Q4_K_M.gguf"

def contains_russian(text):
    """Проверяет содержит ли текст русские буквы"""
    russian_letters = set('абвгдеёжзийклмнопрстуфхцчшщъыьэюя')
    text_lower = text.lower()
    return any(char in russian_letters for char in text_lower)

def check_lm_studio():
    """Проверка доступности LM Studio"""
    try:
        response = requests.get(f"{LM_BASE_URL}/v1/models", timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"LM Studio недоступен: {e}")
        return False

def clean_lm_response(response_text):
    """Очистка ответа LM Studio от markdown разметки"""
    if not response_text:
        return None
    
    # Убираем обратные кавычки и markdown
    text = response_text.strip()
    
    # Удаляем ```json и ``` в начале и конце
    if text.startswith('```json'):
        text = text[7:].strip()
    elif text.startswith('```'):
        text = text[3:].strip()
    
    if text.endswith('```'):
        text = text[:-3].strip()
    
    # Убираем любые другие остатки разметки
    text = re.sub(r'^```.*?```', '', text, flags=re.DOTALL)
    text = text.strip()
    
    # Пытаемся найти JSON в тексте
    json_match = re.search(r'\{[\s\S]*\}', text)
    if json_match:
        return json_match.group()
    
    return text

def analyze_with_lm_studio(text):
    """Анализ текста с помощью LM Studio и Mistral 7B"""
    try:
        api_url = f"{LM_BASE_URL}/v1/chat/completions"
        
        # РУССКОЯЗЫЧНЫЙ ПРОМПТ С ГАРАНТИЕЙ РУССКОГО ОТВЕТА
        russian_prompt = """[INST] Ты - русскоязычный AI ассистент для анализа телефонных разговоров. 

Проанализируй транскрипцию и верни ответ в формате JSON строго на русском языке.

ЖЕСТКИЕ ТРЕБОВАНИЯ:
1. ВСЕ текстовые поля должны быть на РУССКОМ языке
2. Используй только кириллицу
3. Никакого английского в ответе
4. Только JSON без дополнительного текста
5. Не используй markdown разметку

Структура JSON:
{
  "sentiment": "позитивный/негативный/нейтральный",
  "key_topics": ["тема обсуждения 1", "тема обсуждения 2"],
  "action_items": ["необходимое действие 1", "необходимое действие 2"],
  "summary": "полное краткое содержание разговора на русском языке",
  "call_quality": "хороший/средний/плохой"
}

Верни ответ строго на русском языке! [/INST]

Транскрипция для анализа:"""
        
        payload = {
            "model": LM_MODEL_NAME,
            "messages": [
                {
                    "role": "user", 
                    "content": f"{russian_prompt}\n\n{text}\n\nВерни JSON ответ строго на русском языке:"
                }
            ],
            "temperature": 0.1,
            "max_tokens": 4000,
            "top_p": 0.9,
            "stream": False
        }
        
        print(f"📨 Отправка запроса к LM Studio с моделью: {LM_MODEL_NAME}")
        response = requests.post(api_url, json=payload, timeout=600)  # 10 минут таймаут
        
        if response.status_code == 200:
            result = response.json()
            analysis_result = result['choices'][0]['message']['content']
            
            # Очищаем ответ от markdown
            cleaned_response = clean_lm_response(analysis_result)
            
            if not cleaned_response:
                print(f"❌ Пустой ответ от LM Studio")
                return None
            
            # Валидация JSON
            try:
                json_data = json.loads(cleaned_response)
                
                # Проверка русского языка
                if not contains_russian(cleaned_response):
                    print("⚠️ Предупреждение: ответ может содержать английский текст")
                else:
                    print(f"✅ Mistral 7B вернул русскоязычный JSON")
                
                return json.dumps(json_data, ensure_ascii=False, indent=2)
            except json.JSONDecodeError as e:
                print(f"❌ Ответ не является валидным JSON: {e}")
                print(f"Raw response: {analysis_result[:200]}...")
                return None
                
        else:
            print(f"❌ Ошибка HTTP {response.status_code}: {response.text}")
            return None
            
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return None

def split_long_text(text, max_tokens=3000):
    """Разделение длинного текста на части для Mistral 7B"""
    words = text.split()
    chunks = []
    current_chunk = []
    current_length = 0
    
    for word in words:
        # Примерная оценка токенов (1 слово ≈ 1.3 токена)
        if current_length + len(word) * 1.3 > max_tokens:
            chunks.append(' '.join(current_chunk))
            current_chunk = []
            current_length = 0
        
        current_chunk.append(word)
        current_length += len(word) * 1.3
    
    if current_chunk:
        chunks.append(' '.join(current_chunk))
    
    return chunks

def analyze_long_text(text):
    """Анализ длинного текста по частям"""
    chunks = split_long_text(text, max_tokens=3000)
    all_results = []
    
    for i, chunk in enumerate(chunks):
        print(f"📄 Анализ части {i+1}/{len(chunks)} ({(i+1)/len(chunks)*100:.1f}%)...")
        result = analyze_with_lm_studio(chunk)
        if result:
            try:
                result_data = json.loads(result)
                all_results.append(result_data)
            except:
                all_results.append({"chunk": i+1, "error": "invalid_json"})
        time.sleep(2)  # Пауза между запросами
    
    # Объединяем результаты анализа
    if all_results:
        return json.dumps({
            "combined_analysis": all_results,
            "total_chunks": len(chunks),
            "combined_summary": "Анализ выполнен по частям из-за большого объема текста"
        }, ensure_ascii=False)
    
    return None

def get_db_connection():
    """Установка соединения с базой данных"""
    try:
        conn = psycopg2.connect(**db_config)
        return conn
    except Exception as e:
        print(f"❌ Ошибка подключения к БД: {e}")
        return None

def save_analysis_to_db(transcription_id, analysis_result):
    """Сохранение результата анализа в базу данных"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        # Проверяем валидность JSON перед сохранением
        analysis_data = json.loads(analysis_result)
        
        with conn.cursor() as cur:
            # ОБНОВЛЕННЫЙ ЗАПРОС С model_used
            cur.execute("""
                INSERT INTO transcription_analysis 
                (transcription_id, analysis_result, analysis_date, model_used) 
                VALUES (%s, %s, %s, %s)
            """, (transcription_id, analysis_result, datetime.now(), LM_MODEL_NAME))
            conn.commit()
        
        print(f"💾 Анализ сохранен в БД для transcription_id: {transcription_id}")
        return True
        
    except json.JSONDecodeError as e:
        print(f"❌ Ошибка: analysis_result не является валидным JSON: {e}")
        return False
    except Exception as e:
        print(f"❌ Ошибка сохранения в БД: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

def process_task(task_path, task_id, transcription_id):
    """Обработка отдельной задачи"""
    try:
        with open(task_path, 'r', encoding='utf-8') as f:
            task_data = json.load(f)
        
        text = task_data.get('text', '')
        if not text or len(text.strip()) < 10:
            print(f"⚠️ Пустой или слишком короткий текст в задаче {task_id}")
            return False
        
        text_length = len(text)
        print(f"🔍 Анализируем задачу {task_id}, длина текста: {text_length} символов")
        
        # Проверяем доступность LM Studio
        if not check_lm_studio():
            print("❌ LM Studio недоступен, пропускаем задачу")
            return False
        
        print("✅ LM Studio доступен, начинаем анализ...")
        
        # Выбираем метод анализа в зависимости от длины текста
        if text_length > 8000:  # Длинные тексты анализируем по частям
            print("📖 Текст длинный, анализируем по частям...")
            analysis_result = analyze_long_text(text)
        else:  # Короткие тексты анализируем целиком
            analysis_result = analyze_with_lm_studio(text)
        
        if not analysis_result:
            print(f"❌ Не удалось проанализировать задачу {task_id}")
            return False
        
        # Сохранение в базу данных
        if save_analysis_to_db(transcription_id, analysis_result):
            print(f"✅ Задача {task_id} успешно обработана и сохранена в БД")
            return True
        else:
            print(f"❌ Ошибка сохранения задачи {task_id} в БД")
            return False
        
    except Exception as e:
        print(f"❌ Ошибка обработки задачи {task_id}: {e}")
        return False

def ensure_directories_exist():
    """Создание необходимых директорий если они отсутствуют"""
    directories = [
        os.path.join(UNC_PATH, 'pending'),
        os.path.join(UNC_PATH, 'processing'), 
        os.path.join(UNC_PATH, 'completed'),
        os.path.join(UNC_PATH, 'failed')
    ]
    
    for directory in directories:
        if not os.path.exists(directory):
            try:
                os.makedirs(directory)
                print(f"📁 Создана директория: {directory}")
            except Exception as e:
                print(f"❌ Ошибка создания директории {directory}: {e}")
                return False
    return True

def main():
    print("=" * 70)
    print("🚀 Запуск Transcription Watcher с Mistral 7B")
    print("=" * 70)
    print(f"📡 LM Studio URL: {LM_BASE_URL}")
    print(f"🧠 Модель: {LM_MODEL_NAME}")
    print(f"📂 SMB Share: {UNC_PATH}")
    print(f"🗄️ DB Host: {db_config['host']}")
    print("=" * 70)
    
    # Проверка подключений
    print("🔍 Проверка подключений...")
    if not check_lm_studio():
        print("⚠️ ВНИМАНИЕ: LM Studio недоступен!")
        print("Убедитесь, что LM Studio запущен на http://localhost:8080")
        print("И что модель Mistral 7B загружена")
    else:
        print("✅ LM Studio доступен")
    
    if not ensure_directories_exist():
        print("❌ Ошибка создания директорий")
        return
    
    print("👂 Watcher запущен, ожидание задач...")
    print("Нажмите Ctrl+C для остановки")
    print("=" * 70)
    
    while True:
        try:
            pending_dir = os.path.join(UNC_PATH, 'pending')
            processing_dir = os.path.join(UNC_PATH, 'processing')
            completed_dir = os.path.join(UNC_PATH, 'completed')
            failed_dir = os.path.join(UNC_PATH, 'failed')
            
            # Обработка задач в директории pending
            task_files = [f for f in os.listdir(pending_dir) if f.endswith('.json')]
            
            if task_files:
                print(f"📋 Найдено задач: {len(task_files)}")
            
            for task_file in task_files:
                task_path = os.path.join(pending_dir, task_file)
                processing_path = os.path.join(processing_dir, task_file)
                
                try:
                    # Перемещаем в processing
                    os.rename(task_path, processing_path)
                    
                    with open(processing_path, 'r', encoding='utf-8') as f:
                        task_data = json.load(f)
                    
                    task_id = task_data.get('task_id', 'unknown')
                    transcription_id = task_data.get('transcription_id')
                    
                    print(f"🔄 Обработка задачи: {task_id}")
                    
                    success = process_task(processing_path, task_id, transcription_id)
                    
                    # Перемещаем в completed или failed
                    if success:
                        completed_path = os.path.join(completed_dir, task_file)
                        os.rename(processing_path, completed_path)
                        print(f"✅ Задача {task_id} завершена успешно")
                    else:
                        failed_path = os.path.join(failed_dir, task_file)
                        os.rename(processing_path, failed_path)
                        print(f"❌ Задача {task_id} перемещена в failed")
                        
                except Exception as e:
                    print(f"⚠️ Критическая ошибка с задачей {task_file}: {e}")
                    # Пытаемся переместить в failed в случае ошибки
                    try:
                        failed_path = os.path.join(failed_dir, task_file)
                        if os.path.exists(processing_path):
                            os.rename(processing_path, failed_path)
                        elif os.path.exists(task_path):
                            os.rename(task_path, failed_path)
                    except:
                        pass
            
            # Пауза перед следующей проверкой
            time.sleep(15)
            
        except KeyboardInterrupt:
            print("\n🛑 Watcher остановлен пользователем")
            break
        except Exception as e:
            print(f"💥 Критическая ошибка в основном цикле: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()