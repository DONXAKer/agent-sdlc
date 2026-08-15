# Задача: PAY-412 — идемпотентность создания платежа

> Заполненный пример по [`../templates/intent.template.md`](../templates/intent.template.md). Читать вместе с
> [`readiness.md`](readiness.md), [`exploration-report.md`](exploration-report.md),
> [`plan.md`](plan.md), [`chunk-1-journal.md`](chunk-1-journal.md),
> [`verification-report-1-attempt-1.md`](verification-report-1-attempt-1.md) и
> [`gates.md`](gates.md) — это один виток от цели до приёмки.

- **Контур:** полный — четыре файла, меняется контракт ответа (код `200` на повторе), гонки
- **Ветка витка:** `sdlc/PAY-412`

## Коротко

Поддержать заголовок `Idempotency-Key` в `POST /api/payments`, переиспользовав механизм
идемпотентности, который уже работает для заказов.

## Зачем

При повторной отправке формы оплаты (двойной клик, retry мобильного клиента при таймауте)
`POST /api/payments` создаёт второй платёж на ту же сумму. За последний месяц — 47 дублей,
все разбираются вручную через возврат.

После итерации повторный запрос с тем же ключом не создаёт второй платёж: клиент получает результат
первого. Ручные возвраты по этой причине прекращаются.

## Что делаем

- Поддержка заголовка `Idempotency-Key` в `POST /api/payments`
- Хранение ключа с привязкой к результату первого запроса
- Ответ на повторный запрос: результат первого вместо нового платежа

## Чего не делаем

- Не трогаем остальные эндпоинты `PaymentController` (`GET /api/payments/{id}`, `POST /{id}/refund`)
- Не меняем контракт ответа при первом (успешном) запросе — тело и код остаются как были
- Не добавляем идемпотентность в `OrderController` — там она уже есть, задача её только переиспользует
- Не трогаем схему таблицы `payments` и таблицу ключей: ключи уже хранит общий механизм
- Не покрываем идемпотентностью вызов из `PaymentRetryScheduler` — он идёт без ключа; это следующий виток

## Приёмочный лист

| id | Пункт | Как проверить (процедура + критерий) |
|---|-------|--------------------------------------|
| claim-1 | Повторный `POST /api/payments` с тем же `Idempotency-Key` и тем же телом возвращает код `200` | `PaymentIdempotencyIT.retryReturns200` — критерий: код ответа ровно `200` |
| claim-2 | Повторный запрос возвращает `paymentId` первого платежа | `PaymentIdempotencyIT.retryReturnsSameId` — критерий: `paymentId` в ответе равен `paymentId` первого ответа |
| claim-3 | Повторный запрос не создаёт новую строку в `payments` | `PaymentIdempotencyIT.retryCreatesNoRow` — критерий: `count(payments)` после повтора равен значению до повтора |
| claim-4 | Запрос без заголовка `Idempotency-Key` возвращает код `201` | `PaymentIdempotencyIT.noKeyReturns201` — критерий: код ответа ровно `201` |
| claim-5 | `[edge]` Запрос без заголовка не пишет ключей для этого endpoint'а | `PaymentIdempotencyIT.noKeyWritesNoKeyRow` — критерий: число строк таблицы ключей с `endpoint = 'POST /api/payments'` после двух запросов подряд равно значению до них |
| claim-6 | `[edge]` Тот же `Idempotency-Key` с другим телом возвращает код `409` | `PaymentIdempotencyIT.sameKeyOtherBodyReturns409` — критерий: код ответа ровно `409` |
| claim-7 | `[edge]` Тот же ключ с другим телом не создаёт платёж | `PaymentIdempotencyIT.sameKeyOtherBodyCreatesNoPayment` — критерий: `count(payments)` не изменился |
| claim-8 | `[edge]` Два одновременных запроса с одним ключом создают ровно один платёж | `PaymentIdempotencyIT.concurrentCreatesOnePayment` — Given два потока с ключом `K` · When оба шлют запрос одновременно (реальный PostgreSQL) · Then `count(payments) = 1` |
| claim-9 | `[edge]` Проигравший в гонке поток получает код `200` | тот же тест, второй ассерт — Given два потока с `K` · When первый закоммитился раньше · Then код ответа проигравшего ровно `200` |
| claim-10 | `[edge]` Проигравший в гонке получает `paymentId` победителя | тот же тест, третий ассерт — критерий: `paymentId` проигравшего равен `paymentId` победителя |
| claim-11 | `[edge]` При гонке `PaymentCreatedEvent` публикуется ровно один раз | `PaymentIdempotencyIT.concurrentPublishesEventOnce` — Given подписчик-счётчик · When два потока с `K` одновременно · Then перехвачено ровно одно событие с этим `paymentId` |

_Отдельной колонки Given-When-Then нет: для одношаговых пунктов она пересказывала бы процедуру
третий раз. Многошаговым сценариям гонки шаги вписаны прямо в «Как проверить»._

## Инварианты

- Нет прямых SQL-запросов вне репозиторного слоя — проверяется `grep -rn "createQuery\|jdbcTemplate" --include=*.java src/main/java/com/acme/payments/{service,web}`
- Все публичные endpoint'ы проходят auth-middleware — проверяется `SecurityConfigTest.allEndpointsRequireAuth`
- Ответ при первом успешном создании платежа не меняется — проверяется существующим
  `PaymentControllerTest.createResponseContract` (не менять)

## Когда остановиться и спросить

_Только проектные условия; общие точки остановки — норма `SDLC.md`, сюда не переписываются._

- Потребовалось тронуть `common/idempotency` или схему таблицы ключей — остановиться и спросить:
  механизм общий с заказами, и его правка задевает чужой домен

## Что придётся тронуть

_Заполнено агентом на разведке, проверено человеком._

- `src/main/java/com/acme/payments/web/PaymentController.java` — принять заголовок, выбрать код ответа
- `src/main/java/com/acme/payments/service/PaymentService.java` — обёртка вокруг существующего
  `IdempotencyGuard`
- `src/main/java/com/acme/payments/schedule/PaymentRetryScheduler.java` — второй вызывающий
  `create`, обновить под новую сигнатуру
- `src/test/java/com/acme/payments/PaymentIdempotencyIT.java` — новый интеграционный тест

## Открытые вопросы

_Отвеченные закрыты здесь чекбоксом; разбор — в [`clarification-report.md`](clarification-report.md)._

- [x] **[блокирующий]** Что возвращать при повторе — `200` или `201`? → `200`, чтобы клиент мог
      отличить повтор от первого создания
- [x] **[блокирующий]** Нужен ли claim на однократную публикацию `PaymentCreatedEvent` при гонке?
      → да: дублированное событие спишет деньги дважды в биллинге. Добавлен claim-11
- [x] **[неблокирующий]** Срок жизни ключа? → 24 часа, как в `OrderService`
- [ ] **[неблокирующий]** Добавлять ли новый endpoint в конфигурацию `IdempotencyCleanupJob`?
      Без этого строки нового endpoint'а не удаляются никогда и таблица растёт. Отложено до
      следующего витка — растёт медленно; **вопрос остаётся открытым, это принятый риск, а не
      закрытый пункт**
