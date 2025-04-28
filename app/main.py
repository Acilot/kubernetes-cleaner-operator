import kopf
import kubernetes
import os
import re
from datetime import datetime, timezone, timedelta

NAMESPACE_PATTERN = os.environ.get("NAMESPACE_PATTERN", ".*std-.*")
EXCLUDED_NAMESPACES = {"kube-system", "kube-public", "kube-node-lease"}

def namespace_matches(name):
    match = re.match(NAMESPACE_PATTERN, name)
    if match and name not in EXCLUDED_NAMESPACES:
        return True
    return False

def pod_not_running_long_enough(pod, threshold_hours=24):
    phase = pod.status.phase
    if phase == "Running":
        return False
    if not pod.status.start_time:
        return False
    age = datetime.now(timezone.utc) - pod.status.start_time
    return age > timedelta(hours=threshold_hours)

@kopf.timer('apps', 'v1', 'deployments', interval=3600)
def cleanup_pods(spec, namespace, name, logger, **kwargs):
    if not namespace_matches(namespace):
        logger.debug(f"Пропускаем неймспейс {namespace} - не соответствует маске или системный")
        return

    logger.info(f"Обрабатываем Deployment '{name}' в неймспейсе '{namespace}'")

    kubernetes.config.load_incluster_config()
    v1 = kubernetes.client.CoreV1Api()

    # Поиск подов с лейблом app={name} (предполагается, что деплоймент ставит такой лейбл)
    label_selector = f"app={name}"
    pods = v1.list_namespaced_pod(namespace, label_selector=label_selector).items

    if not pods:
        logger.info(f"Нет подов с лейблом '{label_selector}' в неймспейсе '{namespace}' для деплоймента '{name}'")
        return

    for pod in pods:
        pod_name = pod.metadata.name
        pod_phase = pod.status.phase
        start_time = pod.status.start_time.isoformat() if pod.status.start_time else "unknown"

        if pod_not_running_long_enough(pod):
            logger.info(f"Удаляю под '{pod_name}' из неймспейса '{namespace}', статус: {pod_phase}, старт: {start_time} (более 24 часов не в Running)")
            try:
                v1.delete_namespaced_pod(pod_name, namespace)
                logger.info(f"Под '{pod_name}' успешно удалён")
            except kubernetes.client.exceptions.ApiException as e:
                logger.error(f"Ошибка при удалении пода '{pod_name}': {e}")
        else:
            logger.debug(f"Под '{pod_name}' в неймспейсе '{namespace}' в статусе '{pod_phase}', старт: {start_time} - удаление не требуется")
