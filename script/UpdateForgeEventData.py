# -*- coding: utf-8 -*-
#!/usr/bin/env python
from datetime 					import datetime
import os
import re
import json
import zipfile
import io

import Util

sep = os.path.sep

def main(mainPath, responseHeaders):
	rootPath = "." # For production
	if os.environ['IS_PRODUCTION'] == "false":
		rootPath = mainPath # For dev

	modloader = "forge"
	orgName = "MinecraftForge"
	repoName = "MinecraftForge"

	isCancellable = lambda line: bool(re.search(r'@Cancelable\b', line) or re.search(r'implements[^{]*\bCancellable\b', line))

	print(f"Starting to fetch {repoName} events.\n")
	print("Path:", rootPath)

	branchesApiUrl = f"https://api.github.com/repos/{orgName}/{repoName}/branches"

	forgePkg = f"net/{orgName.lower()}"
	retroRootEventSrcFolder = f"common/net/{orgName.lower()}/event"

	branchesData = Util.fetchAllBranches(branchesApiUrl, responseHeaders)

	branches = [b["name"] for b in branchesData if b["name"][0].isdigit() and "." in b["name"]]
	branches = Util.naturalSort(branches)

	os.makedirs(rootPath + sep + "data" + sep + modloader, exist_ok=True)

	print("\nRunning for the branches:", branches)

	for branch in branches:
		print("\nProcessing branch:", branch)

		branchJsonFilePath = rootPath + sep + "data" + sep + modloader + sep + branch + ".json"
		branchMinJsonFilePath = rootPath + sep + "data" + sep + modloader + sep + branch + ".min.json"

		branchApiUrl = branchesApiUrl + "/" + branch
		branchResponse = Util.fetchWithRetry(branchApiUrl, responseHeaders=responseHeaders)
		branchData = branchResponse.json()

		currentCommitIdentifier = Util.getCommitIdentifier(branchData, responseHeaders)

		if os.path.exists(branchJsonFilePath):
			with open(branchJsonFilePath, "r") as f:
				if json.load(f).get("last_commit") == currentCommitIdentifier:
					print(f"[{branch}] Already up-to-date, skipping.")
					continue

		branchEvents = {}

		zipUrl = f"https://github.com/{orgName}/{repoName}/archive/refs/heads/{branch}.zip"
		print(f"[{branch}] Downloading zip {zipUrl}")
		try:
			zipResponse = Util.fetchWithRetry(zipUrl, responseHeaders=responseHeaders)
		except Exception as e:
			print(f"[{branch}] Error downloading zip: {e}")
			continue

		with zipfile.ZipFile(io.BytesIO(zipResponse.content)) as z:
			for filePath in z.namelist():
				if not filePath.endswith("Event.java"):
					continue

				normalised = filePath.replace("\\", "/")
				if branch == "1.6":
					if "/" + retroRootEventSrcFolder + "/" not in "/" + normalised:
						continue
				else:
					if "/" + forgePkg + "/" not in "/" + normalised:
						continue

				try:
					with z.open(filePath) as f:
						try:
							text = f.read().decode("utf-8")
						except UnicodeDecodeError:
							continue
				except Exception as e:
					print(f"[{branch}] Error reading {filePath}: {e}")
					continue

				name = filePath.split("/")[-1].replace(".java", "").strip()
				relativePath = "/".join(filePath.split("/")[1:])
				blobUrl = f"https://github.com/{orgName}/{repoName}/blob/{branch}/{relativePath}"

				print(f"[{branch}] Processing {name}")

				for package, entry in Util.extractForgeEvents(text, name, blobUrl, isCancellable):
					if package not in branchEvents:
						branchEvents[package] = []
					branchEvents[package].append(entry)

		print("Parsed:", sum(len(v) for v in branchEvents.values()), "events for branch", branch)

		branchDataOut = {
			"lastupdated": datetime.now().strftime("%Y%m%d%H%M%S"),
			"version": branch,
			"last_commit": currentCommitIdentifier,
			"data": branchEvents
		}

		with open(branchJsonFilePath, "w", encoding="utf-8") as branchJsonFile:
			branchJsonFile.write(json.dumps(branchDataOut, indent=4))

		with open(branchMinJsonFilePath, "w", encoding="utf-8") as branchMinJsonFile:
			branchMinJsonFile.write(json.dumps(branchDataOut, separators=(',', ':')))

	branchesDict = {"branches": list(reversed(branches))}

	with open(rootPath + sep + "data" + sep + modloader + sep + "branches.json", "w", encoding="utf-8") as branchesFile:
		branchesFile.write(json.dumps(branchesDict, indent=4))

	with open(rootPath + sep + "data" + sep + modloader + sep + "branches.min.json", "w", encoding="utf-8") as branchesMinFile:
		branchesMinFile.write(json.dumps(branchesDict, separators=(',', ':')))

	print("\nBranches processed:", branches)
	print("Finished.")
