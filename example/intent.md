# Задача: TASK-001 — идемпотентность создания платежа

- **Контур:** полный — четыре файла, меняется контракт ответа (код `200` на повторе), гонки
- **Ветка витка:** `sdlc/TASK-001`

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

| id | Пункт | Как проверить (процедура + критерий) | Класс Кано |
|---|-------|--------------------------------------|------------|
| claim-1 | Повторный `POST /api/payments` с тем же `Idempotency-Key` и тем же телом возвращает код `200` | `PaymentIdempotencyIT.retryReturns200` — критерий: код ответа ровно `200` | performance |
| claim-2 | Повторный запрос возвращает `paymentId` первого платежа | `PaymentIdempotencyIT.retryReturnsSameId` — критерий: `paymentId` в ответе равен `paymentId` первого ответа | performance |
| claim-3 | Повторный запрос не создаёт новую строку в `payments` | `PaymentIdempotencyIT.retryCreatesNoRow` — критерий: `count(payments)` после повтора равен значению до повтора | performance |
| claim-4 | Запрос без заголовка `Idempotency-Key` возвращает код `201` | `PaymentIdempotencyIT.noKeyReturns201` — критерий: код ответа ровно `201` | performance |
| claim-5 | `[edge]` Запрос без заголовка не пишет ключей для этого endpoint'а | `PaymentIdempotencyIT.noKeyWritesNoKeyRow` — критерий: число строк таблицы ключей с `endpoint = 'POST /api/payments'` после двух запросов подряд равно значению до них | performance |
| claim-6 | `[edge]` Тот же `Idempotency-Key` с другим телом возвращает код `409` | `PaymentIdempotencyIT.sameKeyOtherBodyReturns409` — критерий: код ответа ровно `409` | performance |
| claim-7 | `[edge]` Тот же ключ с другим телом не создаёт платёж | `PaymentIdempotencyIT.sameKeyOtherBodyCreatesNoPayment` — критерий: `count(payments)` не изменился | performance |
| claim-8 | `[edge]` Два одновременных запроса с одним ключом создают ровно один платёж | `PaymentIdempotencyIT.concurrentCreatesOnePayment` — Given два потока с ключом `K` · When оба шлют запрос одновременно (реальный PostgreSQL) · Then `count(payments) = 1` | performance |
| claim-9 | `[edge]` Проигравший в гонке поток получает код `200` | тот же тест, второй ассерт — Given два потока с `K` · When первый закоммитился раньше · Then код ответа проигравшего ровно `200` | performance |
| claim-10 | `[edge]` Проигравший в гонке получает `paymentId` победителя | тот же тест, третий ассерт — критерий: `paymentId` проигравшего равен `paymentId` победителя | performance |
| claim-11 | `[edge]` При гонке `PaymentCreatedEvent` публикуется ровно один раз | `PaymentIdempotencyIT.concurrentPublishesEventOnce` — Given подписчик-счётчик · When два потока с `K` одновременно · Then перехвачено ровно одно событие с этим `paymentId` | performance |

## Инварианты

- Нет прямых SQL-запросов вне репозиторного слоя — проверяется `grep -rn "createQuery\|jdbcTemplate" --include=*.java src/main/java/com/acme/payments/{service,web}`
- Все публичные endpoint'ы проходят auth-middleware — проверяется `SecurityConfigTest.allEndpointsRequireAuth`
- Ответ при первом успешном создании платежа не меняется — проверяется существующим
  `PaymentControllerTest.createResponseContract` (не менять)

## Когда остановиться и спросить

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

- [x] **[блокирующий]** Что возвращать при повторе — `200` или `201`? → `200`, чтобы клиент мог
      отличить повтор от первого создания
- [x] **[блокирующий]** Нужен ли claim на однократную публикацию `PaymentCreatedEvent` при гонке?
      → да: дублированное событие спишет деньги дважды в биллинге. Добавлен claim-11
- [x] **[неблокирующий]** Срок жизни ключа? → 24 часа, как в `OrderService`
- [ ] **[неблокирующий]** Добавлять ли новый endpoint в конфигурацию `IdempotencyCleanupJob`?
      Без этого строки нового endpoint'а не удаляются никогда и таблица растёт. Отложено до
      следующего витка — растёт медленно; **вопрос остаётся открытым, это принятый риск, а не
      закрытый пункт**
