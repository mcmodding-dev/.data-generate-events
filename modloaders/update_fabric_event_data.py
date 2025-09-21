# -*- coding: utf-8 -*-
#!/usr/bin/env python
from datetime import datetime
import os
import re
import requests
import json
import zipfile
import io

sep = os.path.sep

def naturalsort(l):
	convert = lambda text: int(text) if text.isdigit() else text.lower()
	alphanum_key = lambda key: [convert(c) for c in re.split('([0-9]+)', key)]
	return sorted(l, key=alphanum_key)

def to_upper_snake_case(s):
	snake = re.sub(r'(?<!^)(?=[A-Z])', '_', s)
	return snake.upper()

def main(mainpath, response_headers):
	rootpath = "." + sep + "modloaders" # For production
	if os.environ['IS_PRODUCTION'] == "false":
		rootpath = mainpath + sep + "modloaders" # For dev

	modloader = "fabric"
	org_name = "FabricMC"
	repo_name = "fabric"

	print(f"Starting to fetch {repo_name} events.\n")

	print("Path:", rootpath)

	branches_api_url = f"https://api.github.com/repos/{org_name}/{repo_name}/branches"

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

		zip_url = f"https://github.com/{org_name}/{repo_name}/archive/refs/heads/{branch}.zip"
		print(f"[{branch}] Downloading zip {zip_url}")
		zip_response = requests.get(zip_url, headers=response_headers)
		zip_response.raise_for_status()

		with zipfile.ZipFile(io.BytesIO(zip_response.content)) as z:
			for file_path in z.namelist():
				if not file_path.endswith(".java"):
					continue

				with z.open(file_path) as f:
					try:
						text = f.read().decode("utf-8")
					except UnicodeDecodeError:
						continue

				if "net.fabricmc.fabric.api.event.Event" not in text:
					continue

				module = file_path.split("/")[1].strip()  # skip top-level folder
				file_name = os.path.basename(file_path)
				name = file_name.replace(".java", "").strip()
				relative_path = "/".join(file_path.split("/")[1:])  # strip top-level
				file_url = f"https://github.com/{org_name}/{repo_name}/blob/{branch}/{relative_path}"

				print(f"[{branch}] Processing {name}")

				callback_names = {}

				is_class = "public class " in text or "public final class " in text

				has_factory = "EventFactory;" in text
				package = ""

				active_event = ""
				active_variable = ""
				found_event = False
				for line in text.split("\n"):
					if "package " in line:
						package = line.replace("package", "").replace(";", "").strip()
						if package.startswith("/*"):
							break

					if "Event<" in line or ("EventFactory." in line and ">" in line):
						if not "private " in line and "createArrayBacked" in line:
							found_event = True

						match = re.search(
							r'public\s+static\s+final\s+Event<[^.]+\.(\w+)>\s+(\w+)\s*=',
							line
						)
						if match:
							callback = match.group(1)  # e.g., "Load"
							variable = match.group(2)  # e.g., "BLOCK_ENTITY_LOAD"
							callback_names[callback] = variable

					if " interface " in line:
						possible_active_event = line.split("interface ")[1].split("{")[0].strip()
						if possible_active_event != "method":
							active_event = possible_active_event
						continue

					if active_event != "":
						if is_class:
							if not found_event:
								continue

							if "*" in line or ";" not in line:
								continue

							function = line.strip()

							if module not in branch_events:
								branch_events[module] = []

							variable = to_upper_snake_case(active_event)
							if active_event in callback_names:
								variable = callback_names[active_event]

							branch_events[module].append({
								"file" : file_name,
								"package": package,
								"interface": active_event,
								"variable": variable,
								"function": function,
								"url": file_url
							})

							active_event = ""
						else:
							if ">" in line:
								if has_factory:
									if "EventFactory." not in line:
										continue
								else:
									if "Event<" not in line:
										continue
								active_variable = line.split(">")[1].split("=")[0].strip()
								continue

							if active_variable == "":
								continue

							if "*" in line or ";" not in line or "(" not in line:
								continue

							if line.startswith("		"):
								continue

							if "return " in line:
								continue

							function = line.strip()
							if module not in branch_events:
								branch_events[module] = []

							branch_events[module].append({
								"file" : file_name,
								"package": package,
								"interface": active_event,
								"variable": active_variable,
								"function": function,
								"url": file_url
							})

							active_event = ""
							active_variable = ""

		print("Parsed:", sum(len(v) for v in branch_events.values()), "events for branch", branch)

		branch_data_out = {
			"lastupdated": datetime.now().strftime("%Y%m%d%H%M%S"),
			"version": branch,
			"last_commit": current_commit_identifier,
			"data": branch_events
		}

		with open(branch_json_file_path, "w", encoding="utf-8") as events_file:
			events_file.write(json.dumps(branch_data_out, indent=4))


	branches_dict = {"branches": list(reversed(branches))}
	with open(rootpath + sep + "data" + sep + modloader + sep + "branches.json", "w", encoding="utf-8") as branches_file:
		branches_file.write(json.dumps(branches_dict, indent=4))

	print("\nBranches processed:", branches)
	print("Finished.")
