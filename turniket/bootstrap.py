# Copyright (c) 2026, Abdulloh Uchkunov and contributors
# For license information, please see license.txt
"""Bootstrap — egalikni Next'ga o'tkazish uchun BIR MARTALIK ko'chirish.

Terminaldagi mavjud ro'yxatni (odam nomi + FPID + etalon yuz rasmi) ERPNext'ga
tortib, `Turniket Yuz` doctype'iga saqlaydi. Bu FAQAT O'QISH — terminalga hech
narsa yozilmaydi.

Keyingi qadamlar (alohida): Turniket Yuz yozuvlarini Student/Employee ga moslash,
har kimga toza raqamli turniket_id berish, keyin push (ikki tomonlama).

Ishga tushirish:
    bench --site target.local execute turniket.bootstrap.pull_from_terminal \
        --kwargs "{'terminal_name': 'Kirish-1', 'dry_run': 1}"
"""

import frappe
from frappe.utils.file_manager import save_file

from turniket import isapi


@frappe.whitelist()
def pull_from_terminal(terminal_name, dry_run=0, limit=None):
	"""Bitta terminaldan hamma etalon rasmni tortib oladi.

	dry_run=1 — hech narsa yozmaydi, faqat nimalar bo'lishini hisoblaydi.
	limit — faqat shuncha yuzni oladi (sinov uchun).
	"""
	dry_run = int(dry_run)
	limit = int(limit) if limit else None

	terminal = frappe.get_doc("Turniket Terminal", terminal_name)

	# 1) FPID -> ism xaritasi (hamma odam)
	user_map = {}
	for u in isapi.iter_all_users(terminal):
		fpid = str(u.get("employeeNo"))
		user_map[fpid] = {
			"name": u.get("name", ""),
			"numOfFace": int(u.get("numOfFace", 0) or 0),
		}

	count = isapi.user_count(terminal)
	log = {
		"terminal": terminal_name,
		"userNumber": count.get("userNumber"),
		"bindFaceUserNumber": count.get("bindFaceUserNumber"),
		"users_fetched": len(user_map),
		"faces_pulled": 0,
		"faces_failed": 0,
		"created": 0,
		"updated": 0,
		"dry_run": bool(dry_run),
		"errors": [],
	}

	# 2) Etalon yuzlarni sahifalab tortamiz
	n = 0
	for m in isapi.iter_faces(terminal):
		if limit and n >= limit:
			break
		n += 1
		fpid = str(m.get("FPID"))
		face_url = m.get("faceURL")
		info = user_map.get(fpid, {})
		term_name = info.get("name", "")

		if dry_run:
			log["faces_pulled"] += 1
			continue

		# Rasmni yuklab olamiz
		content = None
		if face_url:
			try:
				content = isapi.download_bytes(terminal, face_url)
			except Exception as e:
				log["faces_failed"] += 1
				if len(log["errors"]) < 10:
					log["errors"].append(f"{fpid}: yuklab bo'lmadi: {e}")

		# Turniket Yuz yozuvini upsert qilamiz
		exists = frappe.db.exists("Turniket Yuz", fpid)
		if exists:
			yuz = frappe.get_doc("Turniket Yuz", fpid)
			log["updated"] += 1
		else:
			yuz = frappe.new_doc("Turniket Yuz")
			yuz.fpid = fpid
			log["created"] += 1

		yuz.terminal_name = term_name
		yuz.source_terminal = terminal_name
		yuz.has_face = 1 if content else 0
		if not yuz.match_status:
			yuz.match_status = "Bog'lanmagan"
		yuz.flags.ignore_permissions = True
		yuz.save()

		if content:
			f = save_file(
				f"etalon_{fpid}.jpg",
				content,
				"Turniket Yuz",
				yuz.name,
				is_private=1,
			)
			yuz.db_set("photo", f.file_url)
			log["faces_pulled"] += 1

		if n % 25 == 0:
			frappe.db.commit()

	if not dry_run:
		frappe.db.commit()
		frappe.db.set_value(
			"Turniket Sozlamalari", "Turniket Sozlamalari", "last_bootstrap", frappe.utils.now()
		)

	frappe.logger().info(f"Turniket bootstrap: {log}")
	return log
