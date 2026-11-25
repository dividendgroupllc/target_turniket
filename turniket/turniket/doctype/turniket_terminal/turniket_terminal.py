# Copyright (c) 2026, Abdulloh Uchkunov and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class TurniketTerminal(Document):
	def base_url(self):
		scheme = "https" if int(self.port) != 80 else "http"
		return f"{scheme}://{self.ip}:{self.port}"

	@frappe.whitelist()
	def test_connection(self):
		"""Terminalga ulanib, holatini yangilaydi."""
		from turniket.isapi import device_info

		info = device_info(self)
		self.db_set("status", "Onlayn" if info else "Javob bermadi")
		if info and info.get("macAddress"):
			self.db_set("mac", info["macAddress"])
		return info
