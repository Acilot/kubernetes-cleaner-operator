import kopf
from kubernetes import client, config
from datetime import datetime, timedelta

# Настройка конфигурации Kubernetes
try:
    config.load_incluster_config()
except config.ConfigException:
    config.load_kube_config()

v1 = client.CoreV1Api()
apps_v1 = client.AppsV1Api()

@kopf.timer('apps', 'v1', 'deployments', interval=3600)  # Проверять каждый час
def check_deployments(spec, name, namespace, logger, **kwargs):
    deployment = apps_v1.read_namespaced_deployment(name, namespace)
    pods = v1.list_namespaced_pod(namespace, label_selector=','.join([f"{k}={v}" for k, v in deployment.spec.selector.match_labels.items()])).items

    for pod in pods:
        if pod.status.phase != 'Running':
            creation_time = pod.metadata.creation_timestamp.replace(tzinfo=None)
            current_time = datetime.utcnow().replace(tzinfo=None)
            time_diff = current_time - creation_time

            if time_diff > timedelta(hours=24):
                logger.info(f"Deleting deployment {name} in namespace {namespace} due to non-running pod {pod.metadata.name}")
                apps_v1.delete_namespaced_deployment(name, namespace)
                return

    logger.info(f"All pods in deployment {name} are running or recently created.")

if __name__ == "__main__":
    kopf.run()