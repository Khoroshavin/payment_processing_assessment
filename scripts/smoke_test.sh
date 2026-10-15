#!/usr/bin/env bash
# Smoke-тест API платежей против запущенного стека (task up).
#
# Прогоняет основные сценарии: создание платежа, идемпотентный replay
# (включая канонический хэш тела), 409, ошибки авторизации/валидации,
# обработку платежа, DLQ (3 попытки при недоступном webhook) и outbox.
# Проверки RabbitMQ выполняются через Management API, БД — через psql в
# контейнере; при недоступности они пропускаются (SKIP).
#
# Переменные окружения:
#   SMOKE_API_URL          базовый URL API        (по умолчанию http://localhost:8000)
#   SMOKE_API_KEY          ключ заголовка X-API-Key (по умолчанию dev-secret-change-me)
#   SMOKE_WEBHOOK_URL      публичный URL-приёмник webhook, чтобы платежи
#                          не фейлили доставку (например адрес webhook.site)
#                          (по умолчанию http://8.8.8.8:8080/hook — доставка
#                          не сработает, но платеж создастся)
#   SMOKE_UNREACHABLE_WEBHOOK_URL  адрес для теста DLQ
#                          (по умолчанию http://93.184.216.34:1/hook)
#   SMOKE_RABBIT_UI        RabbitMQ Management API (по умолчанию
#                          http://guest:guest@localhost:15672)
#   SMOKE_SKIP_BROKER=1    пропустить проверки RabbitMQ
#   SMOKE_SKIP_DB=1        пропустить проверку outbox через БД
#   SMOKE_COLOR            цвета вывода: always | never | auto (по умолчанию)
#
# Выход: 0 — все проверки PASS, 1 — есть FAIL.

set -uo pipefail

# Цветной вывод: SMOKE_COLOR=always|never|auto (по умолчанию auto — цвета
# только когда stdout является терминалом).
SMOKE_COLOR="${SMOKE_COLOR:-auto}"
case "$SMOKE_COLOR" in
    always|1|true|yes|on) USE_COLOR=1 ;;
    never|0|false|no|off) USE_COLOR=0 ;;
    *)
        if [[ -t 1 ]]; then USE_COLOR=1; else USE_COLOR=0; fi
        ;;
esac
if (( USE_COLOR )); then
    C_BOLD=$'\033[1m'
    C_DIM=$'\033[2m'
    C_RED=$'\033[31m'
    C_GREEN=$'\033[32m'
    C_YELLOW=$'\033[33m'
    C_CYAN=$'\033[36m'
    C_RESET=$'\033[0m'
else
    C_BOLD=""
    C_DIM=""
    C_RED=""
    C_GREEN=""
    C_YELLOW=""
    C_CYAN=""
    C_RESET=""
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

API_URL="${SMOKE_API_URL:-http://localhost:8000}"
API_KEY="${SMOKE_API_KEY:-dev-secret-change-me}"
WEBHOOK_URL="${SMOKE_WEBHOOK_URL:-http://8.8.8.8:8080/hook}"
UNREACHABLE_WEBHOOK_URL="${SMOKE_UNREACHABLE_WEBHOOK_URL:-http://93.184.216.34:1/hook}"
RABBIT_UI="${SMOKE_RABBIT_UI:-http://guest:guest@localhost:15672}"

# Данные PostgreSQL из .env (как в docker-compose), с дефолтами.
PG_USER="$(grep -E '^POSTGRES_USER=' "$ROOT/.env" 2>/dev/null | cut -d= -f2-)"
PG_DB="$(grep -E '^POSTGRES_DB=' "$ROOT/.env" 2>/dev/null | cut -d= -f2-)"
PG_USER="${PG_USER:-postgres}"
PG_DB="${PG_DB:-payments}"

PASS=0
FAIL=0
SKIP=0

say() { printf '\n%s== %s ==%s\n' "${C_BOLD}${C_CYAN}" "$*" "$C_RESET"; }
ok() { PASS=$((PASS + 1)); printf '  %sPASS%s  %s\n' "$C_GREEN" "$C_RESET" "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  %sFAIL%s  %s (ожидалось: %s, получено: %s)\n' "$C_RED" "$C_RESET" "$1" "$2" "$3"; }
skip() { SKIP=$((SKIP + 1)); printf '  %sSKIP%s  %s\n' "$C_YELLOW" "$C_RESET" "$1"; }
info() { printf '  %sINFO%s  %s\n' "$C_DIM" "$C_RESET" "$*"; }

check() { # $1 имя, $2 ожидаемое, $3 фактическое
    if [[ "$3" == "$2" ]]; then ok "$1"; else bad "$1" "$2" "$3"; fi
}

# --- preflight: fail fast, если API недоступен ----------------------------

if ! curl -sS -m 5 -o /dev/null "$API_URL/healthz"; then
    printf '%sERROR: API недоступен (%s). Запустите стек командой `task up`.%s\n' \
        "$C_RED" "$API_URL" "$C_RESET" >&2
    exit 1
fi

# --- helpers -------------------------------------------------------------

json_get() { # $1 файл, $2 путь через точки ("error.code")
    python3 -c 'import json, sys
d = json.load(open(sys.argv[1]))
for k in sys.argv[2].split("."):
    d = d[int(k)] if k.isdigit() else d[k]
print(d)' "$1" "$2"
}

header_get() { # $1 файл заголовков, $2 имя заголовка
    awk -v h="$2" 'tolower($0) ~ "^" tolower(h) ":" {sub(/^[^:]*: ?/, ""); gsub(/\r/, ""); print; exit}' "$1"
}

api() { # $1 метод, $2 путь, [$3 тело JSON], [$4... доп. curl-аргументы]
    local method="$1" path="$2" body="${3:-}"
    local args=(-sS -o "$TMP/body" -D "$TMP/headers" -w '%{http_code}'
        -X "$method" "$API_URL$path" -H "X-API-Key: $API_KEY")
    if [[ -n "$body" ]]; then
        args+=(-H "Content-Type: application/json" -d "$body")
    fi
    if [[ $# -ge 4 ]]; then
        curl "${args[@]}" "${@:4}" > "$TMP/code"
    else
        curl "${args[@]}" > "$TMP/code"
    fi
}

post_payment() { # $1 ключ идемпотентности (или "" чтобы не слать), $2 тело
    local key="$1" body="$2"
    if [[ -n "$key" ]]; then
        api POST /api/v1/payments "$body" -H "Idempotency-Key: $key"
    else
        api POST /api/v1/payments "$body"
    fi
}

status_code() { awk '{print $2}' "$TMP/headers" | head -1; }

# --- базовые проверки API ------------------------------------------------

say "Базовые проверки API ($API_URL)"

api GET /healthz
check "healthz → 200" "200" "$(status_code)"
check "healthz: status=ok" "ok" "$(json_get "$TMP/body" status)"

KEY="smoke-$(date +%s)-$$"
LONG_KEY="$(python3 -c 'print("k" * 256)')"

BODY="{\"amount\":\"1500.50\",\"currency\":\"RUB\",\"description\":\"smoke test\",\"metadata\":{\"order_id\":\"1\",\"note\":\"smoke\"},\"webhook_url\":\"$WEBHOOK_URL\"}"
BODY_REORDERED="{\"webhook_url\":\"$WEBHOOK_URL\",\"metadata\":{\"note\":\"smoke\",\"order_id\":\"1\"},\"description\":\"smoke test\",\"currency\":\"RUB\",\"amount\":\"1500.50\"}"
BODY_DIFFERENT="{\"amount\":\"999.99\",\"currency\":\"RUB\",\"description\":\"smoke test\",\"metadata\":{\"order_id\":\"1\"},\"webhook_url\":\"$WEBHOOK_URL\"}"

say "Создание и идемпотентность"

post_payment "$KEY" "$BODY"
check "POST /payments → 202" "202" "$(status_code)"
PAYMENT_ID="$(json_get "$TMP/body" payment_id)"
check "в ответе есть payment_id" "yes" "$([[ -n "${PAYMENT_ID:-}" ]] && echo yes || echo no)"
check "начальный статус pending" "pending" "$(json_get "$TMP/body" status)"

post_payment "$KEY" "$BODY"
check "replay того же тела → 200" "200" "$(status_code)"
check "заголовок Idempotency-Replayed: true" "true" "$(header_get "$TMP/headers" Idempotency-Replayed)"
check "replay возвращает тот же payment_id" "$PAYMENT_ID" "$(json_get "$TMP/body" payment_id)"

post_payment "$KEY" "$BODY_REORDERED"
check "replay с другим порядком ключей → 200 (canonical hash)" "200" "$(status_code)"
check "заголовок Idempotency-Replayed: true" "true" "$(header_get "$TMP/headers" Idempotency-Replayed)"

post_payment "$KEY" "$BODY_DIFFERENT"
check "тот же ключ + другое тело → 409" "409" "$(status_code)"
check "код ошибки idempotency_conflict" "idempotency_conflict" "$(json_get "$TMP/body" error.code)"

say "GET-эндпоинты"

api GET "/api/v1/payments/$PAYMENT_ID"
check "GET платёж → 200" "200" "$(status_code)"
check "payment_id совпадает" "$PAYMENT_ID" "$(json_get "$TMP/body" payment_id)"

api GET /api/v1/payments/00000000-0000-0000-0000-000000000000
check "GET несуществующий платёж → 404" "404" "$(status_code)"

say "Авторизация"

curl -sS -o "$TMP/body" -D "$TMP/headers" -w '%{http_code}' -X POST \
    "$API_URL/api/v1/payments" -H "Content-Type: application/json" -d "$BODY" > "$TMP/code"
check "без X-API-Key → 401" "401" "$(cat "$TMP/code")"

curl -sS -o "$TMP/body" -D "$TMP/headers" -w '%{http_code}' -X POST \
    "$API_URL/api/v1/payments" \
    -H "X-API-Key: wrong-key" -H "Idempotency-Key: smoke-auth" \
    -H "Content-Type: application/json" -d "$BODY" > "$TMP/code"
check "неверный X-API-Key → 403" "403" "$(cat "$TMP/code")"

say "Валидация Idempotency-Key и тела"

post_payment "" "$BODY"
check "без Idempotency-Key → 422" "422" "$(status_code)"

# curl отбрасывает заголовки с пустым значением; "Idempotency-Key;"
# заставляет curl передать заголовок с пустым значением.
api POST /api/v1/payments "$BODY" -H "Idempotency-Key;"
check "пустой Idempotency-Key → 422" "422" "$(status_code)"

post_payment "$LONG_KEY" "$BODY"
check "Idempotency-Key 256 символов → 422" "422" "$(status_code)"

post_payment "smoke-amount" "{\"amount\":\"-5\",\"currency\":\"RUB\",\"description\":\"x\",\"metadata\":{},\"webhook_url\":\"$WEBHOOK_URL\"}"
check "невалидный amount → 422" "422" "$(status_code)"

# --- обработка платежа ---------------------------------------------------

say "Обработка платежа (consumer: эмуляция шлюза 2–5 c)"

final_status="pending"
for _ in $(seq 1 30); do
    api GET "/api/v1/payments/$PAYMENT_ID" || true
    final_status="$(json_get "$TMP/body" status 2>/dev/null || echo pending)"
    [[ "$final_status" != "pending" ]] && break
    sleep 1
done
check "статус сменился с pending" "yes" "$([[ "$final_status" != "pending" ]] && echo yes || echo no)"
info "итоговый статус платежа: $final_status"

# --- RabbitMQ: DLQ и malformed-сообщения ----------------------------------

if [[ "${SMOKE_SKIP_BROKER:-0}" == "1" ]]; then
    say "Проверки RabbitMQ пропущены"
    skip "SMOKE_SKIP_BROKER=1"
elif curl -sS -o /dev/null "$RABBIT_UI/api/overview"; then
    say "RabbitMQ: DLQ при недоступном webhook (3 попытки, ~15–30 c)"

    dlq_count() {
        curl -sS "$RABBIT_UI/api/queues/%2F/payments.new.dlq" | json_get /dev/stdin messages 2>/dev/null || echo 0
    }

    baseline="$(dlq_count)"
    info "сообщений в DLQ до теста: $baseline"

    post_payment "smoke-dlq-$$" "{\"amount\":\"10.00\",\"currency\":\"EUR\",\"description\":\"dlq test\",\"metadata\":{},\"webhook_url\":\"$UNREACHABLE_WEBHOOK_URL\"}"
    dlq_code="$(cat "$TMP/code")"
    if [[ "$dlq_code" != "202" ]]; then
        bad "создание платежа для DLQ-теста" "202" "$dlq_code"
    else
        grew="no"
        for _ in $(seq 1 60); do
            now="$(dlq_count)"
            if (( now > baseline )); then
                grew="yes"
                baseline="$now"
                break
            fi
            sleep 1
        done
        check "платёж с недоступным webhook попал в DLQ" "yes" "$grew"
    fi

    say "RabbitMQ: malformed-сообщение → DLQ сразу, без requeue"

    work_count() {
        curl -sS "$RABBIT_UI/api/queues/%2F/payments.new" | json_get /dev/stdin messages 2>/dev/null || echo 0
    }

    # Минимум по нескольким сэмплам: другие платежи скрипта могут легально
    # проходить ретраи в этот момент. С багом requeue-цикла malformed-сообщение
    # находилось бы в рабочей очереди постоянно, поднимая минимум.
    work_before="$(work_count)"
    for _ in $(seq 1 4); do
        sleep 1
        now="$(work_count)"
        (( now < work_before )) && work_before="$now"
    done

    routed="$(curl -sS -X POST -H 'Content-Type: application/json' \
        "$RABBIT_UI/api/exchanges/%2F/payments/publish" \
        -d '{
          "properties": {"content_type": "application/json", "delivery_mode": 2},
          "routing_key": "payments.new",
          "payload": "{\"payment_id\": \"not-a-uuid\", \"event_version\": 1}",
          "payload_encoding": "string"
        }' | json_get /dev/stdin routed)"
    check "публикация malformed-сообщения: routed=true" "True" "$routed"

    grew="no"
    for _ in $(seq 1 30); do
        now="$(dlq_count)"
        if (( now > baseline )); then
            grew="yes"
            break
        fi
        sleep 1
    done
    check "malformed-сообщение попало в DLQ" "yes" "$grew"

    work_after="$(work_count)"
    for _ in $(seq 1 4); do
        sleep 1
        now="$(work_count)"
        (( now < work_after )) && work_after="$now"
    done
    check "рабочая очередь не растёт (нет requeue-цикла)" "yes" \
        "$([[ "$work_after" -le "$work_before" ]] && echo yes || echo no)"
else
    skip "RabbitMQ Management API недоступен ($RABBIT_UI) — проверки DLQ пропущены"
fi

# --- БД: outbox ------------------------------------------------------------

if [[ "${SMOKE_SKIP_DB:-0}" == "1" ]]; then
    say "Проверка БД пропущена"
    skip "SMOKE_SKIP_DB=1"
elif docker compose --env-file "$ROOT/.env" -f "$ROOT/ops/docker-compose.yaml" \
    exec -T postgres true > /dev/null 2>&1; then
    say "Outbox (последние строки через psql)"
    docker compose --env-file "$ROOT/.env" -f "$ROOT/ops/docker-compose.yaml" \
        exec -T postgres psql -U "$PG_USER" -d "$PG_DB" -c \
        "select status, attempts, published_at is not null as published
         from outbox order by id desc limit 5;"
    info "ожидаемо: статус published у всех строк с завершённой публикацией"
else
    skip "контейнер postgres недоступен — проверка outbox пропущена"
fi

# --- итоги -----------------------------------------------------------------

printf '\n%sИтого:%s %sPASS=%d%s %sFAIL=%d%s %sSKIP=%d%s\n' \
    "$C_BOLD" "$C_RESET" \
    "$C_GREEN" "$PASS" "$C_RESET" \
    "$C_RED" "$FAIL" "$C_RESET" \
    "$C_YELLOW" "$SKIP" "$C_RESET"
if (( FAIL > 0 )); then
    printf '%sЕсть упавшие проверки — стек или ожидания требуют внимания.%s\n' \
        "$C_RED" "$C_RESET"
    exit 1
fi
printf '%sВсе проверки пройдены.%s\n' "$C_GREEN" "$C_RESET"
