# -*- coding: utf-8 -*-
#!/usr/bin/env python
from datetime 					import datetime
import os
import re
import requests
import json
import zipfile
import io

import Util

sep = os.path.sep

def main(mainPath, responseHeaders):
	rootPath = "." # For production
	if os.environ['IS_PRODUCTION'] == "false":
		rootPath = mainPath # For dev

	modloader = "fabric"
	orgName = "FabricMC"
	repoName = "fabric"

	print(f"Starting to fetch {repoName} events.\n")

	print("Path:", rootPath)

	branchesApiUrl = f"https://api.github.com/repos/{orgName}/{repoName}/branches"

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

		currentCommitIdentifier, commitDate = Util.getCommitIdentifier(branchData, responseHeaders)

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
				if not filePath.endswith(".java"):
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

				if "net.fabricmc.fabric.api.event.Event" not in text:
					continue

				pathParts = filePath.split("/")
				module = pathParts[2].strip() if pathParts[1].strip() == "deprecated" else pathParts[1].strip()
				fileName = os.path.basename(filePath)
				name = fileName.replace(".java", "").strip()
				relativePath = "/".join(filePath.split("/")[1:])
				fileUrl = f"https://github.com/{orgName}/{repoName}/blob/{branch}/{relativePath}"
				if "/client/" in relativePath:
					fileSide = "client"
				elif "/server/" in relativePath:
					fileSide = "server"
				elif name.startswith("Server") or name.startswith("Dedicated"):
					fileSide = "server"
				elif name.startswith("Client"):
					fileSide = "client"
				else:
					fileSide = None

				print(f"[{branch}] Processing {name}")

				isClass = "public class " in text or "public final class " in text
				hasFactory = "EventFactory;" in text

				linesList = text.split("\n")

				callbackNames = {}
				callbackMeta = {}

				interfaceDescs = {}

				package = ""
				pendingJavadoc = []
				inJavadoc = False
				inJavadocPre = False
				pendingEnvSide = None
				pendingDeprecated = False

				activeEvent = ""
				activeEventLineno = 0
				activeEventDesc = ""
				activeEventSide = None
				activeEventDeprecated = False
				activeEventFallbackDesc = ""
				activeVariable = ""
				activeVariableLineno = 0
				foundEvent = False

				for lineno, line in enumerate(linesList, start=1):
					stripped = line.strip()

					if "/**" in stripped:
						inJavadoc = True
						inJavadocPre = False
						pendingJavadoc = []
						continue

					if inJavadoc:
						if "*/" in stripped:
							inJavadoc = False
							inJavadocPre = False
						else:
							clean = re.sub(r'^\s*\*\s?', '', line)
							if inJavadocPre:
								if re.search(r'</pre>', clean, re.IGNORECASE):
									inJavadocPre = False
								pendingJavadoc.append(clean.rstrip())
							else:
								clean = clean.strip()
								if clean:
									pendingJavadoc.append(clean)
								if re.search(r'<pre\b', clean, re.IGNORECASE):
									inJavadocPre = True
						continue

					if "@Environment" in stripped and "EnvType" in stripped:
						if "CLIENT" in stripped:
							pendingEnvSide = "client"
						elif "SERVER" in stripped:
							pendingEnvSide = "server"
						continue

					if "@Deprecated" in stripped:
						pendingDeprecated = True
						continue

					if "package " in stripped and not package:
						pkg = stripped.replace("package", "", 1).replace(";", "").strip()
						if not pkg.startswith("/*"):
							package = pkg
						continue

					if "Event<" in line or ("EventFactory." in line and ">" in line):
						if not "private " in line and "createArrayBacked" in line:
							foundEvent = True

						m = re.search(
							r'public\s+static\s+final\s+Event<(?:[^>]*\.)?(\w+)>\s+(\w+)\s*=',
							line
						)
						if m:
							iface = m.group(1)
							var = m.group(2)
							callbackNames[iface] = {
								"variable": var,
								"var_lineno": lineno,
								"desc": Util.extractDescriptionWithDeprecated(pendingJavadoc),
							}
						else:
							m_method = re.search(r'Event<(?:[^>]*\.)?(\w+)>\s+\w+\s*\(', line)
							if m_method:
								iface = m_method.group(1)
								desc = Util.extractDescriptionWithDeprecated(pendingJavadoc)
								if desc:
									interfaceDescs[iface] = desc

						if not isClass and activeEvent != "" and ">" in line:
							if (hasFactory and "EventFactory." in line) or (not hasFactory and "Event<" in line):
								activeVariable = line.split(">")[1].split("=")[0].strip()
								activeVariableLineno = lineno
								if not activeEventDesc:
									activeEventDesc = Util.extractDescriptionWithDeprecated(pendingJavadoc)
								activeEventDeprecated = activeEventDeprecated or pendingDeprecated
								pendingJavadoc = []
								pendingEnvSide = None
								pendingDeprecated = False
								continue

						pendingJavadoc = []
						pendingEnvSide = None
						pendingDeprecated = False
						continue

					if " interface " in line:
						possible = line.split("interface ")[1].split("{")[0].strip()
						if possible != "method":
							activeEvent = possible
							activeEventLineno = lineno
							activeEventDesc = Util.extractDescriptionWithDeprecated(pendingJavadoc) or interfaceDescs.get(possible, "")
							activeEventSide = pendingEnvSide or fileSide
							activeEventDeprecated = pendingDeprecated

							if not isClass and not activeEventDesc:
								activeEventFallbackDesc = Util.findFallbackDescription(linesList, lineno)
							else:
								activeEventFallbackDesc = ""

							if isClass:
								callbackMeta[activeEvent] = {
									"desc": activeEventDesc,
									"side": activeEventSide,
									"deprecated": activeEventDeprecated,
								}

						pendingJavadoc = []
						pendingEnvSide = None
						pendingDeprecated = False
						continue

					if activeEvent == "":
						continue

					if isClass:
						if not foundEvent:
							continue

						if "*" in line or ";" not in line:
							continue

						function = line.strip()

						if module not in branchEvents:
							branchEvents[module] = []

						varInfo = callbackNames.get(activeEvent, {})
						metaInfo = callbackMeta.get(activeEvent, {})

						variable = varInfo.get("variable", Util.toUpperSnakeCase(activeEvent))
						urlLineno = varInfo.get("var_lineno", activeEventLineno)
						desc = varInfo.get("desc", "") or metaInfo.get("desc", "") or Util.extractDescription(pendingJavadoc)
						side = metaInfo.get("side", "both")
						deprecated = metaInfo.get("deprecated", False)

						entry = {
							"file": fileName,
							"package": package,
							"interface": activeEvent,
							"variable": variable,
							"function": function,
							"url": f"{fileUrl}#L{urlLineno}",
							"description": desc,
							"side": side,
							"deprecated": deprecated,
						}

						branchEvents[module].append(entry)
						activeEvent = ""

					else:
						if activeVariable == "":
							if ">" in line:
								if hasFactory:
									if "EventFactory." not in line:
										continue
								else:
									if "Event<" not in line:
										continue
								activeVariable = line.split(">")[1].split("=")[0].strip()
								activeVariableLineno = lineno
							continue

						if "*" in line or ";" not in line or "(" not in line:
							continue

						if line.startswith("		"):
							continue

						if "return " in line:
							continue

						function = line.strip()

						if module not in branchEvents:
							branchEvents[module] = []

						urlLineno = activeVariableLineno or activeEventLineno

						entry = {
							"file": fileName,
							"package": package,
							"interface": activeEvent,
							"variable": activeVariable,
							"function": function,
							"url": f"{fileUrl}#L{urlLineno}",
							"description": activeEventDesc or activeEventFallbackDesc,
							"side": activeEventSide,
							"deprecated": activeEventDeprecated,
						}

						branchEvents[module].append(entry)
						activeEvent = ""
						activeVariable = ""
						activeVariableLineno = 0

		print("Parsed:", sum(len(v) for v in branchEvents.values()), "events for branch", branch)

		branchDataOut = {
			"lastupdated": datetime.now().strftime("%Y%m%d%H%M%S"),
			"commit_date": commitDate,
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