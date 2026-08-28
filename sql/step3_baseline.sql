-- Выбираем поля, которые хотим получить в результате.
SELECT
    chunk.id AS chunk_id,                -- ID найденного фрагмента текста.
    article.id AS article_id,            -- ID статьи, которой принадлежит фрагмент.
    article.title,                       -- Заголовок статьи.
    document.canonical_url AS url,       -- URL исходной публикации.
    chunk.text                           -- Текст найденного фрагмента.

-- Начинаем поиск с таблицы фрагментов.
FROM article_chunks AS chunk

-- Присоединяем статью, которой принадлежит каждый фрагмент.
JOIN parsed_articles AS article
    -- article.id и chunk.parsed_article_id содержат ID одной статьи.
    ON article.id = chunk.parsed_article_id

-- Присоединяем исходный документ, из которого получена статья.
JOIN source_documents AS document
    -- document.id и article.document_id содержат ID одного документа.
    ON document.id = article.document_id

-- Присоединяем источник, например сайт ОВД-Инфо.
JOIN sources AS source
    -- source.id и document.source_id содержат ID одного источника.
    ON source.id = document.source_id

-- Оставляем только фрагменты, содержащие указанную подстроку.
-- ILIKE игнорирует регистр, но не понимает формы русского слова.
WHERE chunk.text ILIKE '%реабилитации нацизма%'

  -- Из найденных фрагментов оставляем только материалы ОВД-Инфо.
  AND source.base_url = 'https://ovd.info';
