#!/bin/bash -e

resion="us-central1-c"
region="us-east1-b"
 
for x in 13 14 15 16 17 18 19 20 21 22 23 24
do
	gcloud compute instances create instance-$x --project=veltzer-machines-id --zone=${region} --machine-type=e2-standard-2 --network-interface=network-tier=PREMIUM,stack-type=IPV4_ONLY,subnet=default --metadata=ssh-keys=ubuntu:ssh-ed25519\ AAAAC3NzaC1lZDI1NTE5AAAAIGiKGBEC/5wO2g59ArrMAbEWAkP1pVdp1JrBs\+tkXW1T\ ubuntu@gcp --maintenance-policy=MIGRATE --provisioning-model=STANDARD --service-account=57950378250-compute@developer.gserviceaccount.com --scopes=https://www.googleapis.com/auth/devstorage.read_only,https://www.googleapis.com/auth/logging.write,https://www.googleapis.com/auth/monitoring.write,https://www.googleapis.com/auth/service.management.readonly,https://www.googleapis.com/auth/servicecontrol,https://www.googleapis.com/auth/trace.append --create-disk=auto-delete=yes,boot=yes,device-name=instance-template-machines,image=projects/ubuntu-os-cloud/global/images/ubuntu-2204-jammy-v20240720,mode=rw,size=10,type=projects/veltzer-machines-id/zones/us-central1-a/diskTypes/pd-balanced --no-shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring --labels=goog-ec-src=vm_add-gcloud --reservation-affinity=any
done
