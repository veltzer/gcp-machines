#!/bin/bash -e
gcloud services list --filter="name:compute.googleapis.com"
gcloud services list --filter="name:datastore.googleapis.com"
