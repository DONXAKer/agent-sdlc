# План: PAY-412 — идемпотентность создания платежа

> Заполненный пример по [`../templates/plan.template.md`](../templates/plan.template.md).
> Одобрен человеком до начала реализации — и одобрение записано полем, а не оставлено в чате.

- **Задача:** [`intent.md`](intent.md) (PAY-412)
- **Вход:** exploration-report да · [clarification-report](clarification-report.md) да — разведка
  вскрыла развилки, человек ответил
- **База:** `9c41f2a` — HEAD `main` на момент одобрения
- **Одобрение:** А. Грицай · 2026-07-16

## Подход

Переиспользуем `IdempotencyGuard` из `common/idempotency` — тот же механизм, что работает для
заказов, вместе с его таблицей ключей. Своей логики хранения, хеширования и разбора гонки не пишем;
границей транзакции владеет guard, поэтому `PaymentService.create` своей `@Transactional`
не получает. Миграция не нужна: новый endpoint отличается только значением колонки `endpoint`.

## Шаги

1. **Использовать** `IdempotencyGuard.execute` в `PaymentService.create` — обернуть существующее тело
   метода в `Supplier`, чтобы публикация `PaymentCreatedEvent` не повторялась при replay.
2. **Использовать** `RequestHasher` для `requestHash` — своего хеширования не писать.
3. **Использовать** `IdempotentResult.isReplay()` в `PaymentController.create`: `200` при replay,
   `201` иначе (claims 1, 4, 9).
4. **Использовать** существующий маппинг `IdempotencyConflictException` → `409`
   в `GlobalExceptionHandler` — своего обработчика не добавлять (claims 6, 7).
5. **Использовать** `AbstractIntegrationTest` как базовый класс `PaymentIdempotencyIT`:
   Testcontainers с реальным PostgreSQL, без него claims 8–10 бессмысленны.
6. Guard вызывать с пустым ключом, когда заголовка нет: тогда он выполняет `Supplier` внутри своей
   транзакции, но ключа не пишет — claim 5 и атомарность прямого пути.
7. Обновить вызов в `PaymentRetryScheduler` под новую сигнатуру — ключ не передаётся, поведение
   не меняется.
8. `PaymentIdempotencyIT`: 9 тестов на 11 claim'ов (claims 9 и 10 — ассерты внутри теста claim'а 8;
   claim 11 — отдельный тест с подписчиком-счётчиком событий).

## files_to_touch

| Путь | Что делаем |
|---|---|
| `src/main/java/com/acme/payments/service/PaymentService.java` | обёртка вокруг guard'а, снять `@Transactional` |
| `src/main/java/com/acme/payments/web/PaymentController.java` | заголовок + выбор кода ответа по `isReplay()` |
| `src/main/java/com/acme/payments/schedule/PaymentRetryScheduler.java` | обновить вызов под новую сигнатуру |
| `src/test/java/com/acme/payments/PaymentIdempotencyIT.java` | 9 интеграционных тестов |

- **Добавлено сверх разведки:** нет — список совпал с «Что придётся тронуть»
- **Из задачи исключено:** нет

## Чем закрывается каждый пункт приёмки

| Пункт | Чем закрывается |
|---|---|
| claim-1 | `retryReturns200` + ветка `isReplay()` в контроллере |
| claim-2 | `retryReturnsSameId` |
| claim-3 | `retryCreatesNoRow` |
| claim-4 | `noKeyReturns201` |
| claim-5 | `noKeyWritesNoKeyRow` + шаг 6 плана |
| claim-6 | `sameKeyOtherBodyReturns409`, маппинг уже существует |
| claim-7 | `sameKeyOtherBodyCreatesNoPayment` |
| claim-8 | `concurrentCreatesOnePayment`, уникальность ключа внутри guard'а |
| claim-9 | тот же тест, ассерт на код ответа проигравшего |
| claim-10 | тот же тест, ассерт на `paymentId` проигравшего |
| claim-11 | `concurrentPublishesEventOnce` — подписчик считает события в гонке |

- **Уходит следующим chunk'ам:** нет — этот chunk закрывает все пункты

## Необратимые шаги

- н/п — миграций и других необратимых шагов в этом chunk'е нет: таблица ключей уже существует.
