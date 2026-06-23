#!/usr/bin/env python

"""
This script retrieves the email of your current service
"""

import google.auth

credentials, project_id = google.auth.default()
# service_account_email only exists on service-account credentials, not on
# the base Credentials type, so access it defensively.
print(getattr(credentials, "service_account_email", None))
