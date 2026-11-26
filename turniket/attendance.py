# Copyright (c) 2026, Abdulloh Uchkunov and contributors
# For license information, please see license.txt
"""O'quvchi davomati — Turniket Checkin'lardan Education Student Attendance.

Xodimlar HRMS (Employee Checkin -> Auto Attendance) orqali ketadi; o'quvchilar
uchun Education'da checkin tushunchasi yo'q, shuning uchun kunlik: o'sha kuni
kamida bitta "Kirdi" hodisasi bo'lgan har bir o'quvchiga Present yoziladi.

Scheduler (hourly) bugungi kunni sinxronlaydi. O'tgan kun uchun:
    bench --site ... execute turniket.attendance.sync_student_attendance --kwargs "{'date':'2026-11-25'}"
"""

import frappe
from frappe.utils import today


def _active_group(student):
	"""O'quvchining birinchi faol Student Group'i (kontekst uchun)."""
	return frappe.db.get_value(
		"Student Group Student", {"student": student, "active": 1}, "parent"
	)


@frappe.whitelist()
def sync_student_attendance(date=None):
	"""Berilgan kun uchun 'Kirdi' bo'lgan o'quvchilarга Present yozadi."""
	date = date or today()
	rows = frappe.db.sql(
		"""SELECT DISTINCT student FROM `tabTurniket Checkin`
		WHERE student IS NOT NULL AND student != '' AND direction = 'Kirdi'
		AND DATE(event_time) = %s""",
		(date,),
		as_dict=True,
	)

	stat = {"date": date, "kirgan_oquvchi": len(rows), "yozildi": 0, "bor_edi": 0, "xato": 0}
	submittable = frappe.get_meta("Student Attendance").is_submittable

	for r in rows:
		student = r.student
		if frappe.db.exists(
			"Student Attendance", {"student": student, "date": date, "docstatus": ["<", 2]}
		):
			stat["bor_edi"] += 1
			continue
		try:
			doc = frappe.new_doc("Student Attendance")
			doc.student = student
			doc.date = date
			doc.status = "Present"
			group = _active_group(student)
			if group:
				doc.student_group = group
			doc.flags.ignore_permissions = True
			doc.insert()
			if submittable:
				doc.submit()
			stat["yozildi"] += 1
		except Exception:
			stat["xato"] += 1
			frappe.log_error(frappe.get_traceback(), f"Turniket davomat: {student}")

	frappe.db.commit()
	frappe.logger().info(f"Turniket davomat sync: {stat}")
	return stat


def sync_today():
	"""Scheduler kirish nuqtasi — bugungi kunni sinxronlaydi."""
	return sync_student_attendance(today())
