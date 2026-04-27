# -*- coding: utf-8 -*-
import re
import time
import requests
from datetime import datetime, timezone

def naturalSort(l):
	convert = lambda text: int(text) if text.isdigit() else text.lower()
	alphanumKey = lambda key: [convert(c) for c in re.split('([0-9]+)', key)]
	return sorted(l, key=alphanumKey)

def fetchWithRetry(url, responseHeaders=None, params=None, maxRetries=3):
	lastErr = None
	for attempt in range(maxRetries):
		try:
			response = requests.get(url, headers=responseHeaders or {}, params=params)
		except requests.exceptions.RequestException as e:
			lastErr = e
			if attempt < maxRetries - 1:
				wait = 2 ** attempt
				print(f"  Connection error, retrying in {wait}s... ({e})")
				time.sleep(wait)
				continue
			raise
		if response.status_code == 429 or response.status_code >= 500:
			if attempt < maxRetries - 1:
				wait = 2 ** attempt
				print(f"  Rate limited / server error ({response.status_code}), retrying in {wait}s...")
				time.sleep(wait)
				continue
		response.raise_for_status()
		return response
	raise lastErr

def resolveInlineTags(text):
	def replace(m):
		content = m.group(1).strip()
		if '#' in content:
			return content.split('#')[-1]
		return content.split('.')[-1]
	return re.sub(r'\{@(?:link|linkplain|code)\s+([^}]+)\}', replace, text)

def cleanJavadoc(lines):
	text = " ".join(lines)
	text = resolveInlineTags(text)
	text = re.sub(r'<[^>]+>', '', text)
	return re.sub(r'\s+', ' ', text).strip()

def extractDescription(javadocLines):
	if not javadocLines:
		return ""
	descLines = []
	for l in javadocLines:
		if l.startswith('@'):
			break
		descLines.append(l)

	inPre = False
	segments = []
	currentLines = []

	for l in descLines:
		if re.search(r'<pre\b', l, re.IGNORECASE):
			if currentLines:
				segments.append((False, currentLines))
				currentLines = []
			inPre = True
			after = re.sub(r'<pre[^>]*>(<code[^>]*>)?', '', l, flags=re.IGNORECASE).strip()
			if after:
				currentLines.append(after)
		elif re.search(r'</pre>', l, re.IGNORECASE):
			before = re.sub(r'(</code>)?</pre>', '', l, flags=re.IGNORECASE).strip()
			if before:
				currentLines.append(before)
			segments.append((True, currentLines))
			currentLines = []
			inPre = False
		else:
			cleaned = re.sub(r'</?code[^>]*>', '', l, flags=re.IGNORECASE) if inPre else l
			if cleaned.strip():
				currentLines.append(cleaned)

	if currentLines:
		segments.append((inPre, currentLines))

	parts = []
	for isCode, lines in segments:
		if isCode:
			parts.append("```\n" + "\n".join(lines) + "\n```")
		else:
			prose = " ".join(lines)
			prose = resolveInlineTags(prose)
			prose = re.sub(r'<[^>]+>', '', prose)
			prose = re.sub(r'\s+', ' ', prose).strip()
			if prose:
				parts.append(prose)

	return "\n".join(parts).strip().rstrip('.') or ""

def extractDescriptionWithDeprecated(javadocLines):
	desc = extractDescription(javadocLines)
	deprecatedText = ""
	for line in javadocLines:
		m = re.match(r'@deprecated\s+(.*)', line.strip(), re.IGNORECASE)
		if m:
			raw = m.group(1).strip()
			deprecatedText = "Deprecated: " + re.sub(r'\s+', ' ', resolveInlineTags(raw)).strip()
			break
	if desc and deprecatedText:
		return desc + "\n" + deprecatedText
	return deprecatedText or desc

def extractSide(javadocLines):
	text = " ".join(javadocLines)
	server = bool(re.search(r'LogicalSide[#.]SERVER', text))
	client = bool(re.search(r'LogicalSide[#.]CLIENT', text))
	if server and client:
		return "both"
	if server:
		return "server"
	if client:
		return "client"
	return None

def inferSideFromName(name):
	if re.match(r'Server|Dedicated', name):
		return "server"
	if re.match(r'Client', name):
		return "client"
	return None

def findFallbackDescription(linesList, fromLineno):
	preJavadoc = []
	preInJavadoc = False
	preInJavadocPre = False
	for l in linesList[fromLineno:]:
		s = l.strip()
		if not preInJavadoc and re.match(r'^public\s+(?:interface|class)\s+', l):
			break
		if "/**" in s:
			preInJavadoc = True
			preInJavadocPre = False
			preJavadoc = []
		elif preInJavadoc:
			if "*/" in s:
				preInJavadoc = False
				desc = extractDescription(preJavadoc)
				if desc:
					return desc
			else:
				clean = re.sub(r'^\s*\*\s?', '', l)
				if preInJavadocPre:
					if re.search(r'</pre>', clean, re.IGNORECASE):
						preInJavadocPre = False
					preJavadoc.append(clean.rstrip())
				else:
					clean = clean.strip()
					if clean:
						preJavadoc.append(clean)
					if re.search(r'<pre\b', clean, re.IGNORECASE):
						preInJavadocPre = True
	return ""


def _splitTypeAndNames(s):
	s = re.sub(r'\s*=.*', '', s).strip()
	depth = 0
	for i, c in enumerate(s):
		if c in '<[':
			depth += 1
		elif c in '>]':
			depth -= 1
		elif c == ' ' and depth == 0:
			rest = s[i + 1:].strip()
			if re.match(r'^\w[\w, ]*$', rest):
				names = [n.strip() for n in rest.split(',') if re.match(r'^\w+$', n.strip())]
				return s[:i].strip(), names
	return None, []


def _extractAllClassFields(text):
	lines = text.split("\n")

	setter_names = set()
	for line in lines:
		m = re.match(r'\s*public\s+void\s+set(\w+)\s*\(', line)
		if m:
			n = m.group(1)
			setter_names.add(n[0].lower() + n[1:])

	result = {}
	seen_per_class = {}
	class_stack = []
	brace_depth = 0
	in_javadoc = False

	for line in lines:
		stripped = line.strip()
		if "/**" in stripped:
			in_javadoc = True
			continue
		if in_javadoc:
			if "*/" in stripped:
				in_javadoc = False
			continue

		brace_depth += stripped.count("{") - stripped.count("}")
		while class_stack and class_stack[-1][1] > brace_depth:
			class_stack.pop()

		if stripped.startswith("//"):
			continue

		if re.search(r'\bclass\s+\w', stripped):
			m = re.search(r'\bclass\s+(\w+)', stripped)
			if m:
				cname = m.group(1)
				class_stack.append((cname, brace_depth))
				if cname not in result:
					result[cname] = []
					seen_per_class[cname] = set()
			continue

		if not class_stack:
			continue

		m = re.match(
			r'(public|private|protected)\s+'
			r'((?:(?:static|final|transient|volatile)\s+)*)'
			r'(.+)',
			stripped
		)
		if not m:
			continue

		modifiers = m.group(2)
		if 'static' in modifiers or 'transient' in modifiers:
			continue

		rest = m.group(3)
		if ';' not in rest:
			continue
		rest = rest[:rest.index(';')]
		rest = re.sub(r'^(@\w+(?:\([^)]*\))?\s*)+', '', rest).strip()

		ftype, fnames = _splitTypeAndNames(rest)
		if not ftype or not fnames:
			continue

		is_final = 'final' in modifiers
		cname = class_stack[-1][0]
		for fname in fnames:
			if fname not in seen_per_class[cname]:
				seen_per_class[cname].add(fname)
				mutable = (not is_final) or (fname in setter_names)
				result[cname].append({"name": fname, "type": ftype, "kind": "field", "mutable": mutable})

	return {k: v for k, v in result.items() if v}


def _normalizeRecordComponentName(raw):
	if raw.startswith("get") and len(raw) > 3 and raw[3].isupper():
		return raw[3].lower() + raw[4:]
	if raw.startswith("is") and len(raw) > 2 and raw[2].isupper():
		return raw[2].lower() + raw[3:]
	return raw


def extractRecordFields(line):
	m = re.search(r'\brecord\s+\w+\s*\(([^)]*)\)', line)
	if not m:
		return None
	params_str = m.group(1).strip()
	if not params_str:
		return None
	fields = []
	for param in params_str.split(","):
		param = re.sub(r'@\w+(?:\([^)]*\))?\s*', '', param).strip()
		parts = param.rsplit(None, 1)
		if len(parts) == 2:
			fields.append({"name": _normalizeRecordComponentName(parts[1].strip()), "type": parts[0].strip(), "kind": "record", "mutable": False})
	return fields if fields else None


def extractForgeEvents(text, name, blobUrl, isCancellable):
	lines = text.split("\n")
	results = []
	allFields = _extractAllClassFields(text)

	package = ""
	pendingJavadoc = []
	pendingAnnotations = []
	inJavadoc = False
	inJavadocPre = False
	braceDepth = 0

	typeStack = []

	outerClassSeen = False

	for lineno, line in enumerate(lines, start=1):
		stripped = line.strip()

		if "/**" in stripped:
			inJavadoc = True
			inJavadocPre = False
			pendingJavadoc = []
			pendingAnnotations = []
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

		if not stripped.startswith("//"):
			braceDepth += stripped.count("{") - stripped.count("}")
			while typeStack and typeStack[-1]["depth"] > braceDepth:
				typeStack.pop()

		if stripped.startswith("@") and not stripped.startswith("@Override"):
			pendingAnnotations.append(stripped)
			continue

		if stripped.startswith("package ") and not package:
			pkg = stripped.replace("package", "", 1).replace(";", "").strip()
			if not pkg.startswith("/*"):
				package = pkg
			pendingAnnotations = []
			continue

		# Interfaces
		if re.search(r'\binterface\s+\w', stripped) and not stripped.startswith("//"):
			ifaceMatch = re.search(r'\binterface\s+(\w+)', stripped)
			if ifaceMatch and "{" in stripped:
				typeStack.append({
					"name": ifaceMatch.group(1),
					"kind": "interface",
					"depth": braceDepth,
					"access": "public" if "public " in stripped else ("private" if "private " in stripped else ""),
					"cancellable": False,
				})
			pendingJavadoc = []
			pendingAnnotations = []
			continue

		# Records
		if re.search(r'\brecord\s+\w', stripped) and not stripped.startswith("//"):
			parentInterface = next(
				(f for f in reversed(typeStack) if f["kind"] == "interface"),
				None
			)
			if parentInterface and package:
				lineUrl = f"{blobUrl}#L{lineno}"
				annotationText = " ".join(pendingAnnotations)
				isDeprecated = bool(re.search(r'@Deprecated\b', annotationText))
				thisCancellable = isCancellable(annotationText) or isCancellable(stripped)
				recordMatch = re.search(r'\brecord\s+(\w+)', stripped)
				if recordMatch:
					parentName = parentInterface["name"]
					desc = extractDescriptionWithDeprecated(pendingJavadoc)
					side = inferSideFromName(parentName) or inferSideFromName(recordMatch.group(1))
					fields = extractRecordFields(stripped)
					entry = {
						"event": f"{parentName}.{recordMatch.group(1)}",
						"package": package,
						"url": lineUrl,
						"cancellable": thisCancellable,
						"description": desc,
						"side": side,
						"deprecated": isDeprecated,
						"hasResult": False,
					}
					if fields:
						entry["fields"] = fields
					results.append((package, entry))
			pendingJavadoc = []
			pendingAnnotations = []
			continue

		# Classes
		if re.search(r'\bclass\s+\w', stripped) and not stripped.startswith("//"):
			lineUrl = f"{blobUrl}#L{lineno}"
			annotationText = " ".join(pendingAnnotations)
			isDeprecated = bool(re.search(r'@Deprecated\b', annotationText))
			thisHasResult = bool(re.search(r'@(?:Event\.)?HasResult\b', annotationText))
			thisCancellable = isCancellable(annotationText) or isCancellable(stripped)
			classMatch = re.search(r'\bclass\s+(\w+)', stripped)
			extendsMatch = re.search(r'\bextends\s+([\w.]+)', stripped)

			if "public " in stripped:
				thisAccess = "public"
			elif "private " in stripped:
				thisAccess = "private"
			elif "protected " in stripped:
				thisAccess = "protected"
			else:
				thisAccess = ""

			if classMatch:
				innerName = classMatch.group(1)
				baseClass = extendsMatch.group(1) if extendsMatch else ""

				baseClassSimple = baseClass.split(".")[-1] if baseClass else ""

				frameDepth = braceDepth if "{" in stripped else braceDepth + 1
				typeStack.append({
					"name": innerName,
					"kind": "class",
					"depth": frameDepth,
					"access": thisAccess,
					"cancellable": thisCancellable,
				})

				if not outerClassSeen:
					outerClassSeen = True
					desc = extractDescriptionWithDeprecated(pendingJavadoc)
					side = extractSide(pendingJavadoc) or inferSideFromName(innerName)
					pendingJavadoc = []
					pendingAnnotations = []
					if package:
						entry = {
							"event": innerName,
							"package": package,
							"url": lineUrl,
							"cancellable": thisCancellable,
							"description": desc,
							"side": side,
							"deprecated": isDeprecated,
							"hasResult": thisHasResult,
						}
						fields = allFields.get(innerName)
						if fields:
							entry["fields"] = fields
						results.append((package, entry))

				else:
					if thisAccess == "private":
						pendingJavadoc = []
						pendingAnnotations = []
						continue

					parentClassFrame = next(
						(f for f in reversed(typeStack[:-1]) if f["kind"] == "class"),
						None
					)
					parentInterfaceFrame = next(
						(f for f in reversed(typeStack[:-1]) if f["kind"] == "interface"),
						None
					)

					if parentInterfaceFrame and (
						not parentClassFrame
						or parentInterfaceFrame["depth"] > parentClassFrame["depth"]
					):
						parentName = parentInterfaceFrame["name"]
						desc = extractDescriptionWithDeprecated(pendingJavadoc)
						side = extractSide(pendingJavadoc) or inferSideFromName(innerName) or inferSideFromName(parentName)
						pendingJavadoc = []
						pendingAnnotations = []
						if package:
							entry = {
								"event": f"{parentName}.{innerName}",
								"package": package,
								"url": lineUrl,
								"cancellable": thisCancellable,
								"description": desc,
								"side": side,
								"deprecated": isDeprecated,
								"hasResult": thisHasResult,
							}
							fields = allFields.get(innerName)
							if fields:
								entry["fields"] = fields
							results.append((package, entry))

					elif "static" in stripped:
						if "<" in innerName:
							pendingJavadoc = []
							pendingAnnotations = []
							continue

						if not baseClassSimple or baseClassSimple == "Event":
							pendingJavadoc = []
							pendingAnnotations = []
							continue

						ancestorFrames = [f for f in typeStack[:-1] if f["kind"] == "class"]
						matchIdx = next(
							(i for i, f in enumerate(ancestorFrames) if f["name"] == baseClassSimple),
							None
						)
						if matchIdx is not None:
							ancestorFrames = ancestorFrames[:matchIdx + 1]

						if not ancestorFrames:
							pendingJavadoc = []
							pendingAnnotations = []
							continue

						publicAncestorFrames = [f for f in ancestorFrames if f["access"] != "private"]
						if not publicAncestorFrames:
							pendingJavadoc = []
							pendingAnnotations = []
							continue

						parentPath = ".".join(f["name"] for f in publicAncestorFrames)

						parentCancellable = ancestorFrames[-1]["cancellable"]
						innerCancellable = thisCancellable or parentCancellable

						desc = extractDescriptionWithDeprecated(pendingJavadoc)
						side = extractSide(pendingJavadoc) or inferSideFromName(innerName) or inferSideFromName(publicAncestorFrames[-1]["name"])
						pendingJavadoc = []
						pendingAnnotations = []

						if package:
							entry = {
								"event": f"{parentPath}.{innerName}",
								"package": package,
								"url": lineUrl,
								"cancellable": innerCancellable,
								"description": desc,
								"side": side,
								"deprecated": isDeprecated,
								"hasResult": thisHasResult,
							}
							fields = allFields.get(innerName)
							if fields:
								entry["fields"] = fields
							results.append((package, entry))

					else:
						pendingJavadoc = []
						pendingAnnotations = []

			continue

		if stripped and not stripped.startswith("//") and not stripped.startswith("*"):
			pendingAnnotations = []

	return results

def fetchAllBranches(branchesApiUrl, responseHeaders):
	branchesData = []
	page = 1
	while True:
		response = fetchWithRetry(
			branchesApiUrl,
			responseHeaders=responseHeaders,
			params={"per_page": 100, "page": page}
		)
		data = response.json()
		if not data:
			break
		branchesData.extend(data)
		page += 1
	return branchesData

def getCommitIdentifier(branchData, responseHeaders):
	lastCommitSha = branchData["commit"]["sha"]
	lastCommitUrl = branchData["commit"]["url"]
	commitResponse = fetchWithRetry(lastCommitUrl, responseHeaders=responseHeaders)
	commitData = commitResponse.json()
	commitMessage = commitData["commit"]["message"]
	identifier = "[" + lastCommitSha + "] " + commitMessage

	rawDate = commitData.get("commit", {}).get("committer", {}).get("date", "")
	try:
		dt = datetime.strptime(rawDate, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
		commitDate = dt.strftime("%Y%m%d%H%M%S")
	except Exception:
		commitDate = ""

	return identifier, commitDate

def toUpperSnakeCase(s):
	snake = re.sub(r'(?<!^)(?=[A-Z])', '_', s)
	return snake.upper()