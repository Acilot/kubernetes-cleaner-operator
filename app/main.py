import kopf
import kubernetes
import os
import re
from datetime import datetime, timezone, timedelta

# Настройки из переменных окружения
NAMESPACE_PATTERNS = [pattern.strip() for pattern in os.environ.get("NAMESPACE_PATTERNS", "std-.*").split(",")]
NAMESPACE_LIST = [ns.strip() for ns in os.environ.get("NAMESPACE_LIST", "").split(",") if ns.strip()]
EXCLUDED_NAMESPACES = {"kube-system", "kube-public", "kube-node-lease", "default"}
KOPF_FINALIZER = "kopf.zalando.org/KopfFinalizerMarker"

def namespace_matches(name: str) -> bool:
    """Проверяет, подходит ли namespace под маску или список"""
    if name in EXCLUDED_NAMESPACES:
        return False
    if name in NAMESPACE_LIST:
        return True
    for pattern in NAMESPACE_PATTERNS:
        if re.match(pattern, name):
            return True
    return False

def namespace_older_than(ns, days=250):
    """Проверяет, старше ли namespace указанного количества дней"""
    creation_time = ns.metadata.creation_timestamp
    if not creation_time:
        return False
    age = datetime.now(timezone.utc) - creation_time
    return age > timedelta(days=days)

def pod_not_running_long_enough(pod, threshold_hours=24):
    """Проверяет, находится ли под не в Running статусе дольше указанного времени"""
    phase = pod.status.phase
    if phase == "Running":
        return False
    if not pod.status.start_time:
        return False
    age = datetime.now(timezone.utc) - pod.status.start_time
    return age > timedelta(hours=threshold_hours)

def pod_pending_too_long(pod, threshold_hours=1):
    """Проверяет, находится ли под в Pending статусе дольше указанного времени"""
    if pod.status.phase != "Pending":
        return False
    start_time = pod.status.start_time or pod.metadata.creation_timestamp
    if not start_time:
        return False
    age = datetime.now(timezone.utc) - start_time
    return age > timedelta(hours=threshold_hours)

def remove_finalizers_from_namespace(v1, ns, logger):
    """Удаляет финализаторы из namespace"""
    ns_name = ns.metadata.name
    finalizers = getattr(ns.spec, 'finalizers', []) or []
    if finalizers:
        logger.warning(f"Namespace '{ns_name}' застрял в Terminating из-за финализаторов: {finalizers}. Удаляю финализаторы.")
        body = {"spec": {"finalizers": []}}
        try:
            v1.patch_namespace(ns_name, body)
            logger.info(f"Финализаторы удалены из namespace '{ns_name}'.")
        except Exception as e:
            logger.error(f"Ошибка при удалении финализаторов из namespace '{ns_name}': {e}")

def remove_finalizers_from_resources(namespace, logger):
    """Удаляет финализаторы Kopf со всех ресурсов в namespace"""
    try:
        kubernetes.config.load_incluster_config()
        v1 = kubernetes.client.CoreV1Api()
        apps_v1 = kubernetes.client.AppsV1Api()
        
        # Удаляем финализаторы с Pod
        try:
            pods = v1.list_namespaced_pod(namespace).items
            for pod in pods:
                finalizers = pod.metadata.finalizers or []
                if KOPF_FINALIZER in finalizers:
                    logger.warning(f"Удаляю финализатор с Pod {pod.metadata.name} в ns {namespace}")
                    body = {"metadata": {"finalizers": [f for f in finalizers if f != KOPF_FINALIZER]}}
                    v1.patch_namespaced_pod(pod.metadata.name, namespace, body)
        except Exception as e:
            logger.error(f"Ошибка при снятии финализаторов с Pod в {namespace}: {e}")
            
        # Удаляем финализаторы с Deployment
        try:
            deployments = apps_v1.list_namespaced_deployment(namespace).items
            for dep in deployments:
                finalizers = dep.metadata.finalizers or []
                if KOPF_FINALIZER in finalizers:
                    logger.warning(f"Удаляю финализатор с Deployment {dep.metadata.name} в ns {namespace}")
                    body = {"metadata": {"finalizers": [f for f in finalizers if f != KOPF_FINALIZER]}}
                    apps_v1.patch_namespaced_deployment(dep.metadata.name, namespace, body)
        except Exception as e:
            logger.error(f"Ошибка при снятии финализаторов с Deployment в {namespace}: {e}")
            
        # Другие типы ресурсов можно добавить по аналогии
    except Exception as e:
        logger.error(f"Ошибка при снятии финализаторов с ресурсов в {namespace}: {e}")

@kopf.timer('namespaces', interval=1200)  # Каждые 20 минут
def finalize_stuck_namespaces(logger, **kwargs):
    """Обработка зависших namespace в состоянии Terminating"""
    kubernetes.config.load_incluster_config()
    v1 = kubernetes.client.CoreV1Api()
    all_namespaces = v1.list_namespace().items

    for ns in all_namespaces:
        ns_name = ns.metadata.name
        if namespace_matches(ns_name) and ns.status.phase == "Terminating":
            logger.warning(f"Namespace {ns_name} завис в Terminating. Снимаю финализаторы.")
            # Сначала снимаем финализаторы с ресурсов
            remove_finalizers_from_resources(ns_name, logger)
            # Затем снимаем финализаторы с самого namespace, если он все еще в Terminating
            try:
                ns_updated = v1.read_namespace(ns_name)
                if ns_updated.status.phase == "Terminating":
                    remove_finalizers_from_namespace(v1, ns_updated, logger)
            except Exception as e:
                logger.error(f"Ошибка при обновлении namespace {ns_name}: {e}")

@kopf.timer('namespaces', interval=86400)  # Раз в сутки
def cleanup_namespaces(logger, **kwargs):
    """Удаление старых namespace по маске и возрасту"""
    kubernetes.config.load_incluster_config()
    v1 = kubernetes.client.CoreV1Api()
    all_namespaces = v1.list_namespace().items

    for ns in all_namespaces:
        ns_name = ns.metadata.name
        if namespace_matches(ns_name) and namespace_older_than(ns, days=200):
            # Если namespace уже в процессе удаления (Terminating)
            if ns.metadata.deletion_timestamp:
                remove_finalizers_from_namespace(v1, ns, logger)
            else:
                logger.info(f"Удаляю namespace '{ns_name}' (возраст: {ns.metadata.creation_timestamp})")
                try:
                    v1.delete_namespace(ns_name)
                    logger.info(f"Namespace '{ns_name}' успешно удалён.")
                except Exception as e:
                    logger.error(f"Ошибка при удалении namespace '{ns_name}': {e}")
        else:
            logger.debug(f"Namespace '{ns_name}' не подходит под условия удаления.")

@kopf.timer('apps', 'v1', 'deployments', interval=3600)
def cleanup_pods(spec, namespace, name, logger, **kwargs):
    """Очистка подов и скейлинг деплойментов"""
    kubernetes.config.load_incluster_config()
    v1 = kubernetes.client.CoreV1Api()
    apps_v1 = kubernetes.client.AppsV1Api()

    all_namespaces = v1.list_namespace().items
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
            selector = deployment.spec.selector.match_labels or {}
            if not selector:
                logger.warning(f"Deployment '{dep_name}' в неймспейсе '{ns}' не имеет selector.match_labels, пропускаем.")
                continue
            label_selector = ",".join([f"{k}={v}" for k, v in selector.items()])
            pods = v1.list_namespaced_pod(ns, label_selector=label_selector).items

            # Скейлим deployment в 0, если есть Pending-под старше часа
            for pod in pods:
                if pod_pending_too_long(pod):
                    logger.warning(f"Под '{pod.metadata.name}' в деплойменте '{dep_name}' в неймспейсе '{ns}' находится в Pending более часа. Скейлим deployment в 0.")
                    try:
                        apps_v1.patch_namespaced_deployment_scale(
                            name=dep_name,
                            namespace=ns,
                            body={'spec': {'replicas': 0}}
                        )
                        logger.info(f"Deployment '{dep_name}' в неймспейсе '{ns}' успешно скейлен в 0.")
                    except Exception as e:
                        logger.error(f"Ошибка при скейлинге deployment '{dep_name}' в неймспейсе '{ns}': {e}")
                    break  # Достаточно одного такого пода

            # Удаляем поды не в Running более 24 часов
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
