#!/usr/bin/env python

from google.cloud import compute_v1
from google.cloud import service_usage_v1
from google.cloud import cloudquotas_v1
import google.auth

REGIONS=["us-central1", "us-east1"]

def get_instance_limits_per_region():
    _, project_id = google.auth.default()

    if not project_id:
        raise ValueError("Project ID not found. Check your GCP credentials.")

    client = cloudquotas_v1.CloudQuotasClient()
    service_usage_client = service_usage_v1.ServiceUsageClient()

    if REGIONS:
        regions_list = REGIONS
    else:
        regions_list = [x.name for x in client.list(project=project_id)]
    for region in regions_list:
        print(region)
        service_name = 'compute.googleapis.com'

        # request = service_usage_v1.GetServiceRequest(
        #     name=f'projects/{project_id}/services/{service_name}'
        # )
        response = client.get_consumer_quota_metric(name=name)
        print(response.quota_limits)
        import sys
        sys.exit(1)
        # service = service_usage_client.get_service(request=request)
        print(dir(service.config.quota))
        # Access quota information using service.config.quota
        for quota in service.config.quota.metric_rules:
            print(quota)
            if quota.metric == 'compute.googleapis.com/cpus' and 'region' in quota.metric_costs:
                limit = quota.metric_costs['region'][region]
                print(f"Region: {region_name}, Instance Limit (CPUs): {limit}")

if __name__ == "__main__":
    get_instance_limits_per_region()
