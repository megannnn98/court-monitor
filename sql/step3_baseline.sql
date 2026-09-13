-- Выбираем поля, которые хотим получить в результате.
SELECT
    article.id AS article_id,           -- ID статьи.
    article.title,                      -- Заголовок статьи.
    document.canonical_url AS url,      -- URL исходной публикации.
    article.text                        -- Полный текст статьи.

-- Начинаем поиск с таблицы статей.
FROM parsed_articles AS article

-- Присоединяем исходный документ, из которого получена статья.
JOIN source_documents AS document
    -- document.id и article.document_id содержат ID одного документа.
    ON document.id = article.document_id

-- Присоединяем источник, например сайт ОВД-Инфо.
JOIN sources AS source
    -- source.id и document.source_id содержат ID одного источника.
    ON source.id = document.source_id

-- Оставляем только статьи, содержащие указанную подстроку.
-- ILIKE игнорирует регистр, но не понимает формы русского слова.
WHERE article.text ILIKE '%реабилитации нацизма%'

  -- Из найденных статей оставляем только материалы ОВД-Инфо.
  AND source.base_url = 'https://ovd.info';
