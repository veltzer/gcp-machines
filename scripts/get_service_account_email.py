#!/usr/bin/env python

"""
This script retrieves the email of your current service
"""

import google.auth

credentials, project_id = google.auth.default()
print(credentials.service_account_email)
