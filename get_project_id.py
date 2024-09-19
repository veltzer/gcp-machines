#!/usr/bin/env python

"""
Print the current project name
"""


import google.auth

_, project_id = google.auth.default()
print(project_id)
