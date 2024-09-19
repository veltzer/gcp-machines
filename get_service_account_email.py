#!/usr/bin/env python

"""
This script retrieves the email of your current service
"""

import os
import json

def get_service_account_email(credentials_file):
    with open(credentials_file, 'r') as f:
        data = json.load(f)
        return data['client_email']

def main():
    credentials_file = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
    email = get_service_account_email(credentials_file)
    print(email)

if __name__ == "__main__":
    main()
