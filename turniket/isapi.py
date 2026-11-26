# Copyright (c) 2026, Abdulloh Uchkunov and contributors
# For license information, please see license.txt
"""Hikvision ISAPI klienti.

Bu modul turniket terminallari bilan HTTPS + Digest auth orqali gaplashadi.
Hech qanday yuz tanish BU YERDA bo'lmaydi — tanishni terminal qiladi.
Bu modul faqat: hodisa/odam/rasm o'qish (read) va odam/rasm yozish (write, keyingi bosqich).

Terminal parametrlari `Turniket Terminal` doctype'idan olinadi; admin parol
shifrlangan `admin_password` maydonida saqlanadi (get_password).
"""

import frappe
import requests
from requests.auth import HTTPDigestAuth
from urllib3.exceptions import InsecureRequestWarning

# Self-signed sertifikat ogohlantirishini o'chiramiz (terminallar o'z sertifikati bilan)
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

TIMEOUT = 20


def _terminal_doc(terminal):
	"""terminal — nomi (str) yoki Document bo'lishi mumkin."""
	if isinstance(terminal, str):
		return frappe.get_doc("Turniket Terminal", terminal)
	return terminal


def _conn(terminal):
	doc = _terminal_doc(terminal)
	scheme = "https" if int(doc.port) != 80 else "http"
	base = f"{scheme}://{doc.ip}:{doc.port}"
	user = doc.admin_user or "admin"
	pwd = doc.get_password("admin_password") if doc.admin_password else ""
	return base, HTTPDigestAuth(user, pwd)


def _post_json(terminal, path, payload):
	base, auth = _conn(terminal)
	r = requests.post(
		f"{base}{path}",
		json=payload,
		auth=auth,
		verify=False,
		timeout=TIMEOUT,
		headers={"Content-Type": "application/json"},
	)
	r.raise_for_status()
	return r.json()


def _get_json(terminal, path):
	base, auth = _conn(terminal)
	r = requests.get(f"{base}{path}", auth=auth, verify=False, timeout=TIMEOUT)
	r.raise_for_status()
	return r.json()


def download_bytes(terminal, url):
	"""Terminaldagi faceURL (yoki boshqa havola) dan xom baytlarni yuklaydi."""
	_, auth = _conn(terminal)
	r = requests.get(url, auth=auth, verify=False, timeout=TIMEOUT)
	r.raise_for_status()
	return r.content


# ---------------------------------------------------------------------------
# O'QISH (read)
# ---------------------------------------------------------------------------

def device_info(terminal):
	"""Terminal tirikligini tekshiradi, deviceInfo qaytaradi."""
	try:
		data = _get_json(terminal, "/ISAPI/System/deviceInfo?format=json")
		return data.get("DeviceInfo", data)
	except Exception as e:
		frappe.log_error(f"device_info: {e}", "Turniket ISAPI")
		return None


def user_count(terminal):
	"""Ro'yxatdagi odamlar soni (userNumber, bindFaceUserNumber ...)."""
	data = _get_json(terminal, "/ISAPI/AccessControl/UserInfo/Count?format=json")
	return data.get("UserInfoCount", {})


def iter_all_users(terminal, page=30):
	"""Terminaldagi hamma odamni sahifalab qaytaradi (generator).

	Har element: {employeeNo, name, numOfFace, ...}
	"""
	pos = 0
	while True:
		payload = {
			"UserInfoSearchCond": {
				"searchID": "tz-users",
				"searchResultPosition": pos,
				"maxResults": page,
			}
		}
		res = _post_json(terminal, "/ISAPI/AccessControl/UserInfo/Search?format=json", payload)
		block = res.get("UserInfoSearch", {})
		users = block.get("UserInfo", []) or []
		yield from users
		got = len(users)
		pos += got
		if got == 0 or block.get("responseStatusStrg") == "OK" or pos >= int(block.get("totalMatches", pos)):
			break


def iter_faces(terminal, fdid=None, face_lib_type="blackFD", page=30):
	"""Kutubxonadagi hamma etalon yuzni sahifalab qaytaradi (generator).

	Har element: {FPID, faceURL, modelData}
	"""
	doc = _terminal_doc(terminal)
	fdid = fdid or doc.fdid or "1"
	pos = 0
	while True:
		payload = {
			"searchResultPosition": pos,
			"maxResults": page,
			"faceLibType": face_lib_type,
			"FDID": str(fdid),
		}
		res = _post_json(terminal, "/ISAPI/Intelligent/FDLib/FDSearch?format=json", payload)
		matches = res.get("MatchList", []) or []
		yield from matches
		got = len(matches)
		pos += got
		if got == 0 or res.get("responseStatusStrg") == "OK" or pos >= int(res.get("totalMatches", pos)):
			break


def list_face_libraries(terminal):
	data = _get_json(terminal, "/ISAPI/Intelligent/FDLib?format=json")
	return data.get("FDLib", [])


def get_http_hosts(terminal):
	"""Terminalning hozirgi webhook (httpHosts) sozlamasini O'QIYDI (xavfsiz)."""
	return _get_json(terminal, "/ISAPI/Event/notification/httpHosts?format=json")
