#!/usr/bin/env python

from googleapiclient.discovery import build

def list_gcp_quotas():
    """
    Lists all quota types in Google Cloud Platform (GCP).
    """
    service = build('compute', 'v1')
    
    try:
        quota_types = service.projects().get(project='veltzer-machines-id').execute()['quotas']
        
        for quota in quota_types:
            print(f"Quota type: {quota['metric']}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    list_gcp_quotas()
