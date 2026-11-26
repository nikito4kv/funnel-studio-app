-- Этап 3, сведение веток: виды шагов в MAX.
-- id сообщения MAX – строка («mid.…»), в bigint-колонки его не положить.
-- Для опроса (кнопки вопроса, правка на месте – survey.edit) MAX-сообщение
-- хранится в отдельной текстовой колонке; у Telegram она пустая.
ALTER TABLE survey_prompts ADD COLUMN ext_message_id text;
ALTER TABLE survey_edits ADD COLUMN ext_message_id text;
ALTER TABLE survey_edits ALTER COLUMN message_id DROP NOT NULL;
