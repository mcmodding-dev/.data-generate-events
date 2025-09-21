# -*- coding: utf-8 -*-
#!/usr/bin/env python
from datetime import datetime
import os
import re
import requests
import json

sep = os.path.sep

def naturalsort(l):
	convert = lambda text: int(text) if text.isdigit() else text.lower()
	alphanum_key = lambda key: [convert(c) for c in re.split('([0-9]+)', key)]
	return sorted(l, key=alphanum_key)

def main(mainpath, response_headers):
	rootpath = "." + sep + "modloaders" # For production
	if os.environ['IS_PRODUCTION'] == "false":
		rootpath = mainpath + sep + "modloaders" # For dev

	modloader = "forge"
	org_name = "MinecraftForge"
	repo_name = "MinecraftForge"

	print(f"Starting to fetch {repo_name} events.\n")

	print("Path:", rootpath)

	branches_api_url = f"https://api.github.com/repos/{org_name}/{repo_name}/branches"

	root_event_src_folder = f"src/main/java/net/{org_name.lower()}/event"
	retro_root_event_src_folder = f"common/net/{org_name.lower()}/event"

	# Fetch all pages
	branches_data = []
	page = 1
	while True:
		response = requests.get(
			branches_api_url,
			headers=response_headers,
			params={"per_page": 100, "page": page}
		)
		response.raise_for_status()
		data = response.json()
		if not data:
			break
		branches_data.extend(data)
		page += 1

	branches = []
	for branch in [branch["name"] for branch in branches_data]:
		if not branch[0].isdigit() or "." not in branch:
			continue
		branches.append(branch)
	branches = naturalsort(branches)

	print("\nRunning for the branches:", branches)

	for branch in branches:
		print("\nProcessing branch:", branch)

		branch_json_file_path = rootpath + sep + "data" + sep + modloader + sep + branch + ".json"

		branch_api_url = branches_api_url + "/" + branch
		branch_response = requests.get(branch_api_url, headers=response_headers)
		branch_response.raise_for_status()
		branch_data = branch_response.json()

		last_commit_sha = branch_data["commit"]["sha"]
		last_commit_url = branch_data["commit"]["url"]

		commit_response = requests.get(last_commit_url, headers=response_headers)
		commit_response.raise_for_status()
		commit_data = commit_response.json()

		commit_message = commit_data["commit"]["message"]
		current_commit_identifier = "[" + last_commit_sha + "] " + commit_message

		if os.path.exists(branch_json_file_path):
			with open(branch_json_file_path, "r") as f:
				if json.load(f).get("last_commit") == current_commit_identifier:
					print(f"[{branch}] Already up-to-date, skipping.")
					continue

		branch_events = {}

		def walk(cur_branch, path):
			api_url = f"https://api.github.com/repos/{org_name}/{repo_name}/contents/{path}?ref={cur_branch}"
			branch_file_response = requests.get(api_url, headers=response_headers)
			branch_file_response.raise_for_status()
			for item in branch_file_response.json():
				if item["type"] == "file":
					print(f"[{branch}] Fetching {item['path']}.")

					item_path = item["path"]
					file_url = item["download_url"]
					blob_url = file_url.replace(
						"https://raw.githubusercontent.com/",
						"https://github.com/"
					).replace(f"/{branch}/", f"/blob/{branch}/")

					name = item_path.split("/")[-1].replace(".java", "").strip()
					if not name.endswith("Event"):
						continue

					text = requests.get(file_url).text

					print(f"[{branch}] Processing " + name)

					package = ""

					skipped_first = False
					for line in text.split("\n"):
						if "package " in line:
							package = line.replace("package", "").replace(";", "").strip()
							if package.startswith("/*"):
								break

							if package not in branch_events:
								branch_events[package] = []

							branch_events[package].append({"event": name, "url": blob_url})

						if " extends " in line and "class " in line:
							if not skipped_first:
								skipped_first = True
								continue

							class_name = line.split("class")[1].split()[0].strip()

							if "extends" in line:
								base_class = line.split("extends")[1].split()[0].strip()
							else:
								base_class = ""

							if "<" in class_name or "<" in base_class:
								continue

							if base_class == "Event" or "." in base_class:
								continue

							sub_event = f"{base_class}.{class_name}" if base_class else class_name

							if package == "":
								raise RuntimeError(f"[{branch}] Package not found for " + item_path + "!")

							branch_events[package].append({"event": sub_event, "url": blob_url})

				elif item["type"] == "dir":
					walk(cur_branch, item["path"])

		if branch == "1.6":
			walk(branch, retro_root_event_src_folder)
		else:
			walk(branch, root_event_src_folder)

		print("Parsed:", sum(len(v) for v in branch_events.values()), "events for branch", branch)

		branch_data_out = { "lastupdated" : datetime.now().strftime("%Y%m%d%H%M%S"), "version" : branch, "last_commit" : current_commit_identifier, "data" : branch_events }

		with open(branch_json_file_path, "w", encoding="utf-8") as events_file:
			events_file.write(json.dumps(branch_data_out, indent=4))

	branches_dict = { "branches" : list(reversed(branches)) }

	with open(rootpath + sep + "data" + sep + modloader + sep + "branches.json", "w", encoding="utf-8") as branches_file:
		branches_file.write(json.dumps(branches_dict, indent=4))

	print("\nBranches processed:", branches)
	print("Finished.")
