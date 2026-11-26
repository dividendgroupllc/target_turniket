# Copyright (c) 2026, Abdulloh Uchkunov and contributors
# For license information, please see license.txt
"""Turniket webhook — terminallar yuz tanishda hodisani shu yerga POST qiladi.

Hodisa formati (DS-K1T342MFX-E1, amalda tasdiqlangan): multipart/form-data —
"event_log" (JSON) + "Picture" (JPEG), yoki raw JSON/XML. JSON ichida:
  dateTime, ipAddress, macAddress, eventType,
  AccessControllerEvent: {employeeNoString|employeeNo, name, deviceName,
    currentVerifyMode, majorEventType, subEventType, serialNo}

Oqim: parse -> dublikat tekshir -> Turniket Checkin yoz -> FPID'ni Turniket Yuz
orqali Student/Employee ga bog'la -> xodim bo'lsa HRMS Employee Checkin yarat.
Hech qachon xato qaytarmaydi (terminal qayta-qayta urinmasligi uchun 200 OK).
"""

import json
import xml.etree.ElementTree as ET

import frappe
from frappe.utils import now_datetime
from frappe.utils.file_manager import save_file

# MAC -> (terminal nomi, yo'nalish). 6 terminal, amalda tasdiqlangan.
TERMINAL_BY_MAC = {
	"a4:d5:c2:6e:a4:b6": ("Kirish-1", "Kirdi"),   # 4442
	"a4:d5:c2:6e:a4:c1": ("Chiqish-1", "Chiqdi"),  # 4441
	"a4:d5:c2:6e:a4:b0": ("Kirish-2", "Kirdi"),   # 4444
	"a4:d5:c2:6e:a4:c7": ("Chiqish-2", "Chiqdi"),  # 4443
	"a4:d5:c2:6e:a4:b1": ("Kirish-3", "Kirdi"),   # 4446
	"a4:d5:c2:6e:a4:ac": ("Chiqish-3", "Chiqdi"),  # 4445
}


@frappe.whitelist(allow_guest=True)
def listen(token=None, **kwargs):
	"""Terminal hodisasini qabul qiladi. Doim 200 OK qaytaradi."""
	try:
		_check_token(token)
		event, picture = _extract_event()
		if event:
			_process(event, picture)
	except PermissionError:
		raise
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Turniket webhook")
	return {"statusCode": 1, "statusString": "OK"}


def _check_token(token):
	secret = frappe.db.get_single_value("Turniket Sozlamalari", "secret_token")
	if secret and token != secret:
		raise PermissionError("Token noto'g'ri")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _extract_event():
	"""So'rovdan (event dict, picture bytes) ajratadi. Heartbeat -> (None, None)."""
	req = frappe.request
	candidates = []
	picture = None

	if req.files:
		for key in req.files:
			for f in req.files.getlist(key):
				data = f.read()
				if not data:
					continue
				mt = (f.mimetype or "").lower()
				if key.lower().startswith("picture") or mt.startswith("image/"):
					picture = data
				else:
					candidates.append(data)
	if req.form:
		for val in req.form.values():
			if val:
				candidates.append(val.encode() if isinstance(val, str) else val)
	if req.data:
		candidates.append(req.data)

	for raw in candidates:
		payload = _parse_payload(raw)
		if payload:
			event = _normalize(payload)
			if event:
				return event, picture
	return None, None


def _parse_payload(raw):
	raw = (raw or b"").strip()
	if not raw:
		return None
	if raw.startswith(b"<"):
		return _xml_to_dict(raw)
	try:
		return json.loads(raw)
	except (json.JSONDecodeError, UnicodeDecodeError):
		return None


def _xml_to_dict(raw):
	try:
		root = ET.fromstring(raw)
	except ET.ParseError:
		return None

	def strip_ns(tag):
		return tag.split("}", 1)[-1]

	def to_dict(el):
		children = list(el)
		if not children:
			return (el.text or "").strip()
		return {strip_ns(c.tag): to_dict(c) for c in children}

	tag = strip_ns(root.tag)
	return to_dict(root) if tag == "EventNotificationAlert" else {tag: to_dict(root)}


def _normalize(payload):
	"""Shaxs aniqlangan o'tish hodisasini bitta ko'rinishga keltiradi."""
	if not isinstance(payload, dict):
		return None
	if str(payload.get("eventType") or "").lower() in ("heartbeat", "videoloss"):
		return None

	acs = payload.get("AccessControllerEvent")
	if not isinstance(acs, dict):
		return None

	fpid = str(acs.get("employeeNoString") or acs.get("employeeNo") or "").strip()
	if not fpid:
		return None  # shaxssiz hodisa (notanish yuz, eshik) — saqlamaymiz

	mac = str(payload.get("macAddress") or "").strip().lower()
	term_name, mac_direction = TERMINAL_BY_MAC.get(mac, (None, ""))
	device_name = str(acs.get("deviceName") or "").strip()

	return {
		"fpid": fpid,
		"person_name": str(acs.get("name") or "").strip(),
		"event_time": _parse_time(payload.get("dateTime")),
		"terminal": term_name,
		"direction": _direction(device_name, mac_direction),
		"verify_mode": str(acs.get("currentVerifyMode") or "").strip(),
		"event_code": f"{acs.get('majorEventType', '')}/{acs.get('subEventType', '')}",
		"serial": str(acs.get("serialNo") or "").strip(),
		"mac": mac,
		"raw": payload,
	}


def _direction(device_name, mac_direction):
	n = (device_name or "").lower()
	if n.startswith("kirish"):
		return "Kirdi"
	if n.startswith("chiqish"):
		return "Chiqdi"
	return mac_direction


def _parse_time(value):
	value = str(value or "").strip()
	if len(value) >= 19:
		return value[:19].replace("T", " ")
	return now_datetime()


# ---------------------------------------------------------------------------
# Yozish
# ---------------------------------------------------------------------------

def _process(event, picture):
	if event["serial"]:
		dedup = f"{event['mac']}-{event['serial']}"
	else:
		dedup = f"{event['fpid']}-{event['event_time']}-{event['direction']}"

	if frappe.db.exists("Turniket Checkin", {"external_event_id": dedup}):
		return  # dublikat

	person = _resolve_person(event["fpid"])

	doc = frappe.new_doc("Turniket Checkin")
	doc.fpid = event["fpid"]
	doc.person_name = event["person_name"]
	doc.event_time = event["event_time"]
	doc.direction = event["direction"]
	# Terminal yozuvi bo'lsa bog'laymiz; bo'lmasa bo'sh — hodisa baribir saqlanadi
	if event["terminal"] and frappe.db.exists("Turniket Terminal", event["terminal"]):
		doc.terminal = event["terminal"]
	doc.person_type = person["type"]
	doc.student = person["student"]
	doc.employee = person["employee"]
	doc.verify_mode = event["verify_mode"]
	doc.event_code = event["event_code"]
	doc.external_event_id = dedup
	doc.raw_data = json.dumps(event["raw"], ensure_ascii=False)
	doc.flags.ignore_permissions = True
	doc.insert()

	if picture:
		f = save_file(f"otish_{doc.name}.jpg", picture, "Turniket Checkin", doc.name, is_private=1)
		doc.db_set("picture", f.file_url)

	# Xodim bo'lsa HRMS Employee Checkin yaratamiz (Auto Attendance o'zi ishlaydi).
	# Xatosi Turniket Checkin'ni buzmasin — alohida try ichida.
	if person["employee"]:
		try:
			_make_employee_checkin(doc, person["employee"], event)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Turniket Employee Checkin")


def _resolve_person(fpid):
	"""FPID -> Turniket Yuz -> Student/Employee."""
	out = {"type": "Noma'lum", "student": None, "employee": None}
	yuz = frappe.db.get_value("Turniket Yuz", fpid, ["student", "employee"], as_dict=True)
	if not yuz:
		return out
	if yuz.student:
		out["type"] = "O'quvchi"
		out["student"] = yuz.student
	elif yuz.employee:
		out["type"] = "Xodim"
		out["employee"] = yuz.employee
	return out


def _make_employee_checkin(checkin_doc, employee, event):
	log_type = "IN" if event["direction"] == "Kirdi" else "OUT"
	emp_name = frappe.db.get_value("Employee", employee, "employee_name")
	ec = frappe.new_doc("Employee Checkin")
	ec.employee = employee
	ec.employee_name = emp_name
	ec.time = event["event_time"]
	ec.log_type = log_type
	ec.device_id = event["terminal"]
	ec.flags.ignore_permissions = True
	ec.insert()
	checkin_doc.db_set("employee_checkin", ec.name)
