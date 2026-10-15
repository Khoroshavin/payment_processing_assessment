# Payment Processing Service

Асинхронный микросервис процессинга платежей: принимает запросы на оплату,
обрабатывает их через эмулируемый платёжный шлюз и уведомляет клиента о
результате через webhook.

Стек: **FastAPI + Pydantic v2, SQLAlchemy 2.0 (async), PostgreSQL, RabbitMQ (FastStream),
Alembic, Docker Compose, uv**.

## Архитектура

```mermaid
flowchart LR
    Client[Client] -->|POST /api/v1/payments| API[FastAPI]
    API -->|1. INSERT payment + outbox<br/>в одной транзакции| DB[(PostgreSQL)]
    API -->|202 Accepted| Client

    Relay[Outbox Relay] -->|2. SELECT ... FOR UPDATE SKIP LOCKED| DB
    Relay -->|3. publish c publisher confirms| EX[exchange: payments]
    EX --> Q[queue: payments.new]

    Q -->|4. consume| CS[Consumer]
    CS -->|эмуляция шлюза 2-5с, 90/10| CS
    CS -->|обновление статуса| DB
    CS -->|webhook payment_id + status| WH[Client Webhook]

    CS -->|ошибка webhook, попытка 1| R2[payments.new.retry.2s<br/>TTL 2s, возврат в payments.new]
    CS -->|ошибка webhook, попытка 2| R4[payments.new.retry.4s<br/>TTL 4s, возврат в payments.new]
    CS -->|3 неудачные попытки| DLQ[payments.new.dlq]
```

Сервисы в Compose: `postgres`, `rabbitmq`, `migrate` (одноразовый), `api`,
`consumer`, `relay` — один Docker-образ, три entrypoint'а
(`app.entrypoints.api / consumer / relay`).

### Гарантии доставки

- **Outbox pattern**: событие `payments.new` пишется в таблицу `outbox` в той же
  транзакции, что и платёж. Relay публикует его в RabbitMQ и помечает строку
  `published` только после подтверждения брокера (publisher confirms). Публикация
  ретраится с экспоненциальным backoff **бесконечно** — события не теряются ни при
  падении API между коммитом и публикацией, ни при длительной недоступности
  брокера. `RELAY_MAX_ATTEMPTS` лишь эскалирует уровень лога для алертинга.
  Единственное исключение — «отравленная» строка, чей `payload` невозможно
  разобрать в событие: retry ей не поможет, поэтому relay помечает её `dead`
  (ручное вмешательство оператора), сохраняя ошибку в `last_error`.
- **Idempotency**: `UNIQUE(idempotency_key)` + `SELECT ... FOR UPDATE`. Повторный
  POST с тем же ключом и тем же телом возвращает `200` с тем же ответом и
  заголовком `Idempotency-Replayed: true` (без повторной проверки webhook_url);
  тот же ключ с другим телом — `409` `idempotency_conflict` (тело хэшируется
  канонически — с рекурсивной сортировкой ключей JSON — и хранится в
  `payments.request_hash`). Валидация тела на уровне транспорта (422) выполняется
  до идемпотентной сверки: повторный запрос с некорректным телом вернёт 422,
  а не 200/409. Потребитель идемпотентен по статусу
  платежа, а `SELECT ... FOR UPDATE` при обработке сериализует конкурентные
  дубли сообщений: шлюз вызывается ровно один раз, повторные доставки только
  досылают webhook.
- **Retry + DLQ**: неудачная обработка (в первую очередь — сбой webhook)
  перекладывается в TTL-очереди с экспоненциальной задержкой (2с → 4с), счётчик
  попыток хранится в заголовке `x-retry-count` (broker-side, переживает рестарты).
  После 3 попыток сообщение уходит в dead-letter очередь `payments.new.dlq`.
  Сообщения, которые не удаётся разобрать (невалидный JSON или несоответствие
  схеме события), публикуются в DLQ сразу, сырое тело сохраняется; исключения,
  прорвавшиеся из обработчика, отклоняются (reject) и попадают в DLQ через
  нативный dead-letter-роутинг рабочей очереди (`x-dead-letter-exchange`) —
  бесконечный requeue исключён.
- Все публикации (relay и перекладки в retry/DLQ) выполняются с publisher
  confirms; подтверждение исходного сообщения — только после подтверждённой
  публикации. Доставка at-least-once на всём пути, дубли гасятся идемпотентностью.

## Быстрый старт (Docker)

```bash
cp .env.example .env           # при необходимости поменяйте API_KEY
task up                       # сборка + запуск всех сервисов
```

## Taskfile
[Documentation](https://taskfile.dev)

```shell
task -l
```

Команды: `task up`, `task down`, `task logs`, `task test`, `task smoke`,
`task migrate`.

Сервисы:

| Сервис | Адрес |
|---|---|
| API (Swagger UI) | http://localhost:8000/docs |
| RabbitMQ Management UI | http://localhost:15672 (guest/guest) |
| PostgreSQL | localhost:5432 |

Миграции применяются одноразовым сервисом `migrate` (`alembic upgrade head`)
перед стартом `api`, `consumer` и `relay` (`service_completed_successfully`),
поэтому несколько реплик API не гоняются за схемой БД. Для запуска миграций
вручную есть `task migrate`.

Хост-порты задаются в `.env` (`API_PORT`, `POSTGRES_PORT`, `RABBITMQ_PORT`,
`RABBITMQ_UI_PORT`) и пробрасываются через `--env-file`; порты PostgreSQL,
RabbitMQ и Management UI по умолчанию слушают только `127.0.0.1`. Адреса и
порты внутри сети Compose фиксированы и не зависят от хост-маппингов.
Остановить стек:
`docker compose --env-file .env -f ops/docker-compose.yaml down`.

## Структура проекта

```
src/app/
├── core/               # cross-cutting
│   ├── configs/        #   settings (группы по модулям, DSN как property), logging_{prod,dev}.json, ApiConfig
│   ├── exceptions/     #   BaseAppException + общие ошибки (auth, availability)
│   ├── logging.py      #   JsonFormatter, RequestIdFilter, setup_logging из JSON-конфигов
│   ├── orm/            #   Base (naming convention), session factory
│   └── migrations/     #   Alembic (env.py + versions/)
├── middlewares/        # app-wide: request-id, единые обработчики ошибок (manager.py — точка подключения)
├── domains/
│   ├── auth/           # аутентификация: модель ключа, репозиторий (static, замена на БД), сервис валидации
│   └── payments/       # домен платежей: enums, exceptions, dto, models, repos, services, routers
├── infra/              # инфраструктура: gateway (эмулятор PSP), webhook client, messaging (RabbitMQ)
├── modules/
│   └── security/       # переиспользуемые модули: SSRF-защита webhook_url
├── transport/
│   ├── rest/           # FastAPI: app factory, v1-роутеры, health
│   └── msg_streaming/  # FastStream consumer-приложение (payments.new)
└── entrypoints/        # api.py, consumer.py, relay.py

ops/                    # Dockerfile, compose, entrypoint-скрипты
```

## Примеры API

Все эндпоинты требуют заголовок `X-API-Key` (значение `API_KEY` из `.env`).

```bash
# Создание платежа (Idempotency-Key обязателен)
curl -s -X POST http://localhost:8000/api/v1/payments \
  -H "X-API-Key: dev-secret-change-me" \
  -H "Idempotency-Key: order-123" \
  -H "Content-Type: application/json" \
  -d '{
    "amount": "1500.50",
    "currency": "RUB",
    "description": "Заказ #123",
    "metadata": {"order_id": "123"},
    "webhook_url": "https://example.com/webhook"
  }'
# 202 Accepted
# {"payment_id": "019305f0-...", "status": "pending", "created_at": "..."}

# Повторный запрос с тем же Idempotency-Key
# 200 OK + заголовок Idempotency-Replayed: true, тело идентично первому ответу

# Получение информации о платеже
curl -s http://localhost:8000/api/v1/payments/<payment_id> \
  -H "X-API-Key: dev-secret-change-me"
```

Пока платёж обрабатывается, `status = pending`; consumer эмулирует работу шлюза
(2–5 секунд, 90% успех), обновляет статус (`succeeded`/`failed`) и отправляет
webhook на указанный URL с пейлоадом:

```json
{"payment_id": "019305f0-...", "status": "succeeded"}
```

## Проверка Outbox и DLQ

- **Outbox**: после создания платежа строка в таблице `outbox` имеет статус
  `pending`; relay публикует событие и переводит её в `published`. Ошибки
  публикации ретраятся с экспоненциальным backoff **без ограничения числа
  попыток**: после `RELAY_MAX_ATTEMPTS` меняется только уровень лога
  (ERROR → алертинг), событие не может потеряться. Статус `dead` relay
  выставляет только для «отравленных» строк — payload, который невозможно
  разобрать в событие (с ошибкой в `last_error`); это ручное вмешательство
  оператора.
- **DLQ**: если webhook клиента недоступен, сообщение пройдёт 3 попытки
  (задержки 2с и 4с) и окажется в очереди `payments.new.dlq` — посмотреть можно в
  RabbitMQ Management UI (http://localhost:15672, вкладка Queues). Для локальной
  демонстрации используйте `webhook_url`, указывающий на несуществующий адрес,
  например `http://93.184.216.34:1/hook`.

## Ручная проверка API (smoke-тест)

Для проверки живой инсталляции есть скрипт `scripts/smoke_test.sh` — он
прогоняет основные сценарии и печатает `PASS`/`FAIL` по каждому шагу
(exit code 1 при неудачах). Требует запущенный стек.

```bash
task smoke
# или напрямую, с реальным приёмником webhook (например, адресом webhook.site):
SMOKE_WEBHOOK_URL=https://webhook.site/<uuid> ./scripts/smoke_test.sh
```

Что проверяется:

- healthcheck, создание платежа (202), идемпотентный replay того же тела
  (200 + `Idempotency-Replayed: true`);
- канонический хэш тела: replay с другим порядком ключей (в т.ч. внутри
  `metadata`) → 200;
- тот же ключ + другое тело → 409 `idempotency_conflict`;
- авторизация (401/403) и валидация `Idempotency-Key`: отсутствует / пустой /
  длиннее 255 символов → 422;
- обработка платежа: статус меняется с `pending` на `succeeded`/`failed`,
  GET платёж → 200, несуществующий → 404;
- DLQ: платёж с недоступным webhook после 3 попыток попадает в
  `payments.new.dlq`; malformed-сообщение — в DLQ сразу, без requeue-цикла
  (через RabbitMQ Management API);
- outbox: строки relay в статусе `published` (через `psql` в контейнере).

Переменные окружения:

| Переменная | Описание | По умолчанию |
|---|---|---|
| `SMOKE_API_URL` | базовый URL API | `http://localhost:8000` |
| `SMOKE_API_KEY` | значение `X-API-Key` | `dev-secret-change-me` |
| `SMOKE_WEBHOOK_URL` | публичный URL-приёмник webhook (чтобы платежи не фейлили доставку) | `http://8.8.8.8:8080/hook` |
| `SMOKE_UNREACHABLE_WEBHOOK_URL` | адрес для теста DLQ | `http://93.184.216.34:1/hook` |
| `SMOKE_RABBIT_UI` | RabbitMQ Management API | `http://guest:guest@localhost:15672` |
| `SMOKE_SKIP_BROKER=1` | пропустить проверки RabbitMQ | — |
| `SMOKE_SKIP_DB=1` | пропустить проверку outbox через БД | — |
| `SMOKE_COLOR` | цвета вывода: `always` / `never` / `auto` (только в терминале) | `auto` |

Ключевые запросы вручную (curl):

```bash
# Создание платежа → 202
curl -si -X POST http://localhost:8000/api/v1/payments \
  -H "X-API-Key: dev-secret-change-me" \
  -H "Idempotency-Key: order-123" \
  -H "Content-Type: application/json" \
  -d '{"amount":"1500.50","currency":"RUB","description":"Заказ #123",
       "metadata":{"order_id":"1"},"webhook_url":"https://example.com/hook"}'

# Повтор с тем же ключом и телом → 200 + Idempotency-Replayed: true
# Тот же ключ, другое тело → 409 idempotency_conflict

# Сообщений в DLQ (RabbitMQ Management API):
curl -s -u guest:guest http://localhost:15672/api/queues/%2F/payments.new.dlq
```

> Внимание: `curl` отбрасывает заголовки с пустым значением; чтобы передать
> пустой `Idempotency-Key` вручную, используйте `-H "Idempotency-Key;"`.

## Конфигурация

Все параметры задаются переменными окружения (см. `.env.example`):

| Переменная | Описание | По умолчанию |
|---|---|---|
| `API_KEY` | статический ключ для заголовка `X-API-Key` | `dev-secret-change-me` |
| `API_HOST`, `API_PORT`, `API_WORKERS` | адрес, порт и число воркеров API (granian) | `0.0.0.0`, `8000`, `1` |
| `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | подключение к PostgreSQL (DSN собирается из этих полей) | `localhost`, `5432`, `postgres`, `postgres`, `payments` |
| `RABBITMQ_HOST`, `RABBITMQ_PORT`, `RABBITMQ_USER`, `RABBITMQ_PASSWORD` | подключение к RabbitMQ (DSN собирается из этих полей) | `localhost`, `5672`, `guest`, `guest` |
| `RABBITMQ_EXCHANGE_NAME`, `RABBITMQ_QUEUE_NAME`, `RABBITMQ_DLX_NAME`, `RABBITMQ_DLQ_NAME`, `RABBITMQ_DLQ_ROUTING_KEY` | топология RabbitMQ | см. `.env.example` |
| `RABBITMQ_UI_PORT` | хост-порт Management UI (compose) | `15672` |
| `EMULATION_MIN/MAX_DELAY_SECONDS` | диапазон задержки эмуляции шлюза | 2 / 5 |
| `EMULATION_SUCCESS_RATE` | доля успешных платежей | 0.9 |
| `RELAY_*` | интервал/батч/порог эскалации логов/backoff relay (retry публикации бесконечный) | см. `.env.example` |
| `CONSUMER_MAX_RETRIES`, `CONSUMER_RETRY_DELAYS_SECONDS` | 3 попытки: 1 + 2 ретрая, задержки `[2, 4]` | 2, `[2,4]` |
| `WEBHOOK_TIMEOUT_SECONDS` | таймаут webhook-запроса | 5 |
| `LOG_LEVEL`, `ENV` | уровень логирования; окружение выбирает конфиг: `production`/`staging` — JSON, `dev`/`test` — console | `INFO`, `dev` |
| `LOG_COLORS` | цветной консольный логгер (dev): `always` — цвета даже в `docker compose logs`, `never` — отключить, `auto` — только в терминале | `auto` |

## Разработка

```bash
uv sync --all-groups          # установка зависимостей (нужен uv)
task test                     # ruff + mypy + pytest
```

Тесты:

- **unit** — валидация схем, SSRF-защита, auth-домен, логика выбора retry-очереди/DLQ
  (`uv run pytest src/tests/unit`);
- **api** — полный путь через настоящие PostgreSQL и RabbitMQ в
  testcontainers: создание → outbox → relay → consumer → webhook (mock-сервер),
  идемпотентность, авторизация, сценарий 3 попыток → DLQ, конкурентные дубли
  сообщений (шлюз вызывается один раз), бесконечный retry outbox-публикации
  (`uv run pytest src/tests/api`; требуют запущенный docker — без него тесты
  **падают с ошибкой**, а не пропускаются молча, чтобы гарантии доставки
  нельзя было выдать за проверенные).

## Проектные решения

- **Свой outbox relay вместо готовой библиотеки** (`faststream-outbox` и аналогов):
  outbox — один из центральных критериев задачи, реализация паттерна показана в
  коде (`app/domains/payments/services/outbox_relay.py`): claim через
  `FOR UPDATE SKIP LOCKED`,
  publisher confirms, экспоненциальный backoff с бесконечным retry публикации;
  статус `dead` — только для «отравленных» строк с непарсимым payload
  (ручное вмешательство оператора). В
  production-проекте стоит рассмотреть `faststream-outbox` (lease-токены,
  LISTEN/NOTIFY, метрики) — интерфейс relay изолирован, замена локальна.
- **Retry через TTL-очереди RabbitMQ, без Redis**: `x-message-ttl` +
  dead-letter-роутинг обратно в рабочую очередь — нативный паттерн брокера,
  счётчик попыток в заголовках переживает рестарты consumer'а.
- **Единый retry-бюджет** (3 попытки на сообщение): разделение retry процессинга
  и webhook не требуется — процессинг идемпотентен, повторные доставки только
  до-отправляют webhook. Отдельная webhook-механика — возможное расширение.
- **Publisher confirms на всех публикациях** (включены в aio-pika по умолчанию):
  outbox-строка помечается `published`, а исходное сообщение подтверждается
  только после подтверждения брокера.

## Ограничения (осознанные)

- Webhook-уведомления доставляются at-least-once: при сбое между отправкой и
  подтверждением сообщения клиент может получить webhook повторно — получатель
  должен быть идемпотентен по `payment_id`.
- SSRF-защита webhook_url базовая: схемы http/https, блок literal-адресов
  private/loopback/link-local/reserved и DNS-резолв в непубличные адреса.
  Защита от DNS-rebinding не реализована.
- Сообщения в `payments.new.dlq` не обрабатываются автоматически; re-drive —
  ручной (или отдельная утилита, которую можно добавить).
- Webhook-запросы не подписываются; HMAC-подпись — очевидное следующее
  расширение (схема события уже версионируется через `event_version`).
- Метрик и трейсинга нет (точки расширения: middleware и структурированные логи
  с `request_id`).
