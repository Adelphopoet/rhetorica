# Database Migrations

Управление схемой базы данных осуществляется через **Alembic** — инструмент миграций для SQLAlchemy.

## Быстрый старт

```bash
# Применить все ожидающие миграции
make db-migrate

# Создать новую миграцию (автоматически на основе изменений моделей)
make db-migrate-make

# Откатить последнюю миграцию
make db-downgrade
```

## Команды управления миграциями

| Команда | Описание |
|---------|----------|
| `make db-migrate` | Применить все миграции (`upgrade head`) |
| `make db-migrate-make` | Создать новую миграцию (интерактивно) |
| `make db-downgrade` | Откатить на одну миграцию назад |
| `make db-current` | Показать текущую ревизию |
| `make db-heads` | Показать целевую ревизию (head) |

Host-side `make` таргеты читают креды из `.env`, но подключаются к опубликованному PostgreSQL порту на `localhost`. Это сохраняет локальные CLI-миграции синхронными с `docker compose` без ручного переопределения `DATABASE_URL`.

## Создание миграций

### Автоматическая генерация

Alembic сравнивает текущие модели в `packages/backend/src/rhetorica_backend/infra/postgres/` с состоянием БД и генерирует миграцию:

```bash
cd packages/backend
uv run alembic revision --autogenerate -m "add user table"
```

> ⚠️ **Важно:** Всегда проверяйте сгенерированные миграции! Alembic может пропустить некоторые изменения (например, переименование колонок).

### Ручное создание

```bash
cd packages/backend
uv run alembic revision -m "add user table"
```

Заполните функции `upgrade()` и `downgrade()` вручную.

## Структура миграций

```
packages/backend/
  alembic/
    versions/
      fee8a509d08c_initial_schema.py    # начальная миграция
      a1b2c3d4e5f6_add_user_table.py    # новые миграции
    env.py                               # конфигурация окружения
    script.py.mako                       # шаблон миграций
  alembic.ini                            # основной конфиг
```

## Пример миграции

```python
"""add user table

Revision ID: a1b2c3d4e5f6
Revises: fee8a509d08c
Create Date: 2026-04-01 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'a1b2c3d4e5f6'
down_revision = 'fee8a509d08c'


def upgrade() -> None:
    op.create_table(
        'user',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email')
    )


def downgrade() -> None:
    op.drop_table('user')
```

## Работа в Docker

Миграции применяются автоматически при старте API (`apps/api`). 

Для локальной разработки без контейнеров:

```bash
# Поднять PostgreSQL
make infra-up

# Применить миграции
make db-migrate
```

Если вы используете `uv` на хосте, проект ожидает Python `3.12`. Репозиторий включает `.python-version`, чтобы `uv` автоматически выбрал совместимый интерпретатор.

## Отладка

### Просмотр SQL миграции без выполнения

```bash
cd packages/backend
uv run alembic upgrade head --sql
```

### Проверка состояния

```bash
# Текущая ревизия в БД
make db-current

# Целевая ревизия (head)
make db-heads

# История миграций
cd packages/backend
uv run alembic history -v
```

### Принудительная установка ревизии

```bash
# Установить конкретную ревизию (без выполнения миграций)
cd packages/backend
uv run alembic stamp a1b2c3d4e5f6
```

## Политики

1. **Каждое изменение схемы — отдельная миграция.** Не объединяйте несвязанные изменения.
2. **Все миграции должны иметь `downgrade()`.** Это критично для отката в production.
3. **Не редактируйте применённые миграции.** Создавайте новую для исправлений.
4. **Проверяйте миграции в CI.** Автоматическая проверка перед merge.
5. **Тестируйте на staging.** Перед production всегда применяйте на staging-окружении.

## Troubleshooting

### Ошибка: "Target database is not up to date"

База данных не содержит таблицу `alembic_version`. Примените миграции:

```bash
make db-migrate
```

### Ошибка: "No such table"

Миграции не применены. Запустите:

```bash
make db-migrate
```

### Конфликт миграций (multiple heads)

Если несколько разработчиков создали миграции параллельно:

```bash
# Показать конфликтующие heads
cd packages/backend
uv run alembic heads

# Слить в одну миграцию
uv run alembic merge -m "merge heads"
```

### Откат к конкретной ревизии

```bash
cd packages/backend
uv run alembic downgrade <revision_id>
```

## Ссылки

- [Alembic Documentation](https://alembic.sqlalchemy.org/)
