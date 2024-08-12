#!/bin/bash -e
#!/bin/bash

# Ensure the Google Cloud SDK is installed and configured
# If not, you'll need to install and set it up first
gcloud compute instances list \
  --format="table(networkInterfaces[0].accessConfigs[0].natIP, labels)"
