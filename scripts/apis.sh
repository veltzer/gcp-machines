#!/bin/bash -e
gcloud services list --filter="name:compute.googleapis.com"
gcloud services list --filter="name:datastore.googleapis.com"
gcloud services list --filter="name:gmail.googleapis.com"

# gcloud services list --enabled
