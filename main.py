# -*- coding: utf-8 -*-
#!/usr/bin/env python
import requests
import time
import jwt
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "modloaders"))
import update_fabric_event_data
import update_forge_event_data
import update_neoforge_event_data

def main():
	print("Starting the Python automated workflow.")

	MCM_APP_ID = os.environ.get("MCM_APP_ID")
	MCM_INSTALLATION_ID = os.environ.get("MCM_INSTALLATION_ID")
	MCM_APP_PRIVATE_KEY = os.environ.get("MCM_APP_PRIVATE_KEY")

	if not (MCM_APP_ID and MCM_APP_PRIVATE_KEY and MCM_INSTALLATION_ID):
		raise RuntimeError("Missing GitHub App environment variables!")

	# Create JWT for the app (valid 10 minutes)
	now = int(time.time())
	payload = {"iat": now, "exp": now + 600, "iss": int(MCM_APP_ID)}
	jwt_token = jwt.encode(payload, MCM_APP_PRIVATE_KEY.replace("\\n", "\n"), algorithm="RS256")

	# Exchange JWT for installation token
	token_url = f"https://api.github.com/app/installations/{MCM_INSTALLATION_ID}/access_tokens"
	resp = requests.post(token_url, headers={
		"Authorization": f"Bearer {jwt_token}",
		"Accept": "application/vnd.github+json"
	})
	resp.raise_for_status()
	gh_api_token = resp.json()["token"]
	response_headers = {"Authorization": f"token {gh_api_token}"}


	rootpath = os.path.dirname(sys.argv[0])

	update_fabric_event_data.main(rootpath, response_headers)
	update_forge_event_data.main(rootpath, response_headers)
	update_neoforge_event_data.main(rootpath, response_headers)

	return

if __name__ == "__main__":
	main()