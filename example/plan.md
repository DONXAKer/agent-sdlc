# План: PAY-412 — идемпотентность создания платежа

> Заполненный пример по [`../templates/plan.template.md`](../templates/plan.template.md).
> Одобрен человеком до начала реализации.

- **Задача:** [`intent.md`](intent.md) (PAY-412)
- **Вход:** exploration-report да · [clarification-report](clarification-report.md) да — разведка
  вскрыла развилки, человек ответил

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

| Путь | Что делаем | Почему добавлен сверх разведки |
|---|---|---|
| `src/main/java/com/acme/payments/service/PaymentService.java` | обёртка вокруг guard'а, снять `@Transactional` | был в Affected Areas |
| `src/main/java/com/acme/payments/web/PaymentController.java` | заголовок + выбор кода ответа по `isReplay()` | был в Affected Areas |
| `src/main/java/com/acme/payments/schedule/PaymentRetryScheduler.java` | обновить вызов под новую сигнатуру | был в Affected Areas |
| `src/test/java/com/acme/payments/PaymentIdempotencyIT.java` | 9 интеграционных тестов | был в Affected Areas |

**Из задачи исключено:** н/п

## Чем закрывается каждый пункт приёмки

| Пункт | Чем закрывается | В этом chunk'е |
|---|---|---|
| claim-1 | `retryReturns200` + ветка `isReplay()` в контроллере | да |
| claim-2 | `retryReturnsSameId` | да |
| claim-3 | `retryCreatesNoRow` | да |
| claim-4 | `noKeyReturns201` | да |
| claim-5 | `noKeyWritesNoKeyRow` + шаг 6 плана | да |
| claim-6 | `sameKeyOtherBodyReturns409`, маппинг уже существует | да |
| claim-7 | `sameKeyOtherBodyCreatesNoPayment` | да |
| claim-8 | `concurrentCreatesOnePayment`, уникальность ключа внутри guard'а | да |
| claim-9 | тот же тест, ассерт на код ответа проигравшего | да |
| claim-10 | тот же тест, ассерт на `paymentId` проигравшего | да |
| claim-11 | `concurrentPublishesEventOnce` — подписчик считает события в гонке | да |

## Необратимые шаги

- н/п — миграций и других необратимых шагов в этом chunk'е нет: таблица ключей уже существует.
