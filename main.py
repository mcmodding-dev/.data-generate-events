# -*- coding: utf-8 -*-
#!/usr/bin/env python
from datetime 						import datetime
import requests
import time
import jwt
import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "script"))
import UpdateFabricEventData
import UpdateForgeEventData
import UpdateNeoForgeEventData

def main():
	print("Starting the Python automated workflow.")

	MCM_APP_ID = os.environ.get("MCM_APP_ID")
	MCM_INSTALLATION_ID = os.environ.get("MCM_INSTALLATION_ID")
	MCM_APP_PRIVATE_KEY = os.environ.get("MCM_APP_PRIVATE_KEY")

	if not (MCM_APP_ID and MCM_APP_PRIVATE_KEY and MCM_INSTALLATION_ID):
		raise RuntimeError("Missing GitHub App environment variables!")

	now = int(time.time())
	payload = {"iat": now, "exp": now + 600, "iss": int(MCM_APP_ID)}
	jwtToken = jwt.encode(payload, MCM_APP_PRIVATE_KEY.replace("\\n", "\n"), algorithm="RS256")

	tokenUrl = f"https://api.github.com/app/installations/{MCM_INSTALLATION_ID}/access_tokens"
	resp = requests.post(tokenUrl, headers={
		"Authorization": f"Bearer {jwtToken}",
		"Accept": "application/vnd.github+json"
	})

	resp.raise_for_status()
	ghApiToken = resp.json()["token"]
	responseHeaders = {"Authorization": f"token {ghApiToken}"}

	rootPath = os.path.dirname(sys.argv[0])

	UpdateFabricEventData.main(rootPath, responseHeaders)
	UpdateForgeEventData.main(rootPath, responseHeaders)
	UpdateNeoForgeEventData.main(rootPath, responseHeaders)

	dataRootPath = "." if os.environ.get('IS_PRODUCTION') != "false" else rootPath
	lastRunPath = os.path.join(dataRootPath, "data", "script", "run.json")
	os.makedirs(os.path.dirname(lastRunPath), exist_ok=True)
	with open(lastRunPath, "w", encoding="utf-8") as f:
		f.write(json.dumps({"last_run": datetime.now().strftime("%Y%m%d%H%M%S")}, indent=4))

	print("\nWrote run.json data.")

	return

if __name__ == "__main__":
	main()
