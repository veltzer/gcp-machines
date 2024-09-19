from google.cloud import compute_v1
from google.cloud import service_usage_v1
import google.auth

def get_instance_limits_per_region(project_id: str) -> None:

    compute_client = compute_v1.RegionsClient() 
    service_usage_client = service_usage_v1.ServiceUsageClient()

    for region in compute_client.list():
        region_name = region.name
        print(region_name)
        sys.exit(1)
        service = 'compute.googleapis.com'
        parent = f'projects/{project_id}'
        filter_str = f'metric.name="compute.googleapis.com/cpus" AND metric.labels.region="{region_name}"'

        quotas = service_usage_client.list_consumer_quota_metrics(
            parent=parent,
            filter=filter_str,
        )

        for quota in quotas:
            limit = quota.metric.consumer_quota_limits[0].value
            print(f"Region: {region_name}, Instance Limit (CPUs): {limit}")

if __name__ == "__main__":
    _, project_id = google.auth.default() 
    get_instance_limits_per_region(project_id)
