# Sud_delo Case Search Discovery

**Дата:** 2026-08-12  
**Суд:** 2-й Западный окружной военный суд (2ЗОВС)  
**URL:** https://2zovs.msk.sudrf.ru/modules.php?name=sud_delo

## Статус

✅ **Поиск дел НЕ требует JavaScript** — форма использует стандартный HTTP GET.

Фактический browser request подтверждён через `curl` с browser-like заголовками.

## Найденная структура

### Форма поиска

**URL формы:**
```
/modules.php?name=sud_delo&srv_num=1&name_op=sf&delo_id=1540005&new=5
```

**Параметры формы (calform):**
- `name=sud_delo` (hidden)
- `srv_num=1` (hidden)
- `name_op=r` (hidden, results)
- `delo_id=1540006` (hidden, уголовные дела первой инстанции)
- `case_type=0` (hidden)
- `new=0` (hidden)

**Поля поиска:**
- `U1_DEFENDANT__NAMESS` - ФИО подсудимого
- `u1_case__CASE_NUMBERSS` - номер дела
- `u1_case__JUDICIAL_UIDSS` - UID дела
- `U1_EVENT__EVENT_DATEDD` - дата события (DD.MM.YYYY)
- `U1_DEFENDANT__LAW_ARTICLESS` - статья закона
- `U1_DEFENDANT__RESULT` - результат дела
- `U1_CASE__JUDGE` - судья
- `U1_CASE__BUILDING_ID` - здание суда
- `U1_CASE__COURT_STRUCT` - структура суда
- `U1_EVENT__EVENT_NAME` - название события
- `U1_PARTS__INN_STRSS` - ИНН
- `U1_PARTS__KPP_STRSS` - КПП
- `U1_PARTS__OGRN_STRSS` - ОГРН
- `U1_ORDER_INFO__ORDER_NUMSS` - номер заказа
- `U1_ORDER_INFO__EXTERNALKEYSS` - внешний ключ

### JavaScript функция show_search

Найдена в `/modules/sud_delo/JS/union2.js`:

```javascript
function show_search(n_n, c_t) {
    document.location.href = '/modules.php?name=sud_delo' + 
        (srv_num ? '&srv_num=' + srv_num : '') + 
        '&name_op=sf&delo_id=1540005' + 
        (c_t != 1 ? '&new=5' : '');
}
```

### Структура URL для case card

**Формат:**
```
/modules.php?name=sud_delo&srv_num=1&name_op=case&case_id={id}&case_uid={uid}&delo_id={delo_id}&new={new}
```

**Примеры из fixture:**
```
/modules.php?name=sud_delo&srv_num=1&name_op=case&case_id=3179069&case_uid=ec9975ee-3df8-4c00-8ab0-c6620d4ffd1b&delo_id=4&new=4
/modules.php?name=sud_delo&srv_num=1&name_op=case&case_id=3134178&case_uid=083e1a34-b2ca-481e-94a6-17270e4e0088&delo_id=4&new=4
```

**Параметры:**
- `name=sud_delo`
- `srv_num=1`
- `name_op=case`
- `case_id` - числовой ID дела
- `case_uid` - UUID дела
- `delo_id` - тип дела (4=уголовные, 1540006=уголовные первой инстанции, 41=апелляция)
- `new` - флаг (4 для уголовных дел)

### Типы дел (delo_id)

Найдены на сайте:
- `4` - уголовные дела
- `41` - апелляционные уголовные дела
- `1540006` - уголовные дела первой инстанции
- `1540005` - уголовные дела (поиск)
- `1500001`, `1502001`, `1513001` - другие типы
- `1610001`, `1610002` - гражданские дела
- `2450001`, `2550001`, `2800001` - административные дела

## Ограничения

### Проблема JavaScript

Форма поиска использует JavaScript для:
1. Отображения формы поиска (`show_search()`)
2. Отправки формы (вероятно через AJAX или динамическую загрузку)
3. Отображения результатов

**Прямой HTTP-запрос не возвращает результаты поиска.**

## Проверенные подходы

✅ **GET запрос со всеми hidden fields:**

```bash
curl -sL "https://2zovs.msk.sudrf.ru/modules.php" \
  -H "User-Agent: Mozilla/5.0 ..." \
  -G \
  --data-urlencode "name=sud_delo" \
  --data-urlencode "srv_num=1" \
  --data-urlencode "name_op=r" \
  --data-urlencode "delo_id=1540006" \
  --data-urlencode "case_type=0" \
  --data-urlencode "new=0" \
  --data-urlencode "delo_table=u1_case" \
  --data-urlencode "U1_DEFENDANT__LAW_ARTICLESS=205.1"
```

**Результат:** 200 OK, 25 результатов поиска.

**Ключевое отличие от предыдущей неудачной попытки:** в запросе должны присутствовать ВСЕ hidden fields: `case_type=0`, `new=0`, `delo_table=u1_case`.

Фикстура сохранена: `tests/fixtures/sudrf-live/2zovs/sud_delo/search-result-article-205-1.html`

## Итоговое заключение

JavaScript НЕ требуется. Поиск работает через обычный HTTP GET.
Причина предыдущего ложного вывода: были пропущены critical hidden fields.

## Реализация

Реализовано в `SudrfCaseSearchAdapter.search()` — использует полный набор параметров формы.

## Fixtures

Сохранены:
- `tests/fixtures/sudrf-live/2zovs/sud_delo/search-form.html` - главная страница sud_delo
- `tests/fixtures/sudrf-live/2zovs/sud_delo/search-form-full.html` - форма поиска (737 строк)
- `tests/fixtures/sudrf-live/2zovs/sud_delo/case-card-example.html` - пример case card
- `tests/fixtures/sudrf-live/2zovs/sud_delo/search-result-article-205-1.html` - результаты поиска по статье 205.1
