#!/bin/bash -eu

echo "This script will delete ALL running Compute Engine instances in your project."
read -p "Are you sure you want to proceed? (y/n): " CONFIRM

if [[ "$CONFIRM" != "y" ]]
then
  echo "Exiting script."
  exit 0
fi

# Get all zones in the current project
# ZONES=$(gcloud compute zones list --format="value(name)")
ZONES=("us-central1-c us-east1-b us-central1-a us-west2-a")

# Delete instances in each zone
for ZONE in $ZONES; do
    # Get all instances in the zone (filtering out already stopped instances)
    # INSTANCES=$(gcloud compute instances list --zones=$ZONE --filter="status != TERMINATED" --format="value(name)" --verbosity=error)
    INSTANCES=$(gcloud compute instances list --zones=$ZONE --format="value(name)" --verbosity=error)

    for INSTANCE in $INSTANCES
    do
        echo "Deleting instance: $INSTANCE in zone: $ZONE"
        gcloud compute instances delete "$INSTANCE" --zone="$ZONE" --quiet
    done
done

echo "All running Compute Engine instances in your project have been stopped."
