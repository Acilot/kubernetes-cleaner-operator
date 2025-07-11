# Kubernetes Cleaner Operator

**Kubernetes Cleaner Operator** предназначен для крупных кластеров, где сложно контролировать состояние подов и деплойментов.

## Описание функционала

- **Автоматическое удаление неймспейсов старше 250 дней**: Периодически проверяет неймспейсы по заданной маске из переменных и удаляет их.
- **Автоматическое удаление подов**: Периодически проверяет состояние деплойментов в кластере на наличие подов, находящихся не в состоянии `RUNNING`, и удаляет такие поды.
- **Автоматическое масштабирование деплойментов**: Если поды снова появляются и остаются в состоянии `Pending` более 1 часа, деплойменты автоматически масштабируются до 0.

## Структура проекта

- **`/app`**: Основной код приложения
- **`/k8s/`**: Содержит все необходимые манифесты и роли доступа для установки и настройки оператора.
- **`github/workflows/`**: Предоставляет полный workflow для сборки и деплоя приложения в кластер Yandex Cloud.
