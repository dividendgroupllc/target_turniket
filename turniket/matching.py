# Copyright (c) 2026, Abdulloh Uchkunov and contributors
# For license information, please see license.txt
"""Moslashtirish — bootstrap'da tortilgan Turniket Yuz yozuvlarini (terminal ism +
FPID) ERPNext Student/Employee yozuvlariga bog'laydi.

Bu FAQAT BIR MARTALIK ko'chirish bosqichi: mavjud 465 odamni ismi bo'yicha topamiz
(terminalda yagona kalit yo'q, faqat ism bor). Kelajakda yangi odamlar toza raqamli
turniket_id oladi va ism-moslash umuman kerak bo'lmaydi.

Ishga tushirish (dry-run — hech narsa yozmaydi):
    bench --site target.local execute turniket.matching.moslash --kwargs "{'dry_run': 1}"
"""

import re
from difflib import SequenceMatcher

import frappe

# O'zbekcha ism normalizatsiyasi uchun almashtirishlar
_REPLACE = [
	("o'", "o"), ("g'", "g"), ("oʻ", "o"), ("gʻ", "g"),
	("'", ""), ("ʻ", ""), ("`", ""), ("ʼ", ""),
	("kh", "h"), ("ye", "e"),
]
# Olib tashlanadigan qo'shimcha tokenlar (otalik belgilari)
_DROP_TOKENS = {"qizi", "qizı", "ogli", "ugli", "o'g'li", "qz", "aka", "xxx"}


def normalize(name):
	if not name:
		return ""
	s = name.lower().strip()
	for a, b in _REPLACE:
		s = s.replace(a, b)
	# x -> h (Xalimov -> halimov, Xasanova -> hasanova)
	s = s.replace("x", "h")
	# faqat harf va probel
	s = re.sub(r"[^a-zа-яё\s]", " ", s)
	s = re.sub(r"\s+", " ", s).strip()
	return s


def tokens(name):
	toks = [t for t in normalize(name).split() if len(t) >= 2 and t not in _DROP_TOKENS]
	return toks


def token_match_count(a_tokens, b_tokens, thr=0.85):
	"""Ikki token to'plami o'rtasida mos tokenlar soni (fuzzy, typo'ga bardoshli)."""
	used = set()
	n = 0
	for a in a_tokens:
		best_j, best_r = -1, 0.0
		for j, b in enumerate(b_tokens):
			if j in used:
				continue
			r = SequenceMatcher(None, a, b).ratio()
			# kichraytma qo'shimcha: elyor <-> elyorjon (biri ikkinchisining prefiksi)
			if min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a)):
				r = max(r, 0.95)
			if r > best_r:
				best_r, best_j = r, j
		if best_j >= 0 and best_r >= thr:
			used.add(best_j)
			n += 1
	return n


def _pool(doctype):
	"""Nomzodlar: (docname, ko'rinadigan ism, tokenlar, faolmi)."""
	out = []
	if doctype == "Student":
		rows = frappe.get_all("Student", fields=["name", "student_name", "enabled"])
		for r in rows:
			out.append((r.name, r.student_name or "", tokens(r.student_name), bool(r.enabled)))
	else:
		rows = frappe.get_all("Employee", fields=["name", "employee_name", "status"])
		for r in rows:
			out.append((r.name, r.employee_name or "", tokens(r.employee_name), r.status == "Active"))
	return out


def _best(term_tokens, pool):
	"""Pooldan eng mos nomzod(lar). Qaytaradi: (eng_yaxshi, ball, teng_balllar_soni)."""
	scored = []
	for docname, disp, toks, active in pool:
		if not toks:
			continue
		n = token_match_count(term_tokens, toks)
		if n >= 2:  # kamida familiya + ism mos
			# teng ballda faol yutadi, keyin ko'proq token
			scored.append((n, active, docname, disp))
	if not scored:
		return None, 0, 0
	scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
	top = scored[0]
	ties = [s for s in scored if s[0] == top[0] and s[1] == top[1]]
	return top, top[0], len(ties)


@frappe.whitelist()
def moslash(dry_run=1):
	dry_run = int(dry_run)
	students = _pool("Student")
	employees = _pool("Employee")

	stat = {"jami": 0, "boglandi": 0, "dublikat": 0, "topilmadi": 0,
	        "student": 0, "employee": 0, "dry_run": bool(dry_run)}
	namunalar = {"topilmadi": [], "dublikat": []}

	for y in frappe.get_all("Turniket Yuz", fields=["name", "fpid", "terminal_name"]):
		stat["jami"] += 1
		tt = tokens(y.terminal_name)
		fpid = str(y.fpid)

		# FPID prefiksi bo'yicha yo'naltirish (30xxx/000005=xodim, 1xxxx=o'quvchi)
		if fpid.startswith("30") or fpid.startswith("000005"):
			order = [("Employee", employees), ("Student", students)]
		elif fpid.startswith("1"):
			order = [("Student", students), ("Employee", employees)]
		else:
			order = [("Student", students), ("Employee", employees)]

		chosen = None
		for dt, pool in order:
			top, _score, ties = _best(tt, pool)
			if top:
				chosen = (dt, top, ties)
				break

		if not chosen:
			stat["topilmadi"] += 1
			if len(namunalar["topilmadi"]) < 15:
				namunalar["topilmadi"].append(f"{fpid}: {y.terminal_name}")
			continue

		dt, (_score, _active, docname, disp), ties = chosen
		status = "Dublikat" if ties > 1 else "Bog'landi"
		if ties > 1:
			stat["dublikat"] += 1
			if len(namunalar["dublikat"]) < 15:
				namunalar["dublikat"].append(f"{fpid} '{y.terminal_name}' -> {ties} ta nomzod")
		else:
			stat["boglandi"] += 1
			stat["student" if dt == "Student" else "employee"] += 1

		if not dry_run:
			doc = frappe.get_doc("Turniket Yuz", y.name)
			# faqat aniq moslikda link yoziladi; dublikatda belgi qo'yiladi, link yo'q
			if status == "Bog'landi":
				doc.student = docname if dt == "Student" else None
				doc.employee = docname if dt == "Employee" else None
				doc.matched_name = disp
			else:
				doc.matched_name = f"{ties} ta nomzod — qo'lda tanlang"
			doc.match_status = status
			doc.flags.ignore_permissions = True
			doc.save()

	if not dry_run:
		frappe.db.commit()

	result = {**stat, "namunalar": namunalar}
	frappe.logger().info(f"Turniket moslash: {stat}")
	return result
