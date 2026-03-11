"""
ERPlite - First Time Setup
Run this script once before starting the application for the first time.
"""
import getpass
import os
from werkzeug.security import generate_password_hash

def setup():
    print("""
╔════════════════════════════════════════════════════════╗
║              Welcome to ERPlite Setup                  ║
║         Run this once before first launch              ║
╚════════════════════════════════════════════════════════╝
""")

    # ── Company name ──────────────────────────────────────
    company_name = input("Company name: ").strip()
    if not company_name:
        company_name = "ERPlite"
        print(f"No name entered, defaulting to '{company_name}'")

    # ── Admin username ────────────────────────────────────
    admin_username = input("Admin username: ").strip()
    if not admin_username:
        admin_username = "admin"
        print(f"No username entered, defaulting to '{admin_username}'")

    # ── Admin password ────────────────────────────────────
    while True:
        password = getpass.getpass("Create admin password: ")
        if len(password) < 8:
            print("Password must be at least 8 characters. Try again.")
            continue
        confirm = getpass.getpass("Confirm admin password: ")
        if password == confirm:
            break
        print("Passwords do not match. Try again.")

    hashed_password = generate_password_hash(password)

    # ── Write to .env ─────────────────────────────────────
    env_path = os.path.join(os.path.dirname(__file__), '.env')

    # Read existing .env if it exists so we don't overwrite db settings
    existing_lines = []
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            existing_lines = [
                line for line in f.readlines()
                if not line.startswith('COMPANY_NAME=')
                and not line.startswith('ADMIN_USERNAME=')
                and not line.startswith('ADMIN_PASSWORD_HASH=')
            ]

    with open(env_path, 'w') as f:
        f.writelines(existing_lines)
        f.write(f"\nCOMPANY_NAME={company_name}\n")
        f.write(f"ADMIN_USERNAME={admin_username}\n")
        f.write(f"ADMIN_PASSWORD_HASH={hashed_password}\n")

    print(f"""
Setup complete!
---------------
Company : {company_name}
Username: {admin_username}
Password: saved securely

Your .env file has been updated. You can now start the app with:
    python app.py
""")

if __name__ == '__main__':
    setup()
