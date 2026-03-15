#!/usr/bin/env python3
"""
manage_users.py
CLI tool for managing API server users.

Usage:
  python3 manage_users.py add <username>         # Add user (prompts for password)
  python3 manage_users.py remove <username>       # Remove user
  python3 manage_users.py list                    # List all users
  python3 manage_users.py passwd <username>       # Change password
"""

import json
import sys
import getpass
from pathlib import Path

import bcrypt

USERS_FILE = str(Path(__file__).parent / 'users.json')


def load_users():
    try:
        with open(USERS_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_users(users):
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f, indent=2)


def hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def prompt_password():
    while True:
        p1 = getpass.getpass('Password: ')
        if len(p1) < 8:
            print('Password must be at least 8 characters.')
            continue
        p2 = getpass.getpass('Confirm:  ')
        if p1 != p2:
            print('Passwords do not match.')
            continue
        return p1


def cmd_add(username):
    users = load_users()
    if username in users:
        print(f'User "{username}" already exists. Use "passwd" to change password.')
        return
    password = prompt_password()
    users[username] = {'password': hash_password(password)}
    save_users(users)
    print(f'User "{username}" created.')


def cmd_remove(username):
    users = load_users()
    if username not in users:
        print(f'User "{username}" not found.')
        return
    confirm = input(f'Remove user "{username}"? [y/N] ').strip().lower()
    if confirm != 'y':
        print('Cancelled.')
        return
    del users[username]
    save_users(users)
    print(f'User "{username}" removed.')


def cmd_list():
    users = load_users()
    if not users:
        print('No users configured. Run: python3 manage_users.py add <username>')
        return
    print(f'{len(users)} user(s):')
    for name in sorted(users):
        print(f'  - {name}')


def cmd_passwd(username):
    users = load_users()
    if username not in users:
        print(f'User "{username}" not found.')
        return
    password = prompt_password()
    users[username]['password'] = hash_password(password)
    save_users(users)
    print(f'Password updated for "{username}".')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == 'list':
        cmd_list()
    elif cmd == 'add' and len(sys.argv) == 3:
        cmd_add(sys.argv[2])
    elif cmd == 'remove' and len(sys.argv) == 3:
        cmd_remove(sys.argv[2])
    elif cmd == 'passwd' and len(sys.argv) == 3:
        cmd_passwd(sys.argv[2])
    else:
        print(__doc__)
        sys.exit(1)
