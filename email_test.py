#!/usr/bin/env python

import os
import base64
from email.mime.text import MIMEText

from googleapiclient.discovery import build
from google.oauth2 import service_account


# Replace with the path to your service account key file
SERVICE_ACCOUNT_FILE = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]

# Define the scopes needed for the Gmail API
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

# Load the credentials from the service account file
credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)

# Build the Gmail API service
service = build("gmail", "v1", credentials=credentials)

# Email information
to = "mark.veltzer@gmail.com"  # Replace with the recipient"s email address
subject = "Hello from Machines!"
body = "This is a test email sent from a Python script using the Gmail API."

# Create the message
message = MIMEText(body)
message["to"] = to
message["subject"] = subject

# Encode the message as base64
encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()

# Create the message body
create_message = {
    "raw": encoded_message
}

# Send the email
try:
    message = (service.users().messages().send(userId="me", body=create_message).execute())
    print(f"Message Id: {message["id"]}")
except Exception as error:
    print(f"An error occurred: {error}")
