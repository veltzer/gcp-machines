if PROJECT_ID=$(pygooglecloud get_project_id); then
	export PROJECT_ID
	export GOOGLE_APPLICATION_CREDENTIALS="${HOME}/.credentials/${PROJECT_ID}.json"
	pygooglecloud check_credentials >/dev/null
fi
