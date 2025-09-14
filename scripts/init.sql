-- Создание таблицы для хранения транскрипций
CREATE TABLE IF NOT EXISTS transcriptions (
    id SERIAL PRIMARY KEY,
    last_name VARCHAR(100) NOT NULL,
    first_name VARCHAR(100) NOT NULL,
    middle_name VARCHAR(100),
    call_date DATE NOT NULL,
    phone_number VARCHAR(20) NOT NULL,
    transcription_text TEXT NOT NULL,
    file_name VARCHAR(255) NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Создание таблицы для хранения результатов анализа транскрипций
CREATE TABLE IF NOT EXISTS transcription_analysis (
    id SERIAL PRIMARY KEY,
    transcription_id INTEGER REFERENCES transcriptions(id) ON DELETE CASCADE,
    analysis_result JSONB NOT NULL,
    analysis_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    model_used VARCHAR(100) NOT NULL,
    status VARCHAR(20) DEFAULT 'completed',
    error_message TEXT,
    processing_time INTERVAL
);

-- Индексы для оптимизации запросов
CREATE INDEX IF NOT EXISTS idx_transcriptions_name ON transcriptions (last_name, first_name);
CREATE INDEX IF NOT EXISTS idx_transcriptions_date ON transcriptions (call_date);
CREATE INDEX IF NOT EXISTS idx_transcriptions_phone ON transcriptions (phone_number);
CREATE INDEX IF NOT EXISTS idx_transcriptions_filename ON transcriptions (file_name);
CREATE INDEX IF NOT EXISTS idx_analysis_transcription_id ON transcription_analysis (transcription_id);
CREATE INDEX IF NOT EXISTS idx_analysis_date ON transcription_analysis (analysis_date);
CREATE INDEX IF NOT EXISTS idx_analysis_status ON transcription_analysis (status);

-- Комментарии к таблицам и полям для документации
COMMENT ON TABLE transcriptions IS 'Таблица для хранения транскрибированных звонков';
COMMENT ON COLUMN transcriptions.last_name IS 'Фамилия абонента';
COMMENT ON COLUMN transcriptions.first_name IS 'Имя абонента';
COMMENT ON COLUMN transcriptions.middle_name IS 'Отчество абонента';
COMMENT ON COLUMN transcriptions.call_date IS 'Дата звонка';
COMMENT ON COLUMN transcriptions.phone_number IS 'Номер телефона абонента';
COMMENT ON COLUMN transcriptions.transcription_text IS 'Текст транскрипции';
COMMENT ON COLUMN transcriptions.file_name IS 'Имя исходного файла';

COMMENT ON TABLE transcription_analysis IS 'Таблица для хранения результатов AI-анализа транскрипций';
COMMENT ON COLUMN transcription_analysis.transcription_id IS 'Ссылка на транскрипцию';
COMMENT ON COLUMN transcription_analysis.analysis_result IS 'Результат анализа в формате JSON';
COMMENT ON COLUMN transcription_analysis.analysis_date IS 'Дата и время проведения анализа';
COMMENT ON COLUMN transcription_analysis.model_used IS 'Использованная модель AI';
COMMENT ON COLUMN transcription_analysis.status IS 'Статус анализа (completed, failed, processing)';
COMMENT ON COLUMN transcription_analysis.error_message IS 'Сообщение об ошибке (если статус failed)';
COMMENT ON COLUMN transcription_analysis.processing_time IS 'Время обработки анализа';

-- Создание представления для удобного просмотра результатов анализа
CREATE OR REPLACE VIEW vw_transcription_with_analysis AS
SELECT 
    t.*,
    ta.analysis_result,
    ta.analysis_date,
    ta.model_used,
    ta.status as analysis_status,
    ta.error_message,
    ta.processing_time
FROM transcriptions t
LEFT JOIN transcription_analysis ta ON t.id = ta.transcription_id;

COMMENT ON VIEW vw_transcription_with_analysis IS 'Представление для просмотра транскрипций с результатами анализа';
