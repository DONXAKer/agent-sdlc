# Журнал chunk'а 1: PAY-412 — идемпотентность создания платежа

> Заполненный пример по [`../templates/chunk-journal.template.md`](../templates/chunk-journal.template.md).
> Diff и вывод тестов попытки (`chunk-1-attempt-1-diff.patch`, `chunk-1-attempt-1-tests.txt`)
> в пример не включены — они порождаются кодовой базой витка.

- **План:** `plan.md`, одобрение от 2026-07-16
- **База:** `9c41f2a`
- **Бюджет попыток:** 3 — умолчание; строка «Бюджет итераций chunk'а» в наборе выключена

## Место правки

- Точки правки по итогам точечной разведки: `PaymentService:create`, `PaymentController:create`,
  `PaymentRetryScheduler:retryFailed`, `PaymentIdempotencyIT` (новый файл)
- Карта разведки: совпала — символы на месте, сигнатуры те, точка правки свободна
- **Подтвердил:** А. Грицай · 2026-07-16

## Попытки

| K | Дата | Что чинили (`retry_instruction` или «первая попытка») | Что изменилось против попытки K−1 | Итог (`verification-report-1-attempt-K.md`) |
|---|---|---|---|---|
| 1 | 2026-07-16 | первая попытка | н/п | retry — расхождение по `limitValidator.check`, интеграционные тесты не запускались (нет Docker) |
