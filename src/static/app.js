/* jshint esversion: 11, browser: true */

"use strict";

// Browser side of the machines app: the sign-in page (index.html, following
// gcp-myworld's), the page the mailed link leads to (verify.html) and the
// machines page, and the About page's facts of the build serving.

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
	const init = { method: options.method || "GET", headers: {} };
	if (options.body !== undefined) {
		init.headers["Content-Type"] = "application/json";
		init.body = JSON.stringify(options.body);
	}
	const response = await fetch(path, init);
	const data = await response.json();
	if (!response.ok) {
		throw new Error(data.error || response.statusText);
	}
	return data;
}

function showError(message) {
	const box = $("error");
	box.textContent = message;
	box.hidden = !message;
}

// ─── footer (every page) ────────────────────────────────────────────────────

// The footer's copyright year: this year, as the reader's clock says it. (The
// build serving is on the About page, not on every page: a page the same for
// every build is one the browser may keep.)
function showYear() {
	for (const year of document.querySelectorAll(".copyright-year")) {
		year.textContent = String(new Date().getFullYear());
	}
}

// ─── about page ─────────────────────────────────────────────────────────────

const REPO = "veltzer/gcp-machines";

function el(tag, className, text) {
	const node = document.createElement(tag);
	if (className) {
		node.className = className;
	}
	if (text !== undefined) {
		node.textContent = text;
	}
	return node;
}

function link(href, ...children) {
	const a = el("a");
	a.href = href;
	a.append(...children);
	return a;
}

// "2026-11-23 10:04:05 UTC", and "23 November 2026, 12:04:05 (Asia/Jerusalem)" in the reader's zone
function bothZones(iso) {
	const when = new Date(iso);
	const utc = el("time", null, `${when.toISOString().slice(0, 19).replace("T", " ")} UTC`);
	utc.dateTime = when.toISOString();
	const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
	const local = new Intl.DateTimeFormat(undefined, { dateStyle: "long", timeStyle: "medium" }).format(when);
	const box = el("span");
	box.append(utc, el("br"), el("span", "muted", `${local} (${zone || "your time zone"})`));
	return box;
}

function buildTime(iso) {
	return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(iso || "") ? bothZones(iso) : "not known (a build run locally)";
}

// The About page: the build serving (/app/version): the commit it was built
// from, when it was deployed and built, when this server started, and the
// Cloud Run revision. Every time is said in UTC and in the reader's own zone.
async function aboutPage() {
	const info = await api("/app/version");
	const list = $("about-facts");
	const fact = (label, value) => {
		const dd = el("dd");
		dd.append(value);
		list.append(el("dt", null, label), dd);
	};
	list.replaceChildren();
	// "v1.2-3-gabc1234-dirty": the commit is its last part
	const commit = (info.git_describe || "").match(/(?:-g)?([0-9a-f]{7,40})(?:-dirty)?$/);
	if (commit) {
		const source = el("span");
		source.append(
			link(`https://github.com/${REPO}`, REPO),
			" at ",
			link(`https://github.com/${REPO}/commit/${commit[1]}`, el("code", null, commit[1])),
		);
		fact("Source", source);
	} else {
		fact("Source", info.git_describe || "not known");
	}
	fact("Deployed", buildTime(info.deploy_date));
	fact("Built", buildTime(info.built_at));
	fact("This server started", buildTime(info.started_at));
	fact("Serving as", info.revision && info.revision !== "local" ? info.revision : "a local server");
}

// ─── sign-in page ───────────────────────────────────────────────────────────

function setupProviders(configured) {
	for (const method of document.querySelectorAll(".method[data-provider]")) {
		if (configured[method.dataset.provider]) {
			continue;
		}
		method.classList.add("off");
		method.setAttribute("aria-disabled", "true");
		method.title = "Not configured on this server";
		method.onclick = (event) => event.preventDefault();
	}
}

// Signing in takes the email and the password; "Get a link by mail" takes
// only the email, and the server mails it a link to choose the password with
function setupEmailForm() {
	const form = $("email-form");
	const password = form.elements.password;
	const submit = $("email-submit");
	let mode = "login";

	const applyMode = () => {
		const linkMode = mode === "link";
		submit.textContent = linkMode ? "Mail me a link" : "Sign in";
		$("password-row").hidden = linkMode;
		$("email-hint").hidden = !linkMode;
		$("email-switch-text").textContent = linkMode ? "Already chose a password?" : "New here, or forgot your password?";
		$("email-mode").textContent = linkMode ? "Sign in" : "Get a link by mail";
		showError("");
	};
	$("email-mode").onclick = () => {
		mode = mode === "login" ? "link" : "login";
		applyMode();
	};
	$("toggle-password").onclick = () => {
		const hidden = password.type === "password";
		password.type = hidden ? "text" : "password";
		$("toggle-password").textContent = hidden ? "Hide" : "Show";
	};
	form.onsubmit = async (event) => {
		event.preventDefault();
		showError("");
		$("email-sent").hidden = true;
		const email = form.elements.email.value.trim();
		if (!email || !email.includes("@")) {
			showError("Enter your email address.");
			form.elements.email.focus();
			return;
		}
		submit.disabled = true;
		try {
			if (mode === "link") {
				const result = await api("/auth/email/register", { method: "POST", body: { email } });
				$("email-sent").textContent = result.message;
				$("email-sent").hidden = false;
			} else {
				const result = await api("/auth/email/login", { method: "POST", body: { email, password: password.value } });
				window.location.href = result.next;
				return;
			}
		} catch (e) {
			showError(e.message);
		}
		submit.disabled = false;
	};
	applyMode();
}

// the email tile opens the email form under the tiles, and closes it again
function setupEmailMethod() {
	const button = $("email-method");
	button.onclick = () => {
		const open = $("email-form").hidden;
		$("email-form").hidden = !open;
		button.setAttribute("aria-expanded", String(open));
		if (open) {
			$("email-form").elements.email.focus();
		}
	};
}

async function indexPage() {
	const config = await api("/api/config");
	if (config.signed_in) {
		window.location.reload();
		return;
	}
	const error = new URLSearchParams(window.location.search).get("error");
	if (error) {
		showError(error);
		window.history.replaceState(null, "", "/");
	}
	setupProviders(config.providers);
	setupEmailMethod();
	setupEmailForm();
	$("dev-login").hidden = !config.dev_login;
}

// ─── the mailed link's page ─────────────────────────────────────────────────

// The link carries a token that proves its reader has the mailbox: they choose the password here
async function verifyPage() {
	const config = await api("/api/config");
	const token = new URLSearchParams(window.location.search).get("token") || "";
	// the token is not to stay in the address bar, the history or a bookmark
	window.history.replaceState(null, "", "/verify");
	const form = $("password-form");
	const password = form.elements.password;
	password.minLength = config.password_min_length;
	try {
		const pending = await api("/auth/email/verify", { method: "POST", body: { token } });
		$("verify-email").textContent = pending.email;
		form.elements.email.value = pending.email;
		form.hidden = false;
		password.focus();
	} catch (e) {
		showError(e.message);
		$("verify-again").hidden = false;
		return;
	}
	$("toggle-password").onclick = () => {
		const hidden = password.type === "password";
		password.type = hidden ? "text" : "password";
		$("toggle-password").textContent = hidden ? "Hide" : "Show";
	};
	form.onsubmit = async (event) => {
		event.preventDefault();
		showError("");
		if (password.value.length < config.password_min_length) {
			showError(`Use at least ${config.password_min_length} characters for the password.`);
			password.focus();
			return;
		}
		$("password-submit").disabled = true;
		try {
			const result = await api("/auth/email/password", { method: "POST", body: { token, password: password.value } });
			window.location.href = result.next;
		} catch (e) {
			showError(e.message);
			$("password-submit").disabled = false;
		}
	};
}

// ─── machines page ──────────────────────────────────────────────────────────

// The admin's Students table: every assign dropdown offers the machines'
// owner labels (data-owners, comma separated; labels cannot hold commas)
// with the account's current assignment selected.
function fillAssignments() {
	const table = document.querySelector(".students[data-owners]");
	if (!table) {
		return;
	}
	const owners = table.dataset.owners ? table.dataset.owners.split(",") : [];
	for (const select of table.querySelectorAll("select[data-selected]")) {
		for (const owner of owners) {
			const option = document.createElement("option");
			option.value = owner;
			option.textContent = owner;
			option.selected = owner === select.dataset.selected;
			select.appendChild(option);
		}
	}
}

// A running machine's turn-off time arrives in UTC (the server cannot know
// the viewer's time zone); show it in the browser's local time instead.
function localizeTimes() {
	for (const time of document.querySelectorAll("time[datetime]")) {
		time.textContent = new Date(time.dateTime).toLocaleTimeString([], {
			hour: "2-digit",
			minute: "2-digit",
		});
	}
}

// A Delete form (data-delete holds the machine name) goes out only after
// the delete dialog: the name typed in unlocks its button, and only that
// button (or Enter on the matching name) submits the form. Cancel, Escape
// and the backdrop all leave the machine alone.
function confirmDeletes() {
	const dialog = $("delete-dialog");
	if (!dialog) {
		return;
	}
	const input = $("delete-input");
	const confirm = $("delete-confirm");
	let pending = null;
	const matches = () => pending !== null && input.value.trim() === pending.dataset.delete;
	const submit = () => {
		if (!matches()) {
			return;
		}
		const form = pending;
		pending = null;
		dialog.close();
		// submit() skips onsubmit, so this does not open the dialog again
		form.submit();
	};
	input.oninput = () => {
		confirm.disabled = !matches();
	};
	input.onkeydown = (event) => {
		if (event.key === "Enter") {
			event.preventDefault();
			submit();
		}
	};
	confirm.onclick = submit;
	$("delete-cancel").onclick = () => dialog.close();
	dialog.onclick = (event) => {
		if (event.target === dialog) {
			dialog.close();
		}
	};
	dialog.onclose = () => {
		pending = null;
	};
	const arm = (form) => {
		form.onsubmit = (event) => {
			event.preventDefault();
			pending = form;
			for (const name of dialog.querySelectorAll(".delete-name")) {
				name.textContent = form.dataset.delete;
			}
			input.value = "";
			confirm.disabled = true;
			dialog.showModal();
			input.focus();
		};
	};
	for (const form of document.querySelectorAll("form[data-delete]")) {
		arm(form);
	}
}

// An Erase button of the admin's Students table (data-erase holds the
// student's email, data-machines the machines that are theirs alone) opens
// the erase dialog, whose form does the erasing. The machines go along only
// when the admin ticks the box, which is offered only when there are any.
// A Remove button (data-roster, on an address that has not signed up) opens
// the same dialog to take the address off the roster instead.
function confirmErases() {
	const dialog = $("erase-dialog");
	if (!dialog) {
		return;
	}
	for (const button of document.querySelectorAll("button[data-erase]")) {
		button.onclick = () => {
			const roster = "roster" in button.dataset;
			dialog.querySelector("form").action = roster ? "/admin/unlist" : "/admin/erase";
			$("erase-verb").textContent = roster ? "Remove" : "Erase";
			$("erase-confirm").textContent = roster ? "Remove from roster" : "Erase student";
			$("erase-account-text").hidden = roster;
			$("erase-roster-text").hidden = !roster;
			$("erase-email").value = button.dataset.erase;
			$("erase-name").textContent = button.dataset.erase;
			$("erase-machines").checked = false;
			$("erase-machines-row").hidden = roster || !button.dataset.machines;
			$("erase-machines-names").textContent = button.dataset.machines || "";
			dialog.showModal();
			$("erase-cancel").focus();
		};
	}
	$("erase-cancel").onclick = () => dialog.close();
	dialog.onclick = (event) => {
		if (event.target === dialog) {
			dialog.close();
		}
	};
}

// The Settings button in the top bar opens the settings dialog with the
// name field ready to edit; Cancel, Escape and the backdrop close it
// unsaved (the field goes back to the saved name), Save posts the form.
function setupSettings() {
	const button = $("settings-button");
	const dialog = $("settings-dialog");
	if (!button || !dialog) {
		return;
	}
	const name = $("settings-name");
	button.onclick = () => {
		name.value = name.defaultValue;
		dialog.showModal();
		name.select();
	};
	$("settings-cancel").onclick = () => dialog.close();
	dialog.onclick = (event) => {
		if (event.target === dialog) {
			dialog.close();
		}
	};
}

// The account panel under the top bar's name: opens and closes on the
// name, closes on a click elsewhere or Escape.
function setupAccountMenu() {
	const button = $("account-button");
	const menu = $("account-menu");
	if (!button || !menu) {
		return;
	}
	const show = (open) => {
		menu.hidden = !open;
		button.setAttribute("aria-expanded", String(open));
	};
	button.onclick = () => show(menu.hidden);
	document.addEventListener("click", (event) => {
		if (!menu.hidden && !event.target.closest(".account")) {
			show(false);
		}
	});
	document.addEventListener("keydown", (event) => {
		if (event.key === "Escape" && !menu.hidden) {
			show(false);
			button.focus();
		}
	});
}

function machinesPage() {
	setupAccountMenu();
	setupSettings();
	fillAssignments();
	localizeTimes();
	confirmDeletes();
	confirmErases();
	for (const button of document.querySelectorAll("button[data-copy]")) {
		button.onclick = async () => {
			await navigator.clipboard.writeText(button.dataset.copy);
			const original = button.textContent;
			button.textContent = "Copied!";
			setTimeout(() => {
				button.textContent = original;
			}, 2000);
		};
	}
}

document.addEventListener("DOMContentLoaded", () => {
	showYear();
	const page = document.body.dataset.page;
	if (page === "index") {
		indexPage().catch((e) => showError(e.message));
	} else if (page === "verify") {
		verifyPage().catch((e) => showError(e.message));
	} else if (page === "about") {
		aboutPage().catch((e) => showError(e.message));
	} else if (page === "machines") {
		machinesPage();
	}
});
