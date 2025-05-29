import kopf
import kubernetes
from kubernetes.client import (
    V1ObjectMeta, V1Service, V1ServiceSpec, V1ServicePort,
    V1StatefulSet, V1StatefulSetSpec, V1LabelSelector,
    V1PodTemplateSpec, V1PodSpec, V1Container, V1ContainerPort,
    V1ConfigMap, V1Deployment, V1DeploymentSpec, V1EnvVar,
    V1CronJob, V1CronJobSpec, V1JobTemplateSpec, V1JobSpec,
    V1PersistentVolumeClaim, V1PersistentVolumeClaimSpec, V1ResourceRequirements

)
from kubernetes.client.rest import ApiException
from kubernetes.client import BatchV1Api

# def create_sentinel_reset_cronjob(name, namespace, sentinel_replicas):
#     job_name = f"{name}-sentinel-reset"
#     batch_api = BatchV1Api()

#     reset_cmds = "\n".join([
#         f"echo '[INFO] Resetting {name}-sentinel-{i}...'; "
#         f"redis-cli -h {name}-sentinel-{i}.{name}-sentinel-svc.{namespace}.svc.cluster.local -p 26379 SENTINEL RESET mymaster || true"
#         for i in range(sentinel_replicas)
#     ])
#     shell_script = f"#!/bin/sh\n{reset_cmds}"

#     container = V1Container(
#         name="reset-sentinel",
#         image="redis:7.2",
#         command=["sh", "-c", shell_script]
#     )

#     pod_spec = V1PodSpec(
#         restart_policy="OnFailure",
#         containers=[container]
#     )

#     pod_template = V1PodTemplateSpec(
#         metadata=V1ObjectMeta(labels={"job": job_name}),
#         spec=pod_spec
#     )
#     job_template = V1JobTemplateSpec(
#         spec=V1JobSpec(template=pod_template)
#     )

#     cronjob = V1CronJob(
#         metadata=V1ObjectMeta(name=job_name),
#         spec=V1CronJobSpec(
#             schedule="*/3 * * * *",
#             job_template=job_template
#         )
#     )

#     try:
#         batch_api.create_namespaced_cron_job(namespace=namespace, body=cronjob)
#         print(f"[✓] CronJob {job_name} created")
#     except kubernetes.client.rest.ApiException as e:
#         if e.status != 409:
#             raise



from kubernetes.client import V1Deployment, V1DeploymentSpec, V1PodSpec, V1PodTemplateSpec, V1Container
from kubernetes.client import V1ObjectMeta, V1LabelSelector, V1ConfigMap, V1Volume, V1VolumeMount, V1KeyToPath

def create_sentinel_reset_deployment(name, namespace, sentinel_replicas):
    reset_name = f"{name}-sentinel-reset"
    script_cm_name = f"{reset_name}-script"
    sentinel_prefix = f"{name}-sentinel"
    script_filename = "sentinel-reset-loop.sh"

    kubernetes.config.load_incluster_config()
    core = kubernetes.client.CoreV1Api()
    apps = kubernetes.client.AppsV1Api()

    # 1. Create ConfigMap for script
    script = f"""#!/bin/sh

SENTINEL_PREFIX="{sentinel_prefix}"
N_SENTINELS="{sentinel_replicas}"
N_REPLICAS="{sentinel_replicas}"
RESET_INTERVAL="60"

while true; do
  echo "[INFO] $(date) - Sentinel reset loop..."

  for i in $(seq 0 $((N_SENTINELs - 1))); do
    SENTINEL="${{SENTINEL_PREFIX}}-$i.${{SENTINEL_PREFIX}}-svc.{namespace}.svc.cluster.local"
    echo "[INFO] Checking Sentinel ${{SENTINEL}}"

    SLAVE_COUNT=$(redis-cli -h ${{SENTINEL}} -p 26379 SENTINEL slaves mymaster | grep -c 'runid')

    if [ "${{SLAVE_COUNT}}" -ne "${{N_REPLICAS}}" ]; then
      echo "[RESET] Resetting Sentinel ${{SENTINEL}} (Detected ${{SLAVE_COUNT}} > Expected ${{N_REPLICAS}})"
      redis-cli -h ${{SENTINEL}} -p 26379 SENTINEL RESET mymaster
    else
      echo "[OK] Reset not required for ${{SENTINEL}}, configuration looks okay (${{SLAVE_COUNT}} slaves)"
    fi
  done

  sleep ${{RESET_INTERVAL}}
done
"""

    cm = V1ConfigMap(
        metadata=V1ObjectMeta(name=script_cm_name),
        data={script_filename: script}
    )
    try:
        core.create_namespaced_config_map(namespace, cm)
    except kubernetes.client.exceptions.ApiException as e:
        if e.status != 409:
            raise

    # 2. Create Deployment
    container = V1Container(
        name="sentinel-reset",
        image="redis:7.2",
        command=["sh", f"/opt/{script_filename}"],
        volume_mounts=[
            V1VolumeMount(name="script", mount_path="/opt", read_only=True)
        ]
    )

    volumes = [
        V1Volume(
            name="script",
            config_map={"name": script_cm_name, "items": [V1KeyToPath(key=script_filename, path=script_filename)]}
        )
    ]

    pod_template = V1PodTemplateSpec(
        metadata=V1ObjectMeta(labels={"app": reset_name}),
        spec=V1PodSpec(containers=[container], volumes=volumes)
    )

    deployment = V1Deployment(
        metadata=V1ObjectMeta(name=reset_name),
        spec=V1DeploymentSpec(
            replicas=1,
            selector=V1LabelSelector(match_labels={"app": reset_name}),
            template=pod_template
        )
    )

    try:
        apps.create_namespaced_deployment(namespace, deployment)
        print(f"✅ Created Deployment: {reset_name}")
    except kubernetes.client.exceptions.ApiException as e:
        if e.status != 409:
            raise

@kopf.on.create('mygroup.dev', 'v1', 'appconfigs')
def create_redis(spec, name, namespace, **kwargs):
    version = spec.get('version', '7.2')
    replicas = spec.get('replicas', 1)
    sentinel_enabled = spec.get('sentinel', {}).get('enabled', True)
    sentinel_replicas = spec.get('sentinel', {}).get('replicas', 3)

    labels = {"app": name}
    redis_svc = f"{name}-svc"
    redis_sts = name
    sentinel_cfg = f"{name}-sentinel-config"
    sentinel_deploy = f"{name}-sentinel"
    script_cm = f"{name}-sentinel-script"

    replicas_string = str(replicas-1) #to get index of last replica
    redis_master_fqdn = f"{name}-{replicas_string}.{redis_svc}.{namespace}.svc.cluster.local"


    kubernetes.config.load_incluster_config()
    core = kubernetes.client.CoreV1Api()
    apps = kubernetes.client.AppsV1Api()

    # --- Redis Headless Service ---
    svc = V1Service(
        metadata=V1ObjectMeta(name=redis_svc),
        spec=V1ServiceSpec(
            cluster_ip="None",
            selector=labels,
            ports=[V1ServicePort(port=6379, target_port=6379)]
        )
    )
    try:
        core.create_namespaced_service(namespace, svc)
    except ApiException as e:
        if e.status != 409:
            raise

    # --- Redis StatefulSet ---
    
    # Define the PVC template
    pvc = V1PersistentVolumeClaim(
        metadata=V1ObjectMeta(name="redis-data"),
        spec=V1PersistentVolumeClaimSpec(
            access_modes=["ReadWriteOnce"],
            resources=V1ResourceRequirements(
                requests={"storage": "0.25Gi"}
            )
        )
    )


    redis_container = V1Container(
        name="redis",
        image=f"redis:{version}",
        volume_mounts = [
            V1VolumeMount(name="redis-data", mount_path="/data")
        ],
        ports=[V1ContainerPort(container_port=6379)],
        env=[{
            "name": "POD_NAME",
            "valueFrom": {
                "fieldRef": {
                    "fieldPath": "metadata.name"
                }
            }
        }],
        command=[
            "sh", "-c",
            f'''
            INDEX=$(echo $POD_NAME | grep -oE "[0-9]+$")
            if [ "$INDEX" = "0" ]; then
            echo "[INFO] Starting as master"
            exec redis-server --dir /data --appendonly yes --appendfilename appendonly.aof
            elif [ "$INDEX" = "1" ]; then
            echo "[INFO] Starting replica-1 as replica of redis-0"
            exec redis-server --dir /data --appendonly yes --appendfilename appendonly.aof --slave-announce-ip {name}-1.{redis_svc}.{namespace}.svc.cluster.local  --replicaof {name}-0.{redis_svc}.{namespace}.svc.cluster.local 6379
            else
            echo "[INFO] Starting replica-2 as replica of redis-0"
            exec redis-server --dir /data --appendonly yes --appendfilename appendonly.aof --slave-announce-ip {name}-2.{redis_svc}.{namespace}.svc.cluster.local  --replicaof {name}-0.{redis_svc}.{namespace}.svc.cluster.local 6379
            fi
            '''
        ]
    )

    

    sts = V1StatefulSet(
        metadata=V1ObjectMeta(name=redis_sts),
        spec=V1StatefulSetSpec(
            service_name=redis_svc,
            replicas=replicas,
            volume_claim_templates=[pvc],
            selector=V1LabelSelector(match_labels=labels),
            template=V1PodTemplateSpec(
                metadata=V1ObjectMeta(labels=labels),
                spec=V1PodSpec(containers=[redis_container]),
                
            )
        )
    )
    try:
        apps.create_namespaced_stateful_set(namespace, sts)
    except ApiException as e:
        if e.status != 409:
            raise

    # --- Sentinel ConfigMap ---
    if sentinel_enabled:
        redis_master_host = f"{redis_sts}-0.{redis_svc}.{namespace}.svc.cluster.local"

        sentinel_config = f"""
port 26379
sentinel monitor mymaster {redis_master_host} 6379 {sentinel_replicas}
sentinel down-after-milliseconds mymaster 5000
sentinel failover-timeout mymaster 10000
sentinel parallel-syncs mymaster 1
sentinel resolve-hostnames yes
        """.strip()

        sentinel_entrypoint_script = f"""#!/bin/sh
CONFIG_SRC="/base/sentinel.conf"
CONFIG_DST="/data/sentinel.conf"

if [ ! -f "$CONFIG_DST" ]; then
  echo "[INFO] Copying base config to writable volume..."
  cp "$CONFIG_SRC" "$CONFIG_DST"
else
  echo "[INFO] Using existing sentinel config from data volume."
fi

exec redis-sentinel "$CONFIG_DST"
""".strip()

        config_map = V1ConfigMap(
            metadata=V1ObjectMeta(name=sentinel_cfg),
            data={"sentinel.conf": sentinel_config}

        )

        script_map = V1ConfigMap(
            metadata=V1ObjectMeta(name=script_cm),
            data={"sentinel-entrypoint.sh": sentinel_entrypoint_script}
        )

        try:
            core.create_namespaced_config_map(namespace, config_map)
            core.create_namespaced_config_map(namespace, script_map)
        except ApiException as e:
            if e.status != 409:
                raise

        # --- Sentinel Deployment ---
        sentinel_container = V1Container(
            name="sentinel",
            image="redis:7.2",
            command=["/bin/sh", "/entrypoint/sentinel-entrypoint.sh"],
            ports=[V1ContainerPort(container_port=26379)],
            volume_mounts=[
                {"name": "sentinel-data", "mountPath": "/data"},
                {"name": "base-config", "mountPath": "/base", "readOnly": True},
                {"name": "entrypoint-script", "mountPath": "/entrypoint", "readOnly": True}
            ]
        )
        init_container = {
            "name": "wait-for-master",
            "image": "busybox",
            "command": ["sh", "-c", f"until nslookup {redis_master_fqdn}; do echo waiting for redis master; sleep 2; done"]
        }

        # sentinel_deploy = V1Deployment(
        #     metadata=V1ObjectMeta(name=sentinel_deploy),
        #     spec=V1DeploymentSpec(
        #         replicas=sentinel_replicas,
        #         selector=V1LabelSelector(match_labels={"role": "sentinel", "app": name}),
        #         template=V1PodTemplateSpec(
        #             metadata=V1ObjectMeta(labels={"role": "sentinel", "app": name}),
        #             spec=V1PodSpec(
        #                 containers=[sentinel_container],
        #                 init_containers=[init_container],
                        
        #                 volumes=[
        #                     {"name": "sentinel-data", "emptyDir": {}},
        #                     {"name": "base-config", "configMap": {"name": sentinel_cfg}},
        #                     {"name": "entrypoint-script", "configMap": {
        #                         "name": script_cm,
        #                         "defaultMode": 0o755
        #                     }}
        #                 ]
        #             )
        #         )
        #     )
        # )

        # try:
        #     apps.create_namespaced_deployment(namespace, sentinel_deploy)
        # except ApiException as e:
        #     if e.status != 409:
        #         raise

        sentinel_headless_svc = V1Service(
            metadata=V1ObjectMeta(name=f"{sentinel_deploy}-svc"),
            spec=V1ServiceSpec(
                cluster_ip="None",
                selector={"app": name, "role": "sentinel"},
                ports=[V1ServicePort(port=26379, target_port=26379)]
            )
        )

        try:
            core.create_namespaced_service(namespace, sentinel_headless_svc)
        except ApiException as e:
            if e.status != 409:
                raise


        sentinel_sts = V1StatefulSet(
            metadata=V1ObjectMeta(name=sentinel_deploy),
            spec=V1StatefulSetSpec(
                service_name=f"{sentinel_deploy}-svc",  # create a headless svc with this name
                replicas=sentinel_replicas,
                selector=V1LabelSelector(match_labels={"app": name, "role": "sentinel"}),
                template=V1PodTemplateSpec(
                    metadata=V1ObjectMeta(labels={"app": name, "role": "sentinel"}),
                    spec=V1PodSpec(
                        containers=[sentinel_container],
                        init_containers=[init_container],
                        volumes=[
                            {"name": "sentinel-data", "emptyDir": {}},
                            {"name": "base-config", "configMap": {"name": sentinel_cfg}},
                            {"name": "entrypoint-script", "configMap": {
                                "name": script_cm,
                                "defaultMode": 0o755
                            }}
                        ]
                    )
                )
            )
        )

        try:
            apps.create_namespaced_stateful_set(namespace, sentinel_sts)
        except ApiException as e:
            if e.status != 409:
                raise
        # --- Sentinel CronJob ---
        #create_sentinel_reset_cronjob(name, namespace, sentinel_replicas)
        #create_sentinel_reset_deployment(name, namespace, sentinel_replicas)
    kopf.info(kwargs["meta"], reason="Created", message=f"Redis with {'Sentinel' if sentinel_enabled else 'no Sentinel'} created.")



@kopf.on.delete('mygroup.dev', 'v1', 'appconfigs')
def delete_redis(spec, name, namespace, **kwargs):
    sentinel_enabled = spec.get('sentinel', {}).get('enabled', False)

    redis_svc = f"{name}-svc"
    redis_sts = name
    sentinel_cfg = f"{name}-sentinel-config"
    sentinel_deploy = f"{name}-sentinel"
    script_cm = f"{name}-sentinel-script"
    sentinel_headless_svc = f"{sentinel_deploy}-svc"
    kubernetes.config.load_incluster_config()
    core = kubernetes.client.CoreV1Api()
    apps = kubernetes.client.AppsV1Api()
    sentinel_reset_deploy = f"{name}-sentinel-reset"
    # Delete Redis StatefulSet
    try:
        apps.delete_namespaced_stateful_set(name=redis_sts, namespace=namespace)
        print(f"✅ Deleted Redis StatefulSet: {redis_sts}")
    except kubernetes.client.exceptions.ApiException as e:
        if e.status != 404:
            raise

    # Delete Headless Service
    try:
        core.delete_namespaced_service(name=redis_svc, namespace=namespace)
        print(f"✅ Deleted Redis Service: {redis_svc}")
    except kubernetes.client.exceptions.ApiException as e:
        if e.status != 404:
            raise

    # If Sentinel was enabled, clean up its resources
    if sentinel_enabled:
        # try:
        #     apps.delete_namespaced_deployment(name=sentinel_reset_deploy, namespace=namespace)
        #     print(f"✅ Deleted Sentinel Deployment: {sentinel_reset_deploy}")
        # except kubernetes.client.exceptions.ApiException as e:
        #     if e.status != 404:
        #         raise
        try:
            apps.delete_namespaced_stateful_set(name=sentinel_deploy, namespace=namespace)
            print(f"✅ Deleted Redis Sentinel StatefulSet: {redis_sts}")
        except kubernetes.client.exceptions.ApiException as e:
            if e.status != 404:
                raise

        try:
            core.delete_namespaced_service(name=sentinel_headless_svc, namespace=namespace)
            print(f"✅ Deleted Redis Sentinel Service: {redis_svc}")
        except kubernetes.client.exceptions.ApiException as e:
            if e.status != 404:
                raise

        try:
            core.delete_namespaced_config_map(name=sentinel_cfg, namespace=namespace)
            print(f"✅ Deleted Sentinel ConfigMap: {sentinel_cfg}")
        except kubernetes.client.exceptions.ApiException as e:
            if e.status != 404:
                raise
        
        try:
            core.delete_namespaced_config_map(name=script_cm, namespace=namespace)
            print(f"✅ Deleted Sentinel ConfigMap: {script_cm}")
        except kubernetes.client.exceptions.ApiException as e:
            if e.status != 404:
                raise

    kopf.info(kwargs["meta"], reason="Deleted", message="Redis + Sentinel resources cleaned up.")