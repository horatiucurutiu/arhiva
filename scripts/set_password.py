#!/usr/bin/env python3
"""Set the Numa Film Archive login credentials.

Prompts for a password on stdin, hashes it with bcrypt, and writes the
hash into config.ini's [Auth] section (creating the section if needed).
The plaintext password is never written to disk or logged.
"""
import argparse
import configparser
import getpass
import os
import secrets
import sys

import bcrypt

DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config.ini")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def write_credentials(config_path: str, username: str, password: str) -> None:
    config = configparser.ConfigParser()
    config.read(config_path)
    if not config.has_section("Auth"):
        config.add_section("Auth")
    config.set("Auth", "USERNAME", username)
    config.set("Auth", "PASSWORD_HASH", hash_password(password))
    if not config.has_option("Auth", "SECRET_KEY") or not config.get("Auth", "SECRET_KEY"):
        config.set("Auth", "SECRET_KEY", secrets.token_hex(32))
    if not config.has_option("Auth", "SECURE_COOKIES"):
        config.set("Auth", "SECURE_COOKIES", "true")
    with open(config_path, "w") as f:
        config.write(f)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default="admin")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    args = parser.parse_args()

    password = getpass.getpass("New password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords do not match.", file=sys.stderr)
        sys.exit(1)
    if len(password) < 8:
        print("Password must be at least 8 characters.", file=sys.stderr)
        sys.exit(1)

    write_credentials(args.config, args.username, password)
    print(f"Password set for user '{args.username}' in {args.config}")


if __name__ == "__main__":
    main()
