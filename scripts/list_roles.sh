#!/bin/bash -eu

PROJECT_ID=$(./get_project_id.py)
# echo "PROJECT_ID is ${PROJECT_ID}"
EMAIL=$(./get_service_account_email.py)
# echo "EMAIL is ${EMAIL}"
gcloud projects get-iam-policy "${PROJECT_ID}"\
	--flatten="bindings[].members"\
	--format="table(bindings.role)"\
	--filter="bindings.members:serviceAccount:${EMAIL}"\
	| awk "NR>1" | sort -u
