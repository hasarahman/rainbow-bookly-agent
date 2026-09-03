from google_auth_oauthlib.flow import InstalledAppFlow

CLIENT_SECRETS_FILE = "/Users/hasanrahman/dcg/credentials/google_oauth_client.json"
TOKEN_FILE = "/Users/hasanrahman/dcg/credentials/google_token.json"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def main():
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
    creds = flow.run_local_server(port=0)
    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())
    print(f"Saved token to {TOKEN_FILE}")


if __name__ == "__main__":
    main()
