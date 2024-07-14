#!/usr/bin/env python

import argparse
from google.cloud import datastore
import bcrypt

def init_datastore_client():
    return datastore.Client()

def list_users(client):
    query = client.query(kind='User')
    users = list(query.fetch())
    for user in users:
        print(f"Username: {user['username']}")

def add_user(client, username, password):
    key = client.key('User', username)
    entity = datastore.Entity(key=key)
    hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
    entity.update({
        'username': username,
        'password': hashed_password
    })
    client.put(entity)
    print(f"User {username} added successfully.")

def delete_user(client, username):
    key = client.key('User', username)
    client.delete(key)
    print(f"User {username} deleted successfully.")

def main():
    parser = argparse.ArgumentParser(description='Interact with Google Cloud Datastore')
    parser.add_argument('action', choices=['list', 'add', 'delete'], help='Action to perform')
    parser.add_argument('--username', help='Username for add or delete action')
    parser.add_argument('--password', help='Password for add action')

    args = parser.parse_args()

    client = init_datastore_client()

    if args.action == 'list':
        list_users(client)
    elif args.action == 'add':
        if not args.username or not args.password:
            print("Username and password are required for add action")
            return
        add_user(client, args.username, args.password)
    elif args.action == 'delete':
        if not args.username:
            print("Username is required for delete action")
            return
        delete_user(client, args.username)

if __name__ == '__main__':
    main()
