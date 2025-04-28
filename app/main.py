import kopf
import kubernetes
import os
import re
from datetime import datetime, timezone, timedelta

# Получаем из окружения, разделяем по запятым, убираем пробелы
NAMESPACE_PATTERNS = [pattern.strip() for pattern in os.environ.get("NAMESPACE_PATTERNS", ".*std-.*").split(",")]
NAMESPACE_LIST = [ns.strip() for ns in os.environ.get("NAMESPACE_LIST", "").split(",") if ns.strip()]

EXCLUDED_NAMESPACES = {"kube-system", "kube-public", "kube-node-lease"}

def namespace_matches(name: str) -> bool:
    return name.startswith("ns") and name not in EXCLUDED_NAMESPACES
    if name in EXCLUDED_NAMESPACES:
        return False
    if name in NAMESPACE_LIST:
        return True
    for pattern in NAMESPACE_PATTERNS:
        if re.match(pattern, name):
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
    kubernetes.config.load_incluster_config()
    v1 = kubernetes.client.CoreV1Api()
    apps_v1 = kubernetes.client.AppsV1Api()

    # Получим все неймспейсы в кластере
    all_namespaces = v1.list_namespace().items
    # Отфильтруем по маскам и списку
    namespaces_to_check = [ns.metadata.name for ns in all_namespaces if namespace_matches(ns.metadata.name)]

    logger.info(f"Неймспейсы для проверки: {namespaces_to_check}")

    for ns in namespaces_to_check:
        try:
            deployments = apps_v1.list_namespaced_deployment(ns).items
        except kubernetes.client.exceptions.ApiException as e:
            logger.error(f"Ошибка при получении деплойментов в неймспейсе {ns}: {e}")
            continue

        for deployment in deployments:
            dep_name = deployment.metadata.name
            logger.info(f"Обрабатываем Deployment '{dep_name}' в неймспейсе '{ns}'")

            label_selector = ",".join([f"{k}={v}" for k, v in (deployment.spec.selector.match_labels or {}).items()])
            pods = v1.list_namespaced_pod(ns, label_selector=label_selector).items

            if not pods:
                logger.info(f"Нет подов для деплоймента '{dep_name}' в неймспейсе '{ns}'")
                continue

            for pod in pods:
                pod_name = pod.metadata.name
                pod_phase = pod.status.phase
                start_time = pod.status.start_time.isoformat() if pod.status.start_time else "unknown"

                if pod_not_running_long_enough(pod):
                    logger.info(f"Удаляю под '{pod_name}' из неймспейса '{ns}', статус: {pod_phase}, старт: {start_time} (более 24 часов не в Running)")
                    try:
                        v1.delete_namespaced_pod(pod_name, ns)
                        logger.info(f"Под '{pod_name}' успешно удалён")
                    except kubernetes.client.exceptions.ApiException as e:
                        logger.error(f"Ошибка при удалении пода '{pod_name}': {e}")
                else:
                    logger.debug(f"Под '{pod_name}' в неймспейсе '{ns}' в статусе '{pod_phase}', старт: {start_time} - удаление не требуется")
